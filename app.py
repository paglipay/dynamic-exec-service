"""Flask entrypoint for the dynamic execution service."""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from slackeventsapi import SlackEventAdapter

from executor.engine import JSONExecutor
from executor.permissions import validate_request


app = Flask(__name__)
try:
    signing_secret = os.environ["SIGNING_SECRET"]
except KeyError:
    env_path = Path(".") / ".env"
    load_dotenv(dotenv_path=env_path)
    signing_secret = os.getenv("SIGNING_SECRET")

slack_event_adapter: SlackEventAdapter | None = None
if signing_secret:
    slack_event_adapter = SlackEventAdapter(
        signing_secret, "/slack/events", app
    )
else:
    app.logger.warning("SIGNING_SECRET is not set; Slack event subscriptions are disabled")
executor = JSONExecutor()
WORKFLOW_REF_PATTERN = re.compile(r"^\$\{steps\.([^\.]+)\.result(?:\.(.+))?\}$")
SLACK_EVENT_TTL_SECONDS = 300
_processed_slack_events: dict[str, float] = {}
_processed_slack_events_lock = threading.Lock()


def _is_duplicate_slack_event(event_data: dict[str, Any], event: dict[str, Any]) -> bool:
    """Return True when a Slack event appears to be a duplicate delivery."""
    event_id = event_data.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        event_id = "|".join(
            [
                str(event.get("channel", "")),
                str(event.get("user", "")),
                str(event.get("ts", "")),
                str(event.get("text", "")),
            ]
        )

    now = time.time()
    with _processed_slack_events_lock:
        expired = [
            key for key, seen_at in _processed_slack_events.items()
            if (now - seen_at) > SLACK_EVENT_TTL_SECONDS
        ]
        for key in expired:
            del _processed_slack_events[key]

        if event_id in _processed_slack_events:
            return True

        _processed_slack_events[event_id] = now
        return False


if slack_event_adapter is not None:
    @slack_event_adapter.on("message")
    def handle_slack_message(event_data: dict[str, Any]) -> None:
        """Handle Slack messages by generating and posting an AI reply."""
        event = event_data.get("event", {}) if isinstance(event_data, dict) else {}
        if not isinstance(event, dict):
            return

        subtype = event.get("subtype")
        if subtype is not None:
            return

        if event.get("bot_id"):
            return

        channel = event.get("channel")
        text = event.get("text", "")
        if not isinstance(channel, str) or not channel:
            return
        if not isinstance(text, str) or not text.strip():
            return

        if _is_duplicate_slack_event(event_data, event):
            app.logger.info("Ignoring duplicate Slack event delivery")
            return

        app.logger.info(
            "Slack message received: channel=%s user=%s text=%s",
            channel,
            event.get("user"),
            text,
        )

        forced_conversation_id = os.getenv("SLACK_CONVERSATION_ID", "").strip()
        if forced_conversation_id:
            conversation_id = forced_conversation_id
        else:
            conversation_key = event.get("thread_ts") or channel
            conversation_id = f"slack:{conversation_key}"
        model_name = os.getenv("SLACK_OPENAI_MODEL", "gpt-4.1-mini")
        max_tool_rounds_raw = os.getenv("SLACK_OPENAI_MAX_TOOL_ROUNDS", "5").strip()
        try:
            max_tool_rounds = int(max_tool_rounds_raw)
        except ValueError:
            max_tool_rounds = 5
        if max_tool_rounds <= 0:
            max_tool_rounds = 5

        try:
            validate_request(
                "plugins.integrations.openai_plugin",
                "OpenAIFunctionCallingPlugin",
                "generate_with_function_calls_and_history",
            )
            executor.instantiate(
                "plugins.integrations.openai_plugin",
                "OpenAIFunctionCallingPlugin",
                {},
            )
            ai_result = executor.call_method(
                "plugins.integrations.openai_plugin",
                "generate_with_function_calls_and_history",
                [conversation_id, text.strip(), model_name, max_tool_rounds],
            )
            if isinstance(ai_result, dict):
                reply_text = str(ai_result.get("text", "")).strip()
            else:
                reply_text = str(ai_result).strip()
        except Exception:
            app.logger.exception("Failed to generate Slack AI reply")
            reply_text = "Sorry, I couldn't generate a reply right now."

        if not reply_text:
            return

        slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
        if not isinstance(slack_bot_token, str) or not slack_bot_token.strip():
            app.logger.warning("SLACK_BOT_TOKEN is not set; cannot post Slack reply")
            return

        try:
            validate_request(
                "plugins.integrations.slack_plugin",
                "SlackPlugin",
                "post_message",
            )
            executor.instantiate(
                "plugins.integrations.slack_plugin",
                "SlackPlugin",
                {"bot_token": slack_bot_token.strip(), "default_channel": "#general"},
            )
            executor.call_method(
                "plugins.integrations.slack_plugin",
                "post_message",
                [channel, reply_text],
            )
        except Exception:
            app.logger.exception("Failed to post Slack AI reply")


