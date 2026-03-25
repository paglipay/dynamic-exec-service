"""Streamlit plugin for creating and running local Streamlit apps."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


class StreamlitPlugin:
    """Create, run, inspect, and stop Streamlit apps from allowlisted JSON calls."""

    def __init__(
        self,
        base_dir: str = ".",
        host: str = "127.0.0.1",
        port: int = 8501,
        allow_outside_base_dir: bool = False,
    ) -> None:
        if not isinstance(base_dir, str) or not base_dir.strip():
            raise ValueError("base_dir must be a non-empty string")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("host must be a non-empty string")
        if not isinstance(port, int) or port <= 0 or port > 65535:
            raise ValueError("port must be an integer between 1 and 65535")
        if not isinstance(allow_outside_base_dir, bool):
            raise ValueError("allow_outside_base_dir must be a boolean")

        self.base_dir = Path(base_dir).resolve()
        if not self.base_dir.exists() or not self.base_dir.is_dir():
            raise ValueError("base_dir must point to an existing directory")

        self.host = host.strip()
        self.port = port
        self.allow_outside_base_dir = allow_outside_base_dir
        self._process: subprocess.Popen[str] | None = None
        self._active_script: str | None = None

    def _resolve_target_path(self, target_path: str, must_exist: bool = True) -> Path:
        if not isinstance(target_path, str) or not target_path.strip():
            raise ValueError("target_path must be a non-empty string")

        candidate = Path(target_path.strip())
        resolved = candidate.resolve() if candidate.is_absolute() else (self.base_dir / candidate).resolve()

        if must_exist and (not resolved.exists() or not resolved.is_file()):
            raise ValueError("target_path does not exist")
        if resolved.suffix.lower() != ".py":
            raise ValueError("Only .py files are allowed")

        if not self.allow_outside_base_dir:
            try:
                resolved.relative_to(self.base_dir)
            except ValueError as exc:
                raise ValueError("target_path must be inside base_dir") from exc

        return resolved

    def _is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def create_app_file(
        self,
        file_path: str | dict[str, Any],
        title: str = "Dynamic Streamlit App",
        template: str = "basic",
        description: str | None = None,
        app_content: str | None = None,
        overwrite_existing: bool = False,
        restart_on_overwrite: bool = True,
    ) -> dict[str, Any]:
        """Create a Streamlit app file under base_dir using a supported template."""
        auto_start = False
        start_port: int | None = None
        start_host: str | None = None
        start_headless = True

        if isinstance(file_path, dict):
            payload = file_path
            file_path = payload.get("file_path", "")
            title = payload.get("title", title)
            template = payload.get("template", template)
            description = payload.get("description", description)
            app_content = payload.get("app_content", app_content)
            overwrite_existing = payload.get("overwrite_existing", overwrite_existing)
            restart_on_overwrite = payload.get("restart_on_overwrite", restart_on_overwrite)
            auto_start = payload.get("auto_start", False)
            start_port = payload.get("start_port")
            start_host = payload.get("start_host")
            if "start_headless" in payload:
                start_headless = payload.get("start_headless")

        resolved = self._resolve_target_path(file_path, must_exist=False)
        existed_before_write = resolved.exists()
        if existed_before_write and not overwrite_existing:
            raise ValueError("file_path already exists")

        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        if not isinstance(template, str) or not template.strip():
            raise ValueError("template must be a non-empty string")
        if description is not None and (not isinstance(description, str) or not description.strip()):
            raise ValueError("description must be a non-empty string when provided")
        if app_content is not None and (not isinstance(app_content, str) or not app_content.strip()):
            raise ValueError("app_content must be a non-empty string when provided")
        if not isinstance(overwrite_existing, bool):
            raise ValueError("overwrite_existing must be a boolean")
        if not isinstance(restart_on_overwrite, bool):
            raise ValueError("restart_on_overwrite must be a boolean")
        if not isinstance(auto_start, bool):
            raise ValueError("auto_start must be a boolean")
        if start_port is not None and (not isinstance(start_port, int) or start_port <= 0 or start_port > 65535):
            raise ValueError("start_port must be an integer between 1 and 65535")
        if start_host is not None and (not isinstance(start_host, str) or not start_host.strip()):
            raise ValueError("start_host must be a non-empty string when provided")
        if not isinstance(start_headless, bool):
            raise ValueError("start_headless must be a boolean")

        normalized_template = template.strip().lower()
        allowed_templates = {"basic", "simple_submit_form"}
        if normalized_template not in allowed_templates:
            raise ValueError("template must be one of: basic, simple_submit_form")

        if app_content is not None:
            generated_app_content = app_content.strip() + "\n"
            normalized_description = (description.strip() if isinstance(description, str) else "Custom app content from payload")
        else:
            if description is None:
                if normalized_template == "simple_submit_form":
                    normalized_description = "Submit your details below."
                else:
                    normalized_description = "This app was created by StreamlitPlugin."
            else:
                normalized_description = description.strip()

            if normalized_template == "simple_submit_form":
                generated_app_content = (
                    "import streamlit as st\n\n"
                    f"st.set_page_config(page_title={title.strip()!r})\n"
                    f"st.title({title.strip()!r})\n"
                    f"st.write({normalized_description!r})\n\n"
                    "with st.form(key=\"simple_submit_form\"):\n"
                    "    name = st.text_input(\"Name\")\n"
                    "    email = st.text_input(\"Email\")\n"
                    "    message = st.text_area(\"Message\")\n"
                    "    submitted = st.form_submit_button(\"Submit\")\n\n"
                    "if submitted:\n"
                    "    st.success(\"Form submitted successfully\")\n"
                    "    st.json({\"name\": name, \"email\": email, \"message\": message})\n"
                )
            else:
                generated_app_content = (
                    "import streamlit as st\n\n"
                    f"st.set_page_config(page_title={title.strip()!r})\n"
                    f"st.title({title.strip()!r})\n"
                    f"st.write({normalized_description!r})\n"
                )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(generated_app_content, encoding="utf-8")

        start_result: dict[str, Any] | None = None
        restarted_app = False
        if (
            existed_before_write
            and overwrite_existing
            and restart_on_overwrite
            and self._is_running()
            and self._active_script == str(resolved)
        ):
            self.stop_app(force=False, timeout_seconds=10)
            start_result = self.start_app(
                script_path=str(resolved),
                port=start_port,
                host=start_host,
                headless=start_headless,
            )
            restarted_app = True

        result: dict[str, Any] = {
            "status": "success",
            "file_path": str(resolved),
            "template": normalized_template,
            "description": normalized_description,
            "used_custom_content": app_content is not None,
            "overwrote_existing": bool(existed_before_write and overwrite_existing),
            "restarted_app": restarted_app,
        }

        if auto_start and start_result is None:
            start_result = self.start_app(
                script_path=str(resolved),
                port=start_port,
                host=start_host,
                headless=start_headless,
            )

        if start_result is not None:
            result["app_start"] = start_result

        return result

    def start_app(
        self,
        script_path: str,
        port: int | None = None,
        host: str | None = None,
        headless: bool = True,
    ) -> dict[str, Any]:
        """Start a Streamlit app process for a Python script."""
        if self._is_running():
            raise ValueError("A Streamlit app is already running")

        resolved_script = self._resolve_target_path(script_path, must_exist=True)

        effective_port = self.port if port is None else port
        if not isinstance(effective_port, int) or effective_port <= 0 or effective_port > 65535:
            raise ValueError("port must be an integer between 1 and 65535")

        effective_host = self.host if host is None else host
        if not isinstance(effective_host, str) or not effective_host.strip():
            raise ValueError("host must be a non-empty string")
        if not isinstance(headless, bool):
            raise ValueError("headless must be a boolean")

        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(resolved_script),
            "--server.address",
            effective_host.strip(),
            "--server.port",
            str(effective_port),
            "--server.headless",
            "true" if headless else "false",
        ]

        try:
            process = subprocess.Popen(
                command,
                cwd=str(resolved_script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            raise ValueError("Failed to start Streamlit process") from exc

        self._process = process
        self._active_script = str(resolved_script)

        return {
            "status": "success",
            "pid": process.pid,
            "script": str(resolved_script),
            "host": effective_host.strip(),
            "port": effective_port,
            "url": f"http://{effective_host.strip()}:{effective_port}",
            "command": command,
        }

    def status(self) -> dict[str, Any]:
        """Return process state for the current Streamlit app."""
        if self._process is None:
            return {
                "status": "success",
                "running": False,
                "pid": None,
                "script": self._active_script,
            }

        running = self._is_running()
        return {
            "status": "success",
            "running": running,
            "pid": self._process.pid,
            "exit_code": None if running else self._process.returncode,
            "script": self._active_script,
        }

    def stop_app(self, force: bool = False, timeout_seconds: int = 10) -> dict[str, Any]:
        """Stop the active Streamlit app process if one is running."""
        if not isinstance(force, bool):
            raise ValueError("force must be a boolean")
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be an integer > 0")

        if self._process is None:
            return {
                "status": "success",
                "stopped": False,
                "message": "No running Streamlit process",
            }

        running_before = self._is_running()
        if not running_before:
            exit_code = self._process.returncode
            self._process = None
            self._active_script = None
            return {
                "status": "success",
                "stopped": False,
                "message": "Process already exited",
                "exit_code": exit_code,
            }

        if force:
            self._process.kill()
        else:
            self._process.terminate()

        try:
            self._process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if not force:
                self._process.kill()
                self._process.wait(timeout=timeout_seconds)
            else:
                raise ValueError("Failed to stop Streamlit process in time") from exc

        exit_code = self._process.returncode
        self._process = None
        self._active_script = None

        return {
            "status": "success",
            "stopped": True,
            "exit_code": exit_code,
        }