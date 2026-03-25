"""OpenAI function-calling integration plugin."""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

from openai import OpenAI
try:
    import redis  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency in some local environments
    redis = None

from config import ALLOWED_MODULES
from executor.engine import JSONExecutor
from executor.permissions import validate_request
from plugins.integrations.conversation_history_manager import ConversationHistoryManager


class OpenAIFunctionCallingPlugin:
    """Use OpenAI function calling with allowlisted plugin methods as tools."""

    _conversation_store: dict[str, list[dict[str, Any]]] = {}
    _conversation_redis_prefix = "openai_function_calling:conversation"
    _slack_images_root = "generated_data/slack_downloads"

    def __init__(self, api_key: str | None = None) -> None:
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not isinstance(resolved_api_key, str) or not resolved_api_key.strip():
            raise ValueError("api_key must be provided (or set OPENAI_API_KEY)")

        self.client = OpenAI(api_key=resolved_api_key.strip())
        self.executor = JSONExecutor()
        self._history_ttl_seconds = self._resolve_history_ttl_seconds()
        self._redis_client = self._build_redis_client()
        self._history_manager = ConversationHistoryManager.from_env()
        self._tool_name_to_target = self._build_tool_mapping()

    def _resolve_history_ttl_seconds(self) -> int:
        """Resolve conversation history TTL from environment with a safe default."""
        raw_ttl = os.getenv("OPENAI_CONVERSATION_TTL_SECONDS", "604800")
        try:
            ttl_seconds = int(raw_ttl)
        except (TypeError, ValueError):
            ttl_seconds = 604800

        if ttl_seconds <= 0:
            ttl_seconds = 604800

        return ttl_seconds

    def _build_redis_client(self) -> Any | None:
        """Build Redis client from REDIS_URL when available."""
        redis_url = os.getenv("REDIS_URL", "").strip()
        if not redis_url or redis is None:
            return None

        try:
            return redis.from_url(redis_url, decode_responses=True)
        except Exception:
            return None

    def _conversation_history_key(self, conversation_id: str) -> str:
        """Build Redis key for one conversation history."""
        return f"{self._conversation_redis_prefix}:{conversation_id}"

    def _load_conversation_history(self, conversation_id: str) -> list[dict[str, Any]]:
        """Load conversation history from Redis or in-memory fallback."""
        redis_client = getattr(self, "_redis_client", None)
        if redis_client is None:
            store = getattr(self, "_conversation_store", {})
            if not isinstance(store, dict):
                return []
            return list(store.get(conversation_id, []))

        redis_key = self._conversation_history_key(conversation_id)
        try:
            serialized = redis_client.get(redis_key)
        except Exception:
            return []

        if not serialized:
            return []

        try:
            history = json.loads(serialized)
        except (TypeError, ValueError):
            return []

        if not isinstance(history, list):
            return []

        return [message for message in history if isinstance(message, dict)]

    def _save_conversation_history(self, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        """Save conversation history to Redis or in-memory fallback."""
        history_manager = getattr(self, "_history_manager", ConversationHistoryManager())
        compacted_messages, _meta = history_manager.compact(messages)

        redis_client = getattr(self, "_redis_client", None)
        if redis_client is None:
            self._conversation_store[conversation_id] = list(compacted_messages)
            return

        redis_key = self._conversation_history_key(conversation_id)
        ttl_seconds = getattr(self, "_history_ttl_seconds", 604800)
        serialized = json.dumps(compacted_messages, ensure_ascii=False)
        try:
            redis_client.setex(redis_key, ttl_seconds, serialized)
        except Exception:
            # Last-resort fallback keeps behavior functional if Redis is temporarily unavailable.
            self._conversation_store[conversation_id] = list(compacted_messages)

    def redis_health_check(self, conversation_id: str | None = None) -> dict[str, Any]:
        """Return Redis diagnostics to verify shared history wiring in production."""
        backend = "redis" if getattr(self, "_redis_client", None) is not None else "memory"
        diagnostics: dict[str, Any] = {
            "status": "success",
            "backend": backend,
            "redis_url_configured": bool(os.getenv("REDIS_URL", "").strip()),
            "redis_package_installed": redis is not None,
            "history_ttl_seconds": getattr(self, "_history_ttl_seconds", 604800),
            "history_max_messages": getattr(getattr(self, "_history_manager", None), "max_messages", None),
            "history_keep_last_messages": getattr(getattr(self, "_history_manager", None), "keep_last_messages", None),
            "history_max_estimated_tokens": getattr(
                getattr(self, "_history_manager", None),
                "max_estimated_tokens",
                None,
            ),
            "dyno": os.getenv("DYNO", "local"),
            "redis_ping": None,
            "round_trip_ok": None,
        }

        if conversation_id is not None:
            if not isinstance(conversation_id, str) or not conversation_id.strip():
                raise ValueError("conversation_id must be a non-empty string when provided")

            key = conversation_id.strip()
            diagnostics["conversation_id"] = key
            diagnostics["history_messages"] = len(self._load_conversation_history(key))

        redis_client = getattr(self, "_redis_client", None)
        if redis_client is None:
            diagnostics["message"] = "Redis client unavailable; using in-memory fallback"
            return diagnostics

        try:
            diagnostics["redis_ping"] = bool(redis_client.ping())
        except Exception as exc:
            diagnostics["status"] = "error"
            diagnostics["redis_ping"] = False
            diagnostics["message"] = f"Redis ping failed: {exc}"
            return diagnostics

        health_key = f"{self._conversation_redis_prefix}:_healthcheck:{uuid4().hex}"
        health_payload = json.dumps({"ok": True})
        try:
            redis_client.setex(health_key, 60, health_payload)
            round_trip_payload = redis_client.get(health_key)
            diagnostics["round_trip_ok"] = round_trip_payload == health_payload
        except Exception as exc:
            diagnostics["status"] = "error"
            diagnostics["round_trip_ok"] = False
            diagnostics["message"] = f"Redis read/write check failed: {exc}"
            return diagnostics
        finally:
            try:
                redis_client.delete(health_key)
            except Exception:
                pass

        diagnostics["message"] = "Redis connectivity and write/read checks passed"
        return diagnostics

    def _build_tool_mapping(self) -> dict[str, tuple[str, str, str]]:
        """Build a deterministic mapping from tool names to allowlisted targets."""
        mapping: dict[str, tuple[str, str, str]] = {}
        counter = 1
        for module_name in sorted(ALLOWED_MODULES):
            module_config = ALLOWED_MODULES[module_name]
            class_name = module_config["class"]
            for method_name in module_config["methods"]:
                # Prevent recursive OpenAI->OpenAI tool loops, but allow image generation bridge.
                if module_name == "plugins.integrations.openai_plugin":
                    continue
                if module_name == "plugins.integrations.openai_http_plugin":
                    continue
                if (
                    module_name == "plugins.integrations.openai_sdk_plugin"
                    and method_name != "generate_image"
                ):
                    continue

                tool_name = f"plugin_tool_{counter:03d}"
                mapping[tool_name] = (module_name, class_name, method_name)
                counter += 1

        if not mapping:
            raise ValueError("No allowlisted plugin tools available")

        return mapping

    def _build_tool_description(
        self,
        module_name: str,
        class_name: str,
        method_name: str,
    ) -> str:
        """Build method-specific usage guidance for each tool."""
        if (
            module_name == "plugins.integrations.gmail_plugin"
            and class_name == "GmailPlugin"
            and method_name == "send_email"
        ):
            return (
                "Send an email through Gmail. "
                "Use args in this exact order: "
                "[to, subject, body_text, cc_or_null, bcc_or_null, attachments_or_null]. "
                "attachments_or_null must be null or a list of file paths, for example "
                "['generated_data/notes.txt']. "
                "When the user asks for an attachment, include a non-empty attachments list."
            )

        if (
            module_name == "plugins.integrations.gmail_plugin"
            and class_name == "GmailPlugin"
            and method_name == "list_messages"
        ):
            return (
                "List Gmail messages. "
                "Use args in this order: [query, max_results, label_ids_or_null]."
            )

        if (
            module_name == "plugins.integrations.gmail_plugin"
            and class_name == "GmailPlugin"
            and method_name == "get_message"
        ):
            return (
                "Fetch one Gmail message. "
                "Use args in this order: [message_id, format, metadata_headers_or_null]."
            )

        if (
            module_name == "plugins.system_tools.excel_plugin"
            and class_name == "ExcelPlugin"
            and method_name == "preview_sheet"
        ):
            return (
                "Return a small preview of one Excel sheet before larger extraction. "
                "Use args as a single payload object in args[0], for example: "
                "[{file_path, sheet, columns, max_rows, start_row}]. "
                "Prefer this for Slack-uploaded workbooks or when the user asks what a sheet contains."
            )

        if (
            module_name == "plugins.system_tools.excel_plugin"
            and class_name == "ExcelPlugin"
            and method_name == "excel_to_json"
        ):
            return (
                "Export Excel rows to JSON. "
                "Use args as a single payload object in args[0], for example: "
                "[{file_path, sheet, columns, filter_by, save_as, max_rows, start_row}]. "
                "columns must be an array of exact sheet header strings. "
                "filter_by must be an array of {column, operator, value}; operator supports 'contains'. "
                "Use max_rows to keep the result small when the user only needs a subset. "
                "Do not place file_path/sheet/columns/filter_by/save_as/max_rows/start_row at the top level of tool arguments; "
                "they belong inside args[0]."
            )

        if (
            module_name == "plugins.system_tools.excel_plugin"
            and class_name == "ExcelPlugin"
            and method_name == "list_columns_in_sheet"
        ):
            return (
                "List available columns in a sheet before building excel_to_json filters. "
                "Use args as a single payload object in args[0], for example: "
                "[{file_path, sheet}]."
            )

        if (
            module_name == "plugins.system_tools.excel_plugin"
            and class_name == "ExcelPlugin"
            and method_name == "list_sheet_names"
        ):
            return (
                "List workbook sheet names before previewing or extracting rows. "
                "Use args as a single payload object in args[0], for example: "
                "[{file_path}]."
            )

        if (
            module_name == "plugins.integrations.openai_sdk_plugin"
            and class_name == "OpenAISDKPlugin"
            and method_name == "generate_image"
        ):
            return (
                "Generate an image with OpenAI and save it locally. "
                "Use args in this exact order: "
                "[prompt, model, size, quality, background, output_format, file_name_or_null]. "
                "Example: ['A friendly robot coding', 'gpt-image-1', '1024x1024', 'high', 'opaque', 'png', 'robot_coding']."
            )

        return (
            f"Call plugin method {module_name}::{class_name}.{method_name}. "
            "Provide constructor_args and args when needed."
        )

    def _build_tools(self) -> list[dict[str, Any]]:
        """Build OpenAI tool definitions from allowlisted plugin methods."""
        tools: list[dict[str, Any]] = []
        for tool_name, (module_name, class_name, method_name) in self._tool_name_to_target.items():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": self._build_tool_description(
                            module_name,
                            class_name,
                            method_name,
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "constructor_args": {
                                    "type": "object",
                                    "description": "Constructor kwargs for the plugin class.",
                                    "additionalProperties": True,
                                },
                                "args": {
                                    "type": "array",
                                    "description": "Positional method arguments.",
                                    "items": {},
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return tools

    def _execute_tool_call(self, tool_name: str, arguments_json: str) -> str:
        """Execute a mapped plugin tool and return JSON-stringified output."""
        if tool_name not in self._tool_name_to_target:
            return json.dumps({"status": "error", "message": "Unknown tool requested"})

        module_name, class_name, method_name = self._tool_name_to_target[tool_name]

        try:
            parsed_arguments: Any = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            return json.dumps({"status": "error", "message": "Tool arguments are invalid JSON"})

        if not isinstance(parsed_arguments, dict):
            return json.dumps({"status": "error", "message": "Tool arguments must be an object"})

        constructor_args = parsed_arguments.get("constructor_args", {})
        args = parsed_arguments.get("args", [])

        if not isinstance(constructor_args, dict):
            return json.dumps({"status": "error", "message": "constructor_args must be an object"})
        if not isinstance(args, list):
            return json.dumps({"status": "error", "message": "args must be an array"})

        try:
            validate_request(module_name, class_name, method_name)
            self.executor.instantiate(module_name, class_name, constructor_args)
            result = self.executor.call_method(module_name, method_name, args)
            return json.dumps({"status": "success", "result": result}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)

    def _execute_chat_turn(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tool_rounds: int,
    ) -> tuple[str, int]:
        """Run function-calling rounds until final assistant text is produced."""
        tools = self._build_tools()
        executed_tool_calls = 0

        for _ in range(max_tool_rounds):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
            except Exception as exc:
                raise ValueError(f"OpenAI function-calling request failed: {exc}") from exc

            choice = response.choices[0]
            message = choice.message
            tool_calls = message.tool_calls or []

            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [tc.model_dump() for tc in tool_calls],
                    }
                )
                for tool_call in tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = tool_call.function.arguments or "{}"
                    tool_output = self._execute_tool_call(tool_name, tool_args)
                    executed_tool_calls += 1
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_output,
                        }
                    )
                continue

            final_text = message.content or ""
            if final_text.strip():
                messages.append(
                    {
                        "role": "assistant",
                        "content": final_text.strip(),
                    }
                )
                return final_text.strip(), executed_tool_calls

        raise ValueError("Exceeded max tool-calling rounds without a final response")

    def _build_system_prompt(self) -> str:
        """Build system guidance with plugin-tool and Slack image directory context."""
        return (
            "You can call available plugin tools when needed. "
            "Use tool calls for concrete actions and then provide a concise final answer. "
            "You are running inside a tool-enabled environment with access to local files through allowlisted plugins. "
            "If a user provides a local file path, do not claim you cannot access local files; call the appropriate tool instead. "
            "For plugin tool calls, put method inputs inside 'args' as positional arguments; "
            "when a plugin method accepts a payload object, pass it as args[0]. "
            "If the user asks to send an email attachment, include attachment file paths in Gmail send_email args. "
            "Do not claim an attachment was sent unless the Gmail tool result shows attachment_count > 0. "
            "Slack image attachments are saved locally under "
            f"'{self._slack_images_root}/' as a flat directory by the app. "
            "If the user asks to reference or locate Slack images, use this directory convention."
        )

    def _build_user_message(
        self,
        prompt: str,
        image_data_urls: list[str] | None,
    ) -> dict[str, Any]:
        """Build a user message with optional multimodal image content."""
        if not image_data_urls:
            return {"role": "user", "content": prompt.strip()}

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt.strip()}]
        for image_url in image_data_urls:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url.strip()},
                }
            )

        return {
            "role": "user",
            "content": content,
        }

    def generate_with_function_calls(
        self,
        prompt: str,
        model: str = "gpt-4.1-mini",
        max_tool_rounds: int = 5,
        image_data_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a response with plugin function-calling enabled."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(max_tool_rounds, int) or max_tool_rounds <= 0:
            raise ValueError("max_tool_rounds must be an integer > 0")
        if image_data_urls is not None:
            if not isinstance(image_data_urls, list):
                raise ValueError("image_data_urls must be an array when provided")
            if any(not isinstance(url, str) or not url.strip() for url in image_data_urls):
                raise ValueError("image_data_urls must contain non-empty strings")

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._build_system_prompt(),
            },
            self._build_user_message(prompt, image_data_urls),
        ]

        final_text, executed_tool_calls = self._execute_chat_turn(
            messages,
            model.strip(),
            max_tool_rounds,
        )

        return {
            "status": "success",
            "model": model.strip(),
            "text": final_text,
            "tool_calls_executed": executed_tool_calls,
        }

    def generate_with_function_calls_and_history(
        self,
        conversation_id: str,
        prompt: str,
        model: str = "gpt-4.1-mini",
        max_tool_rounds: int = 5,
        image_data_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a response with tool calls and preserve conversation history."""
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(max_tool_rounds, int) or max_tool_rounds <= 0:
            raise ValueError("max_tool_rounds must be an integer > 0")
        if image_data_urls is not None:
            if not isinstance(image_data_urls, list):
                raise ValueError("image_data_urls must be an array when provided")
            if any(not isinstance(url, str) or not url.strip() for url in image_data_urls):
                raise ValueError("image_data_urls must contain non-empty strings")

        key = conversation_id.strip()
        messages = self._load_conversation_history(key)
        history_manager = getattr(self, "_history_manager", ConversationHistoryManager())
        messages, compaction_meta_before_turn = history_manager.compact(messages)
        if not any(
            isinstance(message, dict)
            and message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and "Slack image attachments are saved locally" in message.get("content", "")
            for message in messages
        ):
            messages.insert(0, {"role": "system", "content": self._build_system_prompt()})
        messages.append(self._build_user_message(prompt, image_data_urls))

        final_text, executed_tool_calls = self._execute_chat_turn(
            messages,
            model.strip(),
            max_tool_rounds,
        )

        self._save_conversation_history(key, messages)
        stored_messages = self._load_conversation_history(key)
        _, compaction_meta_after_turn = history_manager.compact(stored_messages)

        return {
            "status": "success",
            "conversation_id": key,
            "model": model.strip(),
            "text": final_text,
            "history_messages": len(stored_messages),
            "tool_calls_executed": executed_tool_calls,
            "history_compacted": bool(
                compaction_meta_before_turn.get("compacted")
                or compaction_meta_after_turn.get("compacted")
            ),
            "history_estimated_tokens": compaction_meta_after_turn.get("after_estimated_tokens"),
        }
