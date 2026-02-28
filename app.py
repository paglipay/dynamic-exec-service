"""Flask entrypoint for the dynamic execution service."""

from __future__ import annotations

import re
from typing import Any

from flask import Flask, jsonify, request

from executor.engine import JSONExecutor
from executor.permissions import validate_request


app = Flask(__name__)
executor = JSONExecutor()
WORKFLOW_REF_PATTERN = re.compile(r"^\$\{steps\.([^\.]+)\.result(?:\.(.+))?\}$")


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