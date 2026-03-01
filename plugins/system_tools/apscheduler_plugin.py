"""APScheduler-backed plugin for scheduling allowlisted workflow executions."""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from executor.engine import JSONExecutor
from executor.permissions import validate_request


class APSchedulerPlugin:
    """Schedule and execute allowlisted workflows in-process."""

    _shared_lock = threading.RLock()
    _shared_scheduler: BackgroundScheduler | None = None
    _shared_timezone: str | None = None
    _shared_last_run_results: dict[str, dict[str, Any]] = {}

    def __init__(self, timezone: str = "UTC", auto_start: bool = True) -> None:
        if not isinstance(timezone, str) or not timezone.strip():
            raise ValueError("timezone must be a non-empty string")
        if not isinstance(auto_start, bool):
            raise ValueError("auto_start must be a boolean")

        try:
            tz = ZoneInfo(timezone.strip())
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone, e.g. UTC") from exc

        requested_timezone = timezone.strip()
        with APSchedulerPlugin._shared_lock:
            if APSchedulerPlugin._shared_scheduler is None:
                APSchedulerPlugin._shared_scheduler = BackgroundScheduler(timezone=tz)
                APSchedulerPlugin._shared_timezone = requested_timezone
            elif APSchedulerPlugin._shared_timezone != requested_timezone:
                raise ValueError(
                    "APSchedulerPlugin already initialized with a different timezone. "
                    f"Existing timezone={APSchedulerPlugin._shared_timezone}"
                )

        self.timezone = requested_timezone
        self._scheduler = APSchedulerPlugin._shared_scheduler
        self._lock = APSchedulerPlugin._shared_lock
        self._workflow_ref_pattern = re.compile(r"^\$\{steps\.([^\.]+)\.result(?:\.(.+))?\}$")
        self._last_run_results = APSchedulerPlugin._shared_last_run_results

        if auto_start:
            self.start_scheduler()

    def _ensure_started(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def _validate_execution_fields(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str, str, dict[str, Any], list[Any]]:
        required_fields = ["module", "class", "method"]
        missing_fields = [field for field in required_fields if field not in payload]
        if missing_fields:
            raise ValueError(f"Missing required field(s): {', '.join(missing_fields)}")

        module_name = payload.get("module")
        class_name = payload.get("class")
        method_name = payload.get("method")
        constructor_args = payload.get("constructor_args", {})
        args = payload.get("args", [])

        if not isinstance(module_name, str) or not module_name:
            raise ValueError("module must be a non-empty string")
        if not isinstance(class_name, str) or not class_name:
            raise ValueError("class must be a non-empty string")
        if not isinstance(method_name, str) or not method_name:
            raise ValueError("method must be a non-empty string")
        if not isinstance(constructor_args, dict):
            raise ValueError("constructor_args must be an object")
        if not isinstance(args, list):
            raise ValueError("args must be an array")

        return module_name, class_name, method_name, constructor_args, args

    def _resolve_result_path(self, value: Any, path: str) -> Any:
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise ValueError(f"Reference path '{path}' was not found in step result")
            current = current[part]
        return current

    def _resolve_references(self, value: Any, step_results: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: self._resolve_references(item, step_results) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_references(item, step_results) for item in value]
        if isinstance(value, str):
            match = self._workflow_ref_pattern.fullmatch(value.strip())
            if match is None:
                return value

            step_id = match.group(1)
            result_path = match.group(2)
            if step_id not in step_results:
                raise ValueError(f"Referenced step '{step_id}' has no available result")

            resolved = step_results[step_id]
            if result_path:
                return self._resolve_result_path(resolved, result_path)
            return resolved

        return value

    def _validate_workflow_payload(self, workflow_payload: dict[str, Any]) -> None:
        steps = workflow_payload.get("steps")
        stop_on_error = workflow_payload.get("stop_on_error", True)
        if not isinstance(steps, list) or not steps:
            raise ValueError("workflow.steps must be a non-empty array")
        if not isinstance(stop_on_error, bool):
            raise ValueError("workflow.stop_on_error must be a boolean")

        seen_ids: set[str] = set()
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Step {index} must be an object")

            step_id = step.get("id", str(index))
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError(f"Step {index} id must be a non-empty string")
            step_id = step_id.strip()
            if step_id in seen_ids:
                raise ValueError(f"Duplicate step id '{step_id}'")
            seen_ids.add(step_id)

            step_on_error = step.get("on_error")
            if step_on_error is not None and step_on_error not in {"stop", "continue"}:
                raise ValueError(f"Step '{step_id}' on_error must be 'stop' or 'continue'")

            module_name, class_name, method_name, _constructor_args, _args = self._validate_execution_fields(step)
            validate_request(module_name, class_name, method_name)

    def _execute_workflow_payload(self, workflow_payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_workflow_payload(workflow_payload)

        steps = workflow_payload.get("steps", [])
        stop_on_error = workflow_payload.get("stop_on_error", True)

        executor = JSONExecutor()
        step_results: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        has_errors = False

        for index, step in enumerate(steps, start=1):
            step_id = str(step.get("id", str(index))).strip()
            step_on_error = step.get("on_error", "stop" if stop_on_error else "continue")
            module_name, class_name, method_name, constructor_args, args = self._validate_execution_fields(step)
            constructor_args = self._resolve_references(constructor_args, step_results)
            args = self._resolve_references(args, step_results)

            try:
                validate_request(module_name, class_name, method_name)
                executor.instantiate(module_name, class_name, constructor_args)
                result = executor.call_method(module_name, method_name, args)
                step_results[step_id] = result
                results.append({"id": step_id, "status": "success", "result": result})
            except (ValueError, ImportError, AttributeError, TypeError) as exc:
                has_errors = True
                message = str(exc) if str(exc) else "Invalid execution request"
                results.append({"id": step_id, "status": "error", "message": message})
                if step_on_error == "stop":
                    return {
                        "status": "error",
                        "message": f"Workflow failed at step '{step_id}'",
                        "failed_step": step_id,
                        "results": results,
                    }

        return {"status": "success", "has_errors": has_errors, "results": results}

    def _record_last_run(self, job_id: str, result: dict[str, Any]) -> None:
        self._last_run_results[job_id] = {
            "ran_at": datetime.now(UTC).isoformat(),
            "result": result,
        }

    def _execute_workflow_job(self, job_id: str, workflow: dict[str, Any]) -> None:
        try:
            result = self._execute_workflow_payload(workflow)
        except Exception as exc:
            result = {
                "status": "error",
                "message": f"Scheduled workflow execution failed: {exc}",
            }
        with self._lock:
            self._record_last_run(job_id, result)

    def _parse_run_at(self, run_at_iso: str) -> datetime:
        if not isinstance(run_at_iso, str) or not run_at_iso.strip():
            raise ValueError("run_at_iso must be a non-empty ISO datetime string")
        value = run_at_iso.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("run_at_iso must be a valid ISO datetime string") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    def _serialize_job(self, job: Any) -> dict[str, Any]:
        return {
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        }

    def start_scheduler(self) -> dict[str, Any]:
        """Start scheduler if not already running."""
        with self._lock:
            self._ensure_started()
            return {
                "status": "success",
                "running": self._scheduler.running,
                "message": "Scheduler started",
            }

    def stop_scheduler(self, wait: bool = False) -> dict[str, Any]:
        """Stop scheduler if running."""
        if not isinstance(wait, bool):
            raise ValueError("wait must be a boolean")

        with self._lock:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=wait)
            return {
                "status": "success",
                "running": self._scheduler.running,
                "message": "Scheduler stopped",
            }

    def health(self) -> dict[str, Any]:
        """Return scheduler status and counts."""
        with self._lock:
            jobs = self._scheduler.get_jobs()
            return {
                "status": "success",
                "timezone": self.timezone,
                "running": self._scheduler.running,
                "job_count": len(jobs),
                "tracked_run_count": len(self._last_run_results),
            }

    def list_jobs(self) -> dict[str, Any]:
        """List scheduled jobs with latest run snapshots when available."""
        with self._lock:
            jobs = self._scheduler.get_jobs()
            serialized = []
            for job in jobs:
                item = self._serialize_job(job)
                if job.id in self._last_run_results:
                    item["last_run"] = self._last_run_results[job.id]
                serialized.append(item)

            return {
                "status": "success",
                "running": self._scheduler.running,
                "jobs": serialized,
            }

    def remove_job(self, job_id: str) -> dict[str, Any]:
        """Remove a scheduled job by ID."""
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")

        with self._lock:
            self._scheduler.remove_job(job_id.strip())
            return {
                "status": "success",
                "job_id": job_id.strip(),
                "message": "Job removed",
            }

    def get_last_run(self, job_id: str) -> dict[str, Any]:
        """Get the last recorded run result for a job."""
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")

        key = job_id.strip()
        with self._lock:
            if key not in self._last_run_results:
                raise ValueError("No run record found for job_id")
            return {
                "status": "success",
                "job_id": key,
                "last_run": self._last_run_results[key],
            }

    def run_workflow_now(self, workflow: dict[str, Any]) -> dict[str, Any]:
        """Execute an allowlisted workflow immediately."""
        if not isinstance(workflow, dict):
            raise ValueError("workflow must be an object")
        result = self._execute_workflow_payload(workflow)
        return {
            "status": "success",
            "mode": "immediate",
            "workflow_result": result,
        }

    def add_interval_workflow_job(
        self,
        job_id: str | dict[str, Any],
        workflow: dict[str, Any] | None = None,
        seconds: int | float = 60,
        replace_existing: bool = True,
    ) -> dict[str, Any]:
        """Schedule workflow execution on an interval trigger."""
        if isinstance(job_id, dict):
            options = job_id
            job_id = options.get("job_id", "")
            workflow = options.get("workflow")
            seconds = options.get("seconds", seconds)
            replace_existing = options.get("replace_existing", replace_existing)

        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        if workflow is None or not isinstance(workflow, dict):
            raise ValueError("workflow must be an object")
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            raise ValueError("seconds must be a positive number")
        if not isinstance(replace_existing, bool):
            raise ValueError("replace_existing must be a boolean")

        self._validate_workflow_payload(workflow)
        with self._lock:
            self._ensure_started()
            trigger = IntervalTrigger(seconds=float(seconds), timezone=self._scheduler.timezone)
            self._scheduler.add_job(
                self._execute_workflow_job,
                trigger=trigger,
                id=job_id.strip(),
                args=[job_id.strip(), workflow],
                replace_existing=replace_existing,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )
            job = self._scheduler.get_job(job_id.strip())
            return {
                "status": "success",
                "job": self._serialize_job(job),
                "message": "Interval workflow job scheduled",
            }

    def add_date_workflow_job(
        self,
        job_id: str | dict[str, Any],
        workflow: dict[str, Any] | None = None,
        run_at_iso: str | None = None,
        replace_existing: bool = True,
    ) -> dict[str, Any]:
        """Schedule one-time workflow execution at an ISO datetime."""
        if isinstance(job_id, dict):
            options = job_id
            job_id = options.get("job_id", "")
            workflow = options.get("workflow")
            run_at_iso = options.get("run_at_iso")
            replace_existing = options.get("replace_existing", replace_existing)

        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        if workflow is None or not isinstance(workflow, dict):
            raise ValueError("workflow must be an object")
        if not isinstance(replace_existing, bool):
            raise ValueError("replace_existing must be a boolean")

        run_at = self._parse_run_at(run_at_iso or "")
        self._validate_workflow_payload(workflow)

        with self._lock:
            self._ensure_started()
            trigger = DateTrigger(run_date=run_at, timezone=self._scheduler.timezone)
            self._scheduler.add_job(
                self._execute_workflow_job,
                trigger=trigger,
                id=job_id.strip(),
                args=[job_id.strip(), workflow],
                replace_existing=replace_existing,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )
            job = self._scheduler.get_job(job_id.strip())
            return {
                "status": "success",
                "job": self._serialize_job(job),
                "message": "Date workflow job scheduled",
            }

    def add_cron_workflow_job(
        self,
        job_id: str | dict[str, Any],
        workflow: dict[str, Any] | None = None,
        minute: str | int = "*",
        hour: str | int = "*",
        day: str | int = "*",
        month: str | int = "*",
        day_of_week: str | int = "*",
        second: str | int = "0",
        replace_existing: bool = True,
    ) -> dict[str, Any]:
        """Schedule workflow execution using a cron trigger."""
        if isinstance(job_id, dict):
            options = job_id
            job_id = options.get("job_id", "")
            workflow = options.get("workflow")
            minute = options.get("minute", minute)
            hour = options.get("hour", hour)
            day = options.get("day", day)
            month = options.get("month", month)
            day_of_week = options.get("day_of_week", day_of_week)
            second = options.get("second", second)
            replace_existing = options.get("replace_existing", replace_existing)

        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        if workflow is None or not isinstance(workflow, dict):
            raise ValueError("workflow must be an object")
        if not isinstance(replace_existing, bool):
            raise ValueError("replace_existing must be a boolean")

        self._validate_workflow_payload(workflow)

        with self._lock:
            self._ensure_started()
            trigger = CronTrigger(
                minute=str(minute),
                hour=str(hour),
                day=str(day),
                month=str(month),
                day_of_week=str(day_of_week),
                second=str(second),
                timezone=self._scheduler.timezone,
            )
            self._scheduler.add_job(
                self._execute_workflow_job,
                trigger=trigger,
                id=job_id.strip(),
                args=[job_id.strip(), workflow],
                replace_existing=replace_existing,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )
            job = self._scheduler.get_job(job_id.strip())
            return {
                "status": "success",
                "job": self._serialize_job(job),
                "message": "Cron workflow job scheduled",
            }
