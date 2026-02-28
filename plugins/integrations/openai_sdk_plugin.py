"""OpenAI SDK integration plugin for text generation."""

from __future__ import annotations

import os
import re
from typing import Any

from openai import OpenAI

from config import ALLOWED_MODULES
from plugins.text_file_crud_plugin import TextFileCRUDPlugin


class OpenAISDKPlugin:
    """Generate text using the official OpenAI Python SDK."""

    _conversation_store: dict[str, list[dict[str, str]]] = {}
    _filename_pattern = re.compile(r"([\"'`]*[A-Za-z0-9][A-Za-z0-9 _\-.]*\.(?:txt|md)[\"'`]*)", re.IGNORECASE)
    _default_joke_text = "Why don't scientists trust atoms? Because they make up everything!"

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

    def _build_tools_awareness_prompt(self) -> str:
        """Build a concise system prompt describing allowlisted plugin tools."""
        tool_lines: list[str] = []
        for module_name, module_config in sorted(ALLOWED_MODULES.items()):
            class_name = module_config["class"]
            methods = ", ".join(module_config["methods"])
            tool_lines.append(f"- {module_name}::{class_name} -> [{methods}]")

        tools_text = "\n".join(tool_lines)
        return (
            "You are operating inside the Dynamic Exec Service. "
            "Only reference and suggest tools from this allowlist:\n"
            f"{tools_text}\n\n"
            "Important: to analyze local files (for example generated_data/notes.txt), "
            "the caller must first fetch file content using "
            "plugins.text_file_crud_plugin::TextFileCRUDPlugin.read_text and then pass "
            "that text into this plugin as prompt/context."
        )

    def _is_allowlisted(self, module_name: str, class_name: str, method_name: str) -> bool:
        """Check if a module/class/method combination is allowlisted."""
        module_config = ALLOWED_MODULES.get(module_name)
        if module_config is None:
            return False
        if module_config["class"] != class_name:
            return False
        return method_name in module_config["methods"]

    def _extract_filename_from_message(self, message: str) -> str | None:
        """Extract and sanitize a candidate txt/md filename from free-form text."""
        match = self._filename_pattern.search(message)
        if match is None:
            return None

        candidate = match.group(1).strip()
        candidate = re.sub(r"[\"'`]", "", candidate).strip()
        if not candidate.lower().endswith((".txt", ".md")):
            return None
        if "/" in candidate or "\\" in candidate:
            return None
        return candidate

    def _try_execute_plugin_action(self, message: str) -> str | None:
        """Execute safe plugin actions inferred from user text when confidence is high."""
        normalized = message.lower()
        if "create" not in normalized or "file" not in normalized:
            return None

        filename = self._extract_filename_from_message(message)
        if filename is None:
            return None

        module_name = "plugins.text_file_crud_plugin"
        class_name = "TextFileCRUDPlugin"
        method_name = "create_text"
        if not self._is_allowlisted(module_name, class_name, method_name):
            return "I couldn't create the file because that action is not allowlisted."

        content = self._default_joke_text if "joke" in normalized else "Created by paulbot."
        plugin = TextFileCRUDPlugin(base_dir="generated_data")
        try:
            plugin.create_text(filename, content)
            return f"Created '{filename}' in generated_data. Added: {content}"
        except ValueError as exc:
            return f"I couldn't create '{filename}': {exc}"

    def generate_text(
        self,
        prompt: str,
        model: str = "gpt-4.1-mini",
        include_tools_context: bool = True,
    ) -> dict[str, Any]:
        """Generate text from a prompt and return normalized response content."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(include_tools_context, bool):
            raise ValueError("include_tools_context must be a boolean")

        input_messages: list[dict[str, str]] = [{"role": "user", "content": prompt.strip()}]
        if include_tools_context:
            input_messages.insert(0, {"role": "system", "content": self._build_tools_awareness_prompt()})

        try:
            response = self.client.responses.create(
                model=model.strip(),
                input=input_messages,
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
        include_tools_context: bool = True,
    ) -> dict[str, Any]:
        """Generate text while preserving per-conversation history in memory."""
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(include_tools_context, bool):
            raise ValueError("include_tools_context must be a boolean")

        key = conversation_id.strip()
        history = self._conversation_store.setdefault(key, [])
        request_messages = [*history, {"role": "user", "content": prompt.strip()}]
        if include_tools_context:
            request_messages.insert(0, {"role": "system", "content": self._build_tools_awareness_prompt()})

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

    def reply_with_plugins(
        self,
        conversation_id: str,
        prompt: str,
        model: str = "gpt-4.1-mini",
        include_tools_context: bool = True,
    ) -> dict[str, Any]:
        """Reply to a prompt, executing allowlisted plugin actions when appropriate."""
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(include_tools_context, bool):
            raise ValueError("include_tools_context must be a boolean")

        key = conversation_id.strip()
        action_reply = self._try_execute_plugin_action(prompt.strip())
        if action_reply is not None:
            history = self._conversation_store.setdefault(key, [])
            history.append({"role": "user", "content": prompt.strip()})
            history.append({"role": "assistant", "content": action_reply})
            return {
                "status": "success",
                "conversation_id": key,
                "model": "plugin-action",
                "response_id": None,
                "text": action_reply,
                "history_messages": len(history),
                "action_executed": True,
            }

        result = self.generate_text_with_history(
            key,
            prompt.strip(),
            model.strip(),
            include_tools_context,
        )
        result["action_executed"] = False
        return result
