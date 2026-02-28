"""OpenAI SDK integration plugin for text generation."""

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI


class OpenAISDKPlugin:
    """Generate text using the official OpenAI Python SDK."""

    _conversation_store: dict[str, list[dict[str, str]]] = {}

    def __init__(self, api_key: str | None = None) -> None:
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not isinstance(resolved_api_key, str) or not resolved_api_key.strip():
            raise ValueError("api_key must be provided (or set OPENAI_API_KEY)")

        self.client = OpenAI(api_key=resolved_api_key.strip())

    def _extract_output_text(self, response: Any) -> str:
        """Extract normalized text output from an SDK response."""
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        output_text = ""
        output_items = getattr(response, "output", [])
        if isinstance(output_items, list):
            for item in output_items:
                content_items = getattr(item, "content", [])
                if not isinstance(content_items, list):
                    continue
                for content_item in content_items:
                    text = getattr(content_item, "text", None)
                    if isinstance(text, str):
                        output_text += text

        return output_text.strip()

    def generate_text(self, prompt: str, model: str = "gpt-4.1-mini") -> dict[str, Any]:
        """Generate text from a prompt and return normalized response content."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")

        try:
            response = self.client.responses.create(
                model=model.strip(),
                input=prompt.strip(),
            )
        except Exception as exc:
            raise ValueError(f"OpenAI SDK request failed: {exc}") from exc

        output_text = self._extract_output_text(response)

        if not output_text:
            raise ValueError("OpenAI SDK response did not include text output")

        return {
            "status": "success",
            "model": getattr(response, "model", model.strip()),
            "response_id": getattr(response, "id", None),
            "text": output_text,
        }

    def generate_text_with_history(
        self,
        conversation_id: str,
        prompt: str,
        model: str = "gpt-4.1-mini",
    ) -> dict[str, Any]:
        """Generate text while preserving per-conversation history in memory."""
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")

        key = conversation_id.strip()
        history = self._conversation_store.setdefault(key, [])
        request_messages = [
            *history,
            {"role": "user", "content": prompt.strip()},
        ]

        try:
            response = self.client.responses.create(
                model=model.strip(),
                input=request_messages,
            )
        except Exception as exc:
            raise ValueError(f"OpenAI SDK request failed: {exc}") from exc

        output_text = self._extract_output_text(response)
        if not output_text:
            raise ValueError("OpenAI SDK response did not include text output")

        history.append({"role": "user", "content": prompt.strip()})
        history.append({"role": "assistant", "content": output_text})

        return {
            "status": "success",
            "conversation_id": key,
            "model": getattr(response, "model", model.strip()),
            "response_id": getattr(response, "id", None),
            "text": output_text,
            "history_messages": len(history),
        }
