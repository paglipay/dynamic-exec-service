"""OpenAI HTTP integration plugin for text generation."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


class OpenAIHTTPPlugin:
    """Generate text using OpenAI's Responses API over raw HTTP."""

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str = "https://api.openai.com/v1/responses",
    ) -> None:
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not isinstance(resolved_api_key, str) or not resolved_api_key.strip():
            raise ValueError("api_key must be provided (or set OPENAI_API_KEY)")
        if api_url != "https://api.openai.com/v1/responses":
            raise ValueError("api_url must be https://api.openai.com/v1/responses")

        self.api_key = resolved_api_key.strip()
        self.api_url = api_url

    def generate_text(self, prompt: str, model: str = "gpt-4.1-mini") -> dict[str, Any]:
        """Generate text from a prompt and return normalized response content."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")

        payload = {
            "model": model.strip(),
            "input": prompt.strip(),
        }
        data = json.dumps(payload).encode("utf-8")

        req = request.Request(
            self.api_url,
            data=data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"OpenAI HTTP error {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise ValueError(f"Failed to reach OpenAI API: {exc.reason}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("OpenAI API returned invalid JSON") from exc

        if not isinstance(parsed, dict):
            raise ValueError("OpenAI API returned unexpected response type")

        output_text = parsed.get("output_text")
        if not isinstance(output_text, str) or not output_text.strip():
            output_text = ""
            output = parsed.get("output")
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content")
                    if not isinstance(content, list):
                        continue
                    for content_item in content:
                        if not isinstance(content_item, dict):
                            continue
                        text = content_item.get("text")
                        if isinstance(text, str):
                            output_text += text
            output_text = output_text.strip()

        if not output_text:
            raise ValueError("OpenAI API response did not include text output")

        return {
            "status": "success",
            "model": parsed.get("model", model.strip()),
            "response_id": parsed.get("id"),
            "text": output_text,
        }
