"""OpenAI function-calling integration plugin."""

from __future__ import annotations

import json
import os
import sys
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
    _slack_images_root = os.getenv("BASE_DATA_DIR", "data") + "/slack_downloads"

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

    def clear_conversation_history(self, conversation_id: str) -> dict[str, Any]:
        """Clear stored conversation history for one conversation or all.

        Args:
            conversation_id: The conversation key to clear (e.g. "slack:C12345").
                             Pass "*" to delete ALL conversation keys under the prefix.

        Returns a dict with ``status``, ``backend``, ``cleared_count``, and
        optionally ``cleared_keys`` listing what was removed.
        """
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string, or '*' to clear all")

        conversation_id = conversation_id.strip()
        redis_client = getattr(self, "_redis_client", None)
        backend = "redis" if redis_client is not None else "memory"

        if conversation_id == "*":
            # Clear everything under this plugin's conversation prefix.
            if redis_client is not None:
                pattern = f"{self._conversation_redis_prefix}:*"
                cursor = 0
                cleared_keys: list[str] = []
                try:
                    while True:
                        cursor, batch = redis_client.scan(cursor, match=pattern, count=100)
                        if batch:
                            redis_client.delete(*batch)
                            cleared_keys.extend(batch)
                        if cursor == 0:
                            break
                except Exception as exc:
                    return {
                        "status": "error",
                        "backend": backend,
                        "message": f"Redis scan/delete failed: {exc}",
                    }
                return {
                    "status": "success",
                    "backend": backend,
                    "cleared_count": len(cleared_keys),
                    "cleared_keys": cleared_keys,
                }
            else:
                store = getattr(self, "_conversation_store", {})
                count = len(store)
                keys = list(store.keys())
                store.clear()
                return {
                    "status": "success",
                    "backend": backend,
                    "cleared_count": count,
                    "cleared_keys": keys,
                }

        # Clear a single specific conversation.
        if redis_client is not None:
            redis_key = self._conversation_history_key(conversation_id)
            try:
                deleted = redis_client.delete(redis_key)
            except Exception as exc:
                return {
                    "status": "error",
                    "backend": backend,
                    "message": f"Redis delete failed: {exc}",
                }
            return {
                "status": "success",
                "backend": backend,
                "conversation_id": conversation_id,
                "cleared_count": int(deleted),
                "existed": bool(deleted),
            }
        else:
            store = getattr(self, "_conversation_store", {})
            existed = conversation_id in store
            store.pop(conversation_id, None)
            return {
                "status": "success",
                "backend": backend,
                "conversation_id": conversation_id,
                "cleared_count": 1 if existed else 0,
                "existed": existed,
            }

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
            module_name == "plugins.mongodb_plugin"
            and class_name == "MongoDBPlugin"
            and method_name == "create_document"
        ):
            return (
                "Create one MongoDB document. "
                "Use args in this exact order: [collection, document]. "
                "Only claim creation succeeded after tool result shows inserted_id and action=create_document."
            )

        if (
            module_name == "plugins.mongodb_plugin"
            and class_name == "MongoDBPlugin"
            and method_name == "update_documents"
        ):
            return (
                "Update MongoDB documents with update operators. "
                "Use args in this exact order: "
                "[collection, filter_query, update_operations, upsert, many, allow_empty_filter, fail_on_no_match]. "
                "Check matched_count, modified_count, upserted_id, and operation_result before confirming success. "
                "If operation_result is no_match, do not claim data was updated."
            )

        if (
            module_name == "plugins.mongodb_plugin"
            and class_name == "MongoDBPlugin"
            and method_name == "replace_document"
        ):
            return (
                "Replace one MongoDB document by filter. "
                "Use args in this exact order: [collection, filter_query, replacement, upsert, fail_on_no_match]. "
                "Check matched_count, modified_count, upserted_id, and operation_result before confirming success."
            )

        if (
            module_name == "plugins.mongodb_plugin"
            and class_name == "MongoDBPlugin"
            and method_name == "find_documents"
        ):
            return (
                "Find MongoDB documents with filtering, projection, sorting, and pagination. "
                "Use args in this exact order: "
                "[collection, filter_query_or_null, projection_or_null, sort_or_null, limit, skip]. "
                "sort_or_null should be an array like [{field: 'created_at', direction: 'desc'}]."
            )

        if (
            module_name == "plugins.mongodb_plugin"
            and class_name == "MongoDBPlugin"
            and method_name == "text_search"
        ):
            return (
                "Run a MongoDB text search against a collection that already has a text index. "
                "Use args in this exact order: "
                "[collection, search_text, filter_query_or_null, projection_or_null, limit]. "
                "If search fails because no text index exists, call create_text_index first."
            )

        if (
            module_name == "plugins.mongodb_plugin"
            and class_name == "MongoDBPlugin"
            and method_name == "aggregate_documents"
        ):
            return (
                "Run a MongoDB aggregation pipeline and return JSON-serializable documents. "
                "Use args in this exact order: [collection, pipeline, limit]."
            )

        if (
            module_name == "plugins.mongodb_plugin"
            and class_name == "MongoDBPlugin"
            and method_name == "create_text_index"
        ):
            return (
                "Create a MongoDB text index before using text_search. "
                "Use args in this exact order: [collection, fields, index_name_or_null]."
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

        if (
            module_name == "plugins.system_tools.media_storage_plugin"
            and class_name == "MediaStoragePlugin"
        ):
            base = "Use constructor_args: {\"base_dir\": \"data\"}. "
            if method_name == "list_files":
                return (
                    base +
                    "List files in data storage. "
                    "Use args: [folder_or_empty_string]. Empty string lists the root. "
                    "Result entries include 'relative_path' relative to data/. "
                    "To get the full path for upload, prepend 'data/' to relative_path."
                )
            if method_name == "delete_file":
                return base + "Delete a file. Use args: [relative_path_within_data]."
            if method_name == "list_staged":
                return base + "List staged files for a session. Use args: [session_id]."
            if method_name == "clear_staged":
                return base + "Clear all staged files for a session. Use args: [session_id]."
            if method_name == "remove_staged_file":
                return base + "Remove one staged file. Use args: [session_id, filename]."
            if method_name == "zip_files":
                return (
                    base +
                    "Zip files already stored in data into a downloadable archive. "
                    "Use this whenever the user asks to zip files. No staging session required. "
                    "Use args: [file_paths_list, zip_name_or_empty_string]. "
                    "file_paths_list is a list of relative_path values from list_files (e.g. ['01.mp4', '01A.jpg']). "
                    "The result includes 'local_path' — pass that directly to SlackPlugin.upload_local_file as file_path."
                )

        if (
            module_name == "plugins.system_tools.file_reader_plugin"
            and class_name == "FileReaderPlugin"
        ):
            base = "Use constructor_args: {\"base_dir\": \"data\"}. "
            if method_name == "list_directory":
                return (
                    base +
                    "List files in data/. "
                    "Use args: [relative_subdirectory_or_dot]. "
                    "'.' lists the root of data. 'slack_downloads' lists Slack files. "
                    "Result entries include 'relative_path' relative to data/. "
                    "To get the full path for Slack upload, use 'data/' + relative_path."
                )
            if method_name == "read_text_file":
                return base + "Read a text/markdown/csv/tsv file. Use args: [file_path, max_chars_or_20000]."
            if method_name == "read_pdf_text":
                return base + "Extract text from a PDF file. Use args: [file_path, max_chars_or_20000]."
            if method_name == "read_docx_text":
                return base + "Extract text from a DOCX file. Use args: [file_path, max_chars_or_20000]."
            if method_name == "parse_csv_tsv":
                return base + "Parse a CSV or TSV file. Use args: [file_path, max_rows_or_25, delimiter_or_auto]."
            if method_name == "summarize_excel":
                return base + "Summarize an Excel workbook. Use args: [file_path, max_preview_rows_or_5]."
            if method_name == "read_image_for_vision":
                return (
                    "Read an image file and return a base64 data_url for vision analysis. "
                    "No constructor_args needed. "
                    "Use args: [file_path, max_long_edge_or_1024]. "
                    "file_path is the path as shown in the upload notification or list_files result, e.g. "
                    "'data/staging/<session_id>/photo.jpg' or 'data/photo.jpg' or "
                    "'data/slack_downloads/image.png'. "
                    "Use the EXACT path from the [System event] or tool result — do not shorten or guess it. "
                    "The result contains a 'data_url' field; pass it directly to vision reasoning to describe the image."
                )
            if method_name == "read_image_gps":
                return (
                    "Read GPS coordinates AND all EXIF metadata from a JPEG image file. "
                    "Use this tool — and ONLY this tool — when the user asks about EXIF data, location, GPS, "
                    "camera details, when a photo was taken, or any image metadata. "
                    "Do NOT also call read_image_for_vision for metadata questions — this tool is self-contained. "
                    "No constructor_args needed. "
                    "Use args: [file_path]. "
                    "Use the EXACT file path from the upload notification or list_files result. "
                    "Returns lat, lon, has_gps, and an 'exif' dict with all decoded fields (Make, Model, DateTime, ExposureTime, ISO, etc.). "
                    "Do NOT use ImageProcessingPlugin for GPS — it is slow due to database connections."
                )

        if (
            module_name == "plugins.system_tools.file_system_plugin"
            and class_name == "FileSystemPlugin"
            and method_name == "list_directory"
        ):
            return (
                "List files inside data/. "
                "Use constructor_args: {\"base_dir\": \"data\"}. "
                "Use args: [relative_subdirectory_or_dot]. "
                "Do NOT use this to list upload files; use MediaStoragePlugin.list_files instead."
            )

        if (
            module_name == "plugins.integrations.slack_plugin"
            and class_name == "SlackPlugin"
            and method_name == "upload_local_file"
        ):
            return (
                "Upload a local file to a Slack channel. "
                "Use args: [file_path, channel, title_or_null, initial_comment_or_null]. "
                "For 'channel', always use the channel ID from [slack_channel_id: ...] in the current message — "
                "do NOT pass null; pass the explicit channel ID so the bot can upload to the right place. "
                "Example: if the message contains '[slack_channel_id: C08SH2VRPJL]', use 'C08SH2VRPJL' as channel. "
                "file_path must be the path relative to the app working directory, e.g. "
                "'media_storage/photo.jpg' or 'generated_data/device_image_proper.md'. "
                "Obtain the path from list_files or list_directory result entries "
                "by prepending the root ('media_storage/' or 'generated_data/') to the relative_path field. "
                "Do not invent paths; always look up the path from a prior tool result."
            )

        if (
            module_name == "plugins.integrations.slack_plugin"
            and class_name == "SlackPlugin"
            and method_name == "post_message"
        ):
            return (
                "Post a text message to a Slack channel. "
                "Use args: [channel, text]. "
                "channel can be a channel name like '#network' or a channel ID like 'C123ABC'."
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

            if module_name == "plugins.mongodb_plugin" and isinstance(result, dict):
                if method_name == "create_document":
                    inserted_id = result.get("inserted_id")
                    if not inserted_id:
                        return json.dumps(
                            {
                                "status": "error",
                                "message": "MongoDB create_document did not return inserted_id",
                                "result": result,
                            },
                            ensure_ascii=False,
                        )

                if method_name in {"update_documents", "replace_document"}:
                    operation_result = result.get("operation_result")
                    if operation_result == "no_match":
                        return json.dumps(
                            {
                                "status": "error",
                                "message": "MongoDB write matched zero documents",
                                "result": result,
                            },
                            ensure_ascii=False,
                        )

            return json.dumps({"status": "success", "result": result}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)

    def _execute_chat_turn(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tool_rounds: int,
    ) -> tuple[str, int, list[str]]:
        """Run function-calling rounds until final assistant text is produced."""
        tools = self._build_tools()
        executed_tool_calls = 0
        analyzed_image_paths: list[str] = []

        for round_num in range(max_tool_rounds):
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
            content = message.content or ""

            # Log each round for debugging
            print(f"[OpenAI][Round {round_num+1}/{max_tool_rounds}] tools_called={len(tool_calls)}, content_len={len(content)}", file=sys.stderr)
            if tool_calls:
                tool_names = [tc.function.name for tc in tool_calls]
                print(f"[OpenAI][Round {round_num+1}] tool_calls: {tool_names}", file=sys.stderr)

            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": [tc.model_dump() for tc in tool_calls],
                    }
                )
                for tool_call in tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = tool_call.function.arguments or "{}"
                    try:
                        tool_output = self._execute_tool_call(tool_name, tool_args)
                        executed_tool_calls += 1
                        print(f"[OpenAI][Round {round_num+1}] {tool_name} executed successfully", file=sys.stderr)
                    except Exception as tool_exc:
                        print(f"[OpenAI][Round {round_num+1}] {tool_name} failed: {tool_exc}", file=sys.stderr)
                        tool_output = json.dumps({"error": str(tool_exc)})
                    # Track file paths passed to read_image_for_vision so callers can
                    # build MongoDB metadata for images from any upload source.
                    _tgt = self._tool_name_to_target.get(tool_name, ("", "", ""))
                    if _tgt[2] == "read_image_for_vision":
                        try:
                            _targs = json.loads(tool_args).get("args", [])
                            if _targs and isinstance(_targs[0], str) and _targs[0]:
                                analyzed_image_paths.append(_targs[0])
                        except Exception:
                            pass
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_output,
                        }
                    )
                    # If the tool returned an image data_url, inject it as a user
                    # vision message so the model can actually see the image.
                    try:
                        tool_result_parsed = json.loads(tool_output)
                        data_url = (
                            tool_result_parsed.get("result", {}).get("data_url")
                            if isinstance(tool_result_parsed.get("result"), dict)
                            else None
                        )
                        if isinstance(data_url, str) and data_url.startswith("data:image/"):
                            messages.append(
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "Here is the image content for your analysis:",
                                        },
                                        {
                                            "type": "image_url",
                                            "image_url": {"url": data_url},
                                        },
                                    ],
                                }
                            )
                    except Exception:
                        pass
                continue

            final_text = message.content or ""
            if final_text.strip():
                print(f"[OpenAI][Round {round_num+1}] Got final response: {len(final_text)} chars", file=sys.stderr)
                messages.append(
                    {
                        "role": "assistant",
                        "content": final_text.strip(),
                    }
                )
                return final_text.strip(), executed_tool_calls, analyzed_image_paths
            else:
                # Model returned neither tool calls nor content — nudge it to produce
                # a final reply so the next round has new context to act on.
                print(f"[OpenAI][Round {round_num+1}] No content and no tool_calls — injecting finalization nudge", file=sys.stderr)
                messages.append({"role": "user", "content": "Please provide your final answer now."})

        print(f"[OpenAI] Max rounds ({max_tool_rounds}) exceeded without final response", file=sys.stderr)
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
            "\nImage handling rules:"
            "\n  0. FILE SHARED WITH NO EXPLICIT REQUEST: if the user uploaded or shared a file without "
            "asking for analysis, EXIF data, or a description — acknowledge receipt briefly "
            "(e.g. 'Got it! Let me know what you need.'). Even though image pixels are present in context, "
            "do NOT describe or analyze the image. Do NOT call read_image_gps. Wait for the user to ask "
            "something specific. Use conversation context to judge intent — if the user's message (including "
            "typos or paraphrases) is asking for visual analysis, proceed with rule 2."
            "\n  1. EXIF/METADATA REQUESTS (EXIF, GPS, location, coordinates, camera make/model, 'when was this taken', "
            "'date taken', 'show image data', 'show metadata'): "
            "call FileReaderPlugin.read_image_gps with the saved local file path immediately. "
            "The saved path is listed under 'Saved local image copies' in the context. "
            "Do NOT describe visual content. Do NOT ask the user first. read_image_gps is self-contained."
            "\n  2. VISUAL ANALYSIS REQUESTS (describe, what is shown, analyze, identify, what do you see, "
            "'analize', 'analyse', or any paraphrase requesting you to look at or explain the image): "
            "call FileReaderPlugin.read_image_gps first to get EXIF/metadata, then use the image pixels "
            "already provided in context for the visual description. "
            "Include both the EXIF summary and the visual description in your reply."
            "\n  3. ALL OTHER REQUESTS (file info, storage, questions unrelated to image content): "
            "do not analyze image pixels. Use the saved file path and metadata only. "
            "Never proactively describe an image unless the user explicitly asks."
            "\nImage pixels are always included in context when images are attached. Whether to use them "
            "depends entirely on what the user is asking — use your judgment based on the conversation. "
            "\n\nStorage layout (relative to the app working directory):\n"
            "- media_storage/ : uploaded files, staging sessions, and zip outputs. "
            "Use MediaStoragePlugin (constructor_args: {\"base_dir\": \"media_storage\"}) to list or manage these files. "
            "list_files returns entries with a 'relative_path' field; prepend 'media_storage/' to get the full path.\n"
            "- generated_data/ : Slack downloads, processed files, notes. "
            "Use FileReaderPlugin (constructor_args: {\"base_dir\": \"generated_data\"}) to read files here. "
            "list_directory returns entries with a 'relative_path' field; prepend 'generated_data/' to get the full path. "
            "Slack image attachments are saved under 'generated_data/slack_downloads/'.\n"
            "\nWhen uploading a file to Slack with upload_local_file, the file_path arg must be the "
            "full relative path constructed from the tool result (e.g. 'media_storage/photo.jpg'). "
            "Always look up the path from a prior list_files or list_directory call — never invent it. "
            "\n\nIf the user asks to send an email attachment, include attachment file paths in Gmail send_email args. "
            "Do not claim an attachment was sent unless the Gmail tool result shows attachment_count > 0. "
            "For MongoDB write tools, do not claim a document was created/updated/replaced unless the tool result proves it. "
            "Require inserted_id for create_document. "
            "Require operation_result != 'no_match' for update_documents and replace_document."
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

        final_text, executed_tool_calls, _ = self._execute_chat_turn(
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

    @staticmethod
    def _strip_image_urls_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return messages with image_url content removed, keeping only text.

        Prevents base64 image pixels from being persisted in conversation history,
        which would give the model vision access on every subsequent request.
        """
        stripped = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = [
                    part for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                if text_parts:
                    text_only = " ".join(p.get("text", "") for p in text_parts).strip()
                    stripped.append({**msg, "content": text_only})
                # Skip messages that were purely image_url (no text) — they carry no useful history
            else:
                stripped.append(msg)
        return stripped

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
            and "media_storage/" in message.get("content", "")
            for message in messages
        ):
            messages.insert(0, {"role": "system", "content": self._build_system_prompt()})
        messages.append(self._build_user_message(prompt, image_data_urls))

        final_text, executed_tool_calls, analyzed_image_paths = self._execute_chat_turn(
            messages,
            model.strip(),
            max_tool_rounds,
        )

        self._save_conversation_history(key, self._strip_image_urls_from_messages(messages))
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
            "analyzed_image_paths": analyzed_image_paths,
        }
