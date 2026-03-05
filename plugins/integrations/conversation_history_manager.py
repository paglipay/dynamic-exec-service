"""Utilities for bounding and compacting conversation history."""

from __future__ import annotations

import os
from typing import Any


class ConversationHistoryManager:
    """Compact conversation history with a deterministic summary and tail window."""

    _summary_prefix = "Conversation summary (older context):"

    def __init__(
        self,
        max_messages: int = 60,
        keep_last_messages: int = 24,
        max_estimated_tokens: int = 12000,
        summary_max_chars: int = 1800,
    ) -> None:
        if not isinstance(max_messages, int) or max_messages <= 0:
            raise ValueError("max_messages must be an integer > 0")
        if not isinstance(keep_last_messages, int) or keep_last_messages <= 0:
            raise ValueError("keep_last_messages must be an integer > 0")
        if keep_last_messages > max_messages:
            raise ValueError("keep_last_messages must be <= max_messages")
        if not isinstance(max_estimated_tokens, int) or max_estimated_tokens <= 0:
            raise ValueError("max_estimated_tokens must be an integer > 0")
        if not isinstance(summary_max_chars, int) or summary_max_chars < 200:
            raise ValueError("summary_max_chars must be an integer >= 200")

        self.max_messages = max_messages
        self.keep_last_messages = keep_last_messages
        self.max_estimated_tokens = max_estimated_tokens
        self.summary_max_chars = summary_max_chars

    @classmethod
    def from_env(cls) -> "ConversationHistoryManager":
        """Build settings from environment variables using safe defaults."""

        def _int_env(name: str, default: int) -> int:
            raw_value = os.getenv(name, str(default)).strip()
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                return default
            return value if value > 0 else default

        max_messages = _int_env("OPENAI_HISTORY_MAX_MESSAGES", 60)
        keep_last_messages = _int_env("OPENAI_HISTORY_KEEP_LAST_MESSAGES", 24)
        max_estimated_tokens = _int_env("OPENAI_HISTORY_MAX_ESTIMATED_TOKENS", 12000)
        summary_max_chars = _int_env("OPENAI_HISTORY_SUMMARY_MAX_CHARS", 1800)

        if keep_last_messages > max_messages:
            keep_last_messages = max_messages

        if summary_max_chars < 200:
            summary_max_chars = 200

        return cls(
            max_messages=max_messages,
            keep_last_messages=keep_last_messages,
            max_estimated_tokens=max_estimated_tokens,
            summary_max_chars=summary_max_chars,
        )

    def estimated_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate token usage without model-specific tokenizers."""
        if not isinstance(messages, list):
            return 0

        total_chars = 0
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if isinstance(role, str):
                total_chars += len(role)

            content = message.get("content")
            total_chars += len(self._content_to_text(content))

            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if isinstance(call, dict):
                        total_chars += len(str(call.get("id", "")))
                        fn = call.get("function")
                        if isinstance(fn, dict):
                            total_chars += len(str(fn.get("name", "")))
                            total_chars += len(str(fn.get("arguments", "")))

        # A coarse but stable heuristic commonly used in server-side budgeting.
        return max(1, total_chars // 4)

    def compact(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Compact history when message or token budget is exceeded."""
        valid_messages = [m for m in messages if isinstance(m, dict)]
        before_count = len(valid_messages)
        before_tokens = self.estimated_tokens(valid_messages)

        should_compact = before_count > self.max_messages or before_tokens > self.max_estimated_tokens
        if not should_compact:
            return valid_messages, {
                "compacted": False,
                "before_messages": before_count,
                "after_messages": before_count,
                "before_estimated_tokens": before_tokens,
                "after_estimated_tokens": before_tokens,
            }

        system_messages = [m for m in valid_messages if m.get("role") == "system"]
        non_system = [m for m in valid_messages if m.get("role") != "system"]

        if len(non_system) <= self.keep_last_messages:
            trimmed = non_system
            dropped = []
        else:
            split = len(non_system) - self.keep_last_messages
            dropped = non_system[:split]
            trimmed = non_system[split:]

        base_system = self._pick_base_system_prompt(system_messages)
        existing_summary = self._extract_existing_summary(system_messages)
        summary = self._build_summary(dropped, existing_summary)

        compacted: list[dict[str, Any]] = []
        if base_system is not None:
            compacted.append(base_system)
        if summary:
            compacted.append({"role": "system", "content": f"{self._summary_prefix}\n{summary}"})
        compacted.extend(trimmed)

        after_tokens = self.estimated_tokens(compacted)
        return compacted, {
            "compacted": True,
            "before_messages": before_count,
            "after_messages": len(compacted),
            "before_estimated_tokens": before_tokens,
            "after_estimated_tokens": after_tokens,
        }

    def _pick_base_system_prompt(self, system_messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        for message in system_messages:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if content.startswith(self._summary_prefix):
                continue
            return {"role": "system", "content": content}
        return None

    def _extract_existing_summary(self, system_messages: list[dict[str, Any]]) -> str:
        for message in system_messages:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if content.startswith(self._summary_prefix):
                return content.removeprefix(self._summary_prefix).strip()
        return ""

    def _build_summary(self, dropped_messages: list[dict[str, Any]], existing_summary: str) -> str:
        lines: list[str] = []
        if existing_summary:
            lines.append(existing_summary)

        for message in dropped_messages:
            role = message.get("role")
            role_label = role if isinstance(role, str) else "unknown"
            text = self._content_to_text(message.get("content")).strip()
            if not text:
                tool_call_id = message.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id.strip():
                    text = f"tool response id={tool_call_id.strip()}"
            if not text:
                continue
            text = " ".join(text.split())
            if len(text) > 180:
                text = text[:177].rstrip() + "..."
            lines.append(f"- {role_label}: {text}")

        if not lines:
            return ""

        summary = "\n".join(lines)
        if len(summary) > self.summary_max_chars:
            summary = summary[: self.summary_max_chars - 3].rstrip() + "..."
        return summary

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "text":
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                    elif item_type == "image_url":
                        image_url = item.get("image_url")
                        if isinstance(image_url, dict):
                            url = image_url.get("url")
                            if isinstance(url, str):
                                parts.append(f"[image:{url[:120]}]")
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)

        if isinstance(content, dict):
            try:
                return str(content)
            except Exception:
                return ""

        return ""