def _error_response(message: str, status_code: int = 400):
    """Standardized API error response."""
    return jsonify({"status": "error", "message": message}), status_code


def _validate_execution_fields(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any], list[Any]]:
    """Validate shared execute/workflow step fields and return normalized values."""
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


def _resolve_result_path(value: Any, path: str) -> Any:
    """Resolve dotted path access for dict results."""
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Reference path '{path}' was not found in step result")
        current = current[part]
    return current


def _resolve_references(value: Any, step_results: dict[str, Any]) -> Any:
    """Resolve ${steps.<id>.result[.path]} references in workflow step inputs."""
    if isinstance(value, dict):
        return {key: _resolve_references(item, step_results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_references(item, step_results) for item in value]
    if isinstance(value, str):
        match = WORKFLOW_REF_PATTERN.fullmatch(value.strip())
        if match is None:
            return value

        step_id = match.group(1)
        result_path = match.group(2)
        if step_id not in step_results:
            raise ValueError(f"Referenced step '{step_id}' has no available result")

        resolved = step_results[step_id]
        if result_path:
            return _resolve_result_path(resolved, result_path)
        return resolved

    return value


@app.post("/execute")
def execute() -> Any:
    """Validate and execute a JSON-defined plugin method call."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response("Request body must be valid JSON")

    try:
        module_name, class_name, method_name, constructor_args, args = _validate_execution_fields(
            payload
        )
        validate_request(module_name, class_name, method_name)
        executor.instantiate(module_name, class_name, constructor_args)
        result = executor.call_method(module_name, method_name, args)
        return jsonify({"status": "success", "result": result})
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)
    except (ImportError, AttributeError, TypeError):
        return _error_response("Invalid execution request", status_code=400)
    except Exception:
        app.logger.exception("Unhandled execution error")
        return _error_response("Internal server error", status_code=500)


@app.post("/workflow")
def workflow() -> Any:
    """Execute a chain of allowlisted plugin calls in sequence."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response("Request body must be valid JSON")

    steps = payload.get("steps")
    stop_on_error = payload.get("stop_on_error", True)
    if not isinstance(steps, list) or not steps:
        return _error_response("steps must be a non-empty array")
    if not isinstance(stop_on_error, bool):
        return _error_response("stop_on_error must be a boolean")

    step_results: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    has_errors = False

    try:
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Step {index} must be an object")

            step_id = step.get("id", str(index))
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError(f"Step {index} id must be a non-empty string")
            step_id = step_id.strip()

            if step_id in step_results:
                raise ValueError(f"Duplicate step id '{step_id}'")

            step_on_error = step.get("on_error", "stop" if stop_on_error else "continue")
            if step_on_error not in {"stop", "continue"}:
                raise ValueError(f"Step '{step_id}' on_error must be 'stop' or 'continue'")

            module_name, class_name, method_name, constructor_args, args = _validate_execution_fields(step)
            constructor_args = _resolve_references(constructor_args, step_results)
            args = _resolve_references(args, step_results)

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
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": f"Workflow failed at step '{step_id}'",
                                "failed_step": step_id,
                                "results": results,
                            }
                        ),
                        400,
                    )

        return jsonify({"status": "success", "has_errors": has_errors, "results": results})
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)
    except Exception:
        app.logger.exception("Unhandled workflow execution error")
        return _error_response("Internal server error", status_code=500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=True)