"""Local HTTP helper plugin for calling this service's execute endpoint."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request
from urllib.parse import ParseResult, urlparse, urlunparse


class LocalHTTPModule:
    """Plugin that posts JSON payloads to the local /execute endpoint only."""

    def __init__(self, execute_url: str = "http://localhost:5000/execute") -> None:
        if not isinstance(execute_url, str) or not execute_url:
            raise ValueError("execute_url must be a non-empty string")

        parsed = urlparse(execute_url.strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("execute_url must use http or https")
        if not parsed.netloc:
            raise ValueError("execute_url must include a host")
        if parsed.params or parsed.query or parsed.fragment:
            raise ValueError("execute_url must not include params, query, or fragment")

        normalized_path = parsed.path.rstrip("/")
        if normalized_path not in {"/execute", "/workflow"}:
            raise ValueError("execute_url path must be /execute or /workflow")

        self._base_url_parts = ParseResult(
            scheme=parsed.scheme,
            netloc=parsed.netloc,
            path="",
            params="",
            query="",
            fragment="",
        )
        self.execute_url = urlunparse(self._base_url_parts._replace(path="/execute"))

    def _target_url_for_payload(self, payload: dict[str, Any]) -> str:
        is_workflow_payload = isinstance(payload.get("steps"), list)
        target_path = "/workflow" if is_workflow_payload else "/execute"
        return urlunparse(self._base_url_parts._replace(path=target_path))

    def post_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON payload to /execute or /workflow and return JSON response."""
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        target_url = self._target_url_for_payload(payload)
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            target_url,
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
