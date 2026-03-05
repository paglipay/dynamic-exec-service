"""OpenAI SDK integration plugin for text generation."""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from config import ALLOWED_MODULES
from plugins.integrations.conversation_history_manager import ConversationHistoryManager
from plugins.text_file_crud_plugin import TextFileCRUDPlugin


class OpenAISDKPlugin:
    """Generate text using the official OpenAI Python SDK."""

    _conversation_store: dict[str, list[dict[str, str]]] = {}
    _filename_pattern = re.compile(r"([\"'`]*[A-Za-z0-9][A-Za-z0-9 _\-.]*\.(?:txt|md)[\"'`]*)", re.IGNORECASE)
    _default_joke_text = "Why don't scientists trust atoms? Because they make up everything!"
    _default_markdown_base_dir = Path("generated_data").resolve()
    _default_system_prompt_markdown = "README.md"
    _default_image_output_dir = _default_markdown_base_dir / "images"
    _safe_image_name = re.compile(r"[^A-Za-z0-9._-]+")

    def __init__(self, api_key: str | None = None) -> None:
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not isinstance(resolved_api_key, str) or not resolved_api_key.strip():
            raise ValueError("api_key must be provided (or set OPENAI_API_KEY)")

        self.client = OpenAI(api_key=resolved_api_key.strip())
        self._history_manager = ConversationHistoryManager.from_env()

    def _compact_history(self, conversation_id: str) -> list[dict[str, str]]:
        """Apply bounded-history compaction before a conversation turn."""
        raw_history = self._conversation_store.setdefault(conversation_id, [])
        history_manager = getattr(self, "_history_manager", ConversationHistoryManager())
        compacted, _meta = history_manager.compact(raw_history)
        normalized = [
            message
            for message in compacted
            if isinstance(message, dict)
            and isinstance(message.get("role"), str)
            and isinstance(message.get("content"), str)
        ]
        self._conversation_store[conversation_id] = normalized
        return normalized

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

    def _load_markdown_system_prompt(self, markdown_file: str | None) -> str | None:
        """Load markdown text from file for use as a system prompt."""
        target_markdown = markdown_file
        if target_markdown is None:
            target_markdown = self._default_system_prompt_markdown
        if not isinstance(target_markdown, str) or not target_markdown.strip():
            raise ValueError("markdown_file must be a non-empty string when provided")

        candidate = Path(target_markdown.strip())
        if candidate.suffix.lower() != ".md":
            raise ValueError("markdown_file must point to a .md file")

        if candidate.is_absolute():
            resolved_path = candidate.resolve()
        else:
            parts = candidate.parts
            if parts and parts[0].lower() == self._default_markdown_base_dir.name.lower():
                candidate = Path(*parts[1:]) if len(parts) > 1 else Path("")
            resolved_path = (self._default_markdown_base_dir / candidate).resolve()
        if not resolved_path.exists() or not resolved_path.is_file():
            raise ValueError("markdown_file does not exist")

        try:
            content = resolved_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Failed to read markdown_file: {exc}") from exc

        if not content.strip():
            raise ValueError("markdown_file is empty")

        return (
            f"Additional system context from markdown file '{resolved_path.name}':\n\n"
            f"{content.strip()}"
        )

    def _sanitize_image_file_stem(self, file_name: str | None) -> str:
        """Return a safe filename stem for generated images."""
        if file_name is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            return f"image_{timestamp}"

        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("file_name must be a non-empty string when provided")

        stem = Path(file_name.strip()).stem
        stem = self._safe_image_name.sub("_", stem).strip("._-")
        if not stem:
            raise ValueError("file_name must contain at least one alphanumeric character")

        return stem[:80]

    def _ensure_supported_image_option(self, field_name: str, value: str, allowed: set[str]) -> str:
        """Validate image generation option against an explicit allowlist."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        normalized = value.strip().lower()
        if normalized not in allowed:
            allowed_csv = ", ".join(sorted(allowed))
            raise ValueError(f"{field_name} must be one of: {allowed_csv}")
        return normalized

    def generate_text(
        self,
        prompt: str,
        model: str = "gpt-4.1-mini",
        include_tools_context: bool = True,
        markdown_file: str | None = None,
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
        markdown_system_prompt = self._load_markdown_system_prompt(markdown_file)
        if isinstance(markdown_system_prompt, str):
            insert_index = 1 if include_tools_context else 0
            input_messages.insert(insert_index, {"role": "system", "content": markdown_system_prompt})

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

    def generate_image(
        self,
        prompt: str,
        model: str = "gpt-image-1",
        size: str = "1024x1024",
        quality: str = "auto",
        background: str = "auto",
        output_format: str = "png",
        file_name: str | None = None,
    ) -> dict[str, Any]:
        """Generate an image from a prompt and store it under generated_data/images."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")

        normalized_size = self._ensure_supported_image_option(
            "size",
            size,
            {"1024x1024", "1024x1536", "1536x1024", "auto"},
        )
        normalized_quality = self._ensure_supported_image_option(
            "quality",
            quality,
            {"auto", "low", "medium", "high"},
        )
        normalized_background = self._ensure_supported_image_option(
            "background",
            background,
            {"auto", "transparent", "opaque"},
        )
        normalized_output_format = self._ensure_supported_image_option(
            "output_format",
            output_format,
            {"png", "jpeg", "webp"},
        )

        try:
            response = self.client.images.generate(
                model=model.strip(),
                prompt=prompt.strip(),
                size=normalized_size,
                quality=normalized_quality,
                background=normalized_background,
                output_format=normalized_output_format,
            )
        except Exception as exc:
            raise ValueError(f"OpenAI image generation failed: {exc}") from exc

        data = getattr(response, "data", None)
        if not isinstance(data, list) or not data:
            raise ValueError("OpenAI image generation response did not include image data")

        image_item = data[0]
        b64_json = getattr(image_item, "b64_json", None)
        image_url = getattr(image_item, "url", None)
        revised_prompt = getattr(image_item, "revised_prompt", None)

        if isinstance(b64_json, str) and b64_json.strip():
            try:
                image_bytes = base64.b64decode(b64_json, validate=True)
            except Exception as exc:
                raise ValueError(f"OpenAI image data could not be decoded: {exc}") from exc

            file_stem = self._sanitize_image_file_stem(file_name)
            output_dir = self._default_image_output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{file_stem}.{normalized_output_format}"
            output_path.write_bytes(image_bytes)

            return {
                "status": "success",
                "model": getattr(response, "model", model.strip()),
                "prompt": prompt.strip(),
                "revised_prompt": revised_prompt,
                "image_path": str(output_path),
                "image_format": normalized_output_format,
                "image_bytes": len(image_bytes),
            }

        if isinstance(image_url, str) and image_url.strip():
            return {
                "status": "success",
                "model": getattr(response, "model", model.strip()),
                "prompt": prompt.strip(),
                "revised_prompt": revised_prompt,
                "image_url": image_url.strip(),
                "image_format": normalized_output_format,
            }

        raise ValueError("OpenAI image generation response did not include b64_json or image url")

    def generate_text_with_history(
        self,
        conversation_id: str,
        prompt: str,
        model: str = "gpt-4.1-mini",
        include_tools_context: bool = True,
        markdown_file: str | None = None,
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
        history = self._compact_history(key)
        request_messages = [*history, {"role": "user", "content": prompt.strip()}]
        if include_tools_context:
            request_messages.insert(0, {"role": "system", "content": self._build_tools_awareness_prompt()})
        markdown_system_prompt = self._load_markdown_system_prompt(markdown_file)
        if isinstance(markdown_system_prompt, str):
            insert_index = 1 if include_tools_context else 0
            request_messages.insert(insert_index, {"role": "system", "content": markdown_system_prompt})

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
        history = self._compact_history(key)

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
        markdown_file: str | None = None,
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
        history = self._compact_history(key)
        action_reply = self._try_execute_plugin_action(prompt.strip())
        if action_reply is not None:
            history.append({"role": "user", "content": prompt.strip()})
            history.append({"role": "assistant", "content": action_reply})
            history = self._compact_history(key)
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
            markdown_file,
        )
        result["action_executed"] = False
        return result
