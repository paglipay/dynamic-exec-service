"""Local HTTP helper plugin for calling this service's execute endpoint."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request


class LocalHTTPModule:
    """Plugin that posts JSON payloads to the local /execute endpoint only."""

    def __init__(self, execute_url: str = "http://localhost:5000/execute") -> None:
        if not isinstance(execute_url, str) or not execute_url:
            raise ValueError("execute_url must be a non-empty string")
        if execute_url != "http://localhost:5000/execute":
            raise ValueError("execute_url must be http://localhost:5000/execute")
        self.execute_url = execute_url

    def post_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a JSON payload to the local /execute endpoint and return its JSON response."""
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.execute_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=15) as response:
                body = response.read().decode("utf-8")
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    return parsed
                return {"status": "error", "message": "Non-object JSON response"}
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
            return {"status": "error", "message": body or f"HTTP {exc.code}"}
        except error.URLError as exc:
            raise ValueError(f"Failed to reach execute endpoint: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("Execute endpoint returned invalid JSON") from exc
