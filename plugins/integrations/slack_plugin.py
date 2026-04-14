"""Slack integration plugin for posting messages to channels."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlencode


class SlackPlugin:
    def open_modal(self, args: dict[str, Any]) -> dict[str, Any]:
        """Open a Slack modal. Args: trigger_id, modal_view (dict with Slack modal fields)."""
        if not isinstance(args, dict):
            raise ValueError("args must be a dict")
        trigger_id = args.get("trigger_id")
        modal_view = args.get("modal_view")
        if not isinstance(trigger_id, str) or not trigger_id.strip():
            raise ValueError("trigger_id must be a non-empty string")
        if not isinstance(modal_view, dict):
            raise ValueError("modal_view must be a dict")
        # Map modal_view fields to open_modal_form signature
        form = {
            "trigger_id": trigger_id,
            "title": modal_view.get("title", {}).get("text", "Modal"),
            "submit_label": modal_view.get("submit", {}).get("text", "Submit"),
            "close_label": modal_view.get("close", {}).get("text", "Cancel"),
            "blocks": modal_view.get("blocks", []),
        }
        if "callback_id" in modal_view:
            form["callback_id"] = modal_view["callback_id"]
        if "private_metadata" in modal_view:
            form["private_metadata"] = modal_view["private_metadata"]
        return self.open_modal_form(form)

    def request_modal_with_button(self, args: dict[str, Any]) -> dict[str, Any]:
        """Post a message with a button to trigger a modal. Args: channel, button_text, message_text, callback_id, modal_view (optional)."""
        import traceback
        print("[SlackPlugin][DEBUG] request_modal_with_button called with args:", args)
        channel = args.get("channel", self.default_channel)
        button_text = args.get("button_text", "Open Modal")
        message_text = args.get("message_text", "Click the button to open a modal.")
        callback_id = args.get("callback_id", "open_modal_button")
        modal_view = args.get("modal_view")
        print(f"[SlackPlugin][DEBUG] channel={channel}, button_text={button_text}, message_text={message_text}, callback_id={callback_id}")
        if not isinstance(channel, str) or not channel.strip():
            print("[SlackPlugin][ERROR] channel must be a non-empty string")
            raise ValueError("channel must be a non-empty string")
        if not isinstance(button_text, str) or not button_text.strip():
            print("[SlackPlugin][ERROR] button_text must be a non-empty string")
            raise ValueError("button_text must be a non-empty string")
        if not isinstance(message_text, str) or not message_text.strip():
            print("[SlackPlugin][ERROR] message_text must be a non-empty string")
            raise ValueError("message_text must be a non-empty string")
        if not isinstance(callback_id, str) or not callback_id.strip():
            print("[SlackPlugin][ERROR] callback_id must be a non-empty string")
            raise ValueError("callback_id must be a non-empty string")
        # If modal_view is provided, store it in Redis and set button value to modalview:<key>
        button_value = "open_modal"
        redis_key = None
        if isinstance(modal_view, dict):
            try:
                import redis as redis_mod
            except ImportError:
                redis_mod = None
            redis_url = os.getenv("REDIS_URL", "").strip()
            redis_client = None
            if redis_mod and redis_url:
                try:
                    redis_client = redis_mod.from_url(redis_url, decode_responses=True)
                except Exception as exc:
                    print(f"[SlackPlugin][ERROR] Failed to connect to Redis: {exc}")
                    redis_client = None
            import uuid
            modal_id = str(uuid.uuid4())
            if redis_client:
                try:
                    redis_client.set(f"slack:modal_view:{modal_id}", json.dumps(modal_view), ex=86400)
                    button_value = f"modalview:{modal_id}"
                    redis_key = modal_id
                except Exception as exc:
                    print(f"[SlackPlugin][ERROR] Failed to store modal_view in Redis: {exc}")
        button_element = {
            "type": "button",
            "text": {"type": "plain_text", "text": button_text},
            "action_id": callback_id,
            "value": button_value,
        }
        # Do NOT include modal_view in the outgoing Slack payload (invalid_blocks). Store for backend use only.
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message_text},
            },
            {
                "type": "actions",
                "block_id": callback_id,
                "elements": [button_element],
            },
        ]
        payload = {
            "channel": channel.strip(),
            "text": message_text,
            "blocks": blocks,
        }
        print("[SlackPlugin][DEBUG] Sending chat.postMessage payload:")
        print(json.dumps(payload, indent=2))
        try:
            response = self._post_json(self.api_url, payload)
        except Exception as exc:
            print("[SlackPlugin][ERROR] Exception in _post_json:", exc)
            traceback.print_exc()
            return {
                "status": "error",
                "action": "request_modal_with_button",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "message": "Failed to post message with modal trigger button",
            }
        print("[SlackPlugin][DEBUG] chat.postMessage response:", response)
        return {
            "status": "success",
            "action": "request_modal_with_button",
            "channel": response.get("channel", channel.strip()),
            "ts": response.get("ts"),
            "message": "Message with modal trigger button posted",
        }


    """Post messages to Slack using the chat.postMessage Web API."""

    def __init__(
        self,
        bot_token: str | None = None,
        default_channel: str = "#general",
        api_url: str = "https://slack.com/api/chat.postMessage",
    ) -> None:
        token = bot_token
        if token is None:
            token = os.getenv("SLACK_BOT_TOKEN")
        if not isinstance(token, str) or not token.strip():
            raise ValueError(
                "SLACK_BOT_TOKEN must be set in environment or provided as bot_token"
            )
        if not isinstance(default_channel, str) or not default_channel.strip():
            raise ValueError("default_channel must be a non-empty string")
        if api_url != "https://slack.com/api/chat.postMessage":
            raise ValueError("api_url must be https://slack.com/api/chat.postMessage")

        self.bot_token = token.strip()
        self.default_channel = default_channel.strip()
        self.api_url = api_url

    @staticmethod
    def _validate_blocks(blocks: Any) -> list[dict[str, Any]]:
        if not isinstance(blocks, list) or not blocks:
            raise ValueError("blocks must be a non-empty list")

        validated_blocks: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("each block must be an object")
            validated_blocks.append(block)
        return validated_blocks

    @staticmethod
    def extract_view_submission_values(view_state_values: Any) -> dict[str, Any]:
        """Flatten Slack modal submission state into a JSON-serializable dict."""
        if not isinstance(view_state_values, dict):
            return {}

        extracted: dict[str, Any] = {}
        for block_id, actions in view_state_values.items():
            if not isinstance(block_id, str) or not isinstance(actions, dict):
                button_value = "open_modal_button"

            for action_id, action_payload in actions.items():
                if not isinstance(action_id, str) or not isinstance(
                    action_payload, dict
                ):
                    continue

                field_key = f"{block_id}.{action_id}"
                if "value" in action_payload:
                    extracted[field_key] = action_payload.get("value")
                    continue
                if "selected_option" in action_payload:
                    selected_option = action_payload.get("selected_option")
                    if isinstance(selected_option, dict):
                        extracted[field_key] = selected_option.get("value")
                    continue
                if "selected_options" in action_payload:
                    selected_options = action_payload.get("selected_options")
                    if isinstance(selected_options, list):
                        extracted[field_key] = [
                            item.get("value")
                            for item in selected_options
                            if isinstance(item, dict)
                            and isinstance(item.get("value"), str)
                        ]
                    continue
                if "selected_date" in action_payload:
                    extracted[field_key] = action_payload.get("selected_date")
                    continue
                if "selected_time" in action_payload:
                    extracted[field_key] = action_payload.get("selected_time")
                    continue
                if "selected_conversation" in action_payload:
                    extracted[field_key] = action_payload.get("selected_conversation")
                    continue
                if "selected_channel" in action_payload:
                    extracted[field_key] = action_payload.get("selected_channel")
                    continue
                if "selected_user" in action_payload:
                    extracted[field_key] = action_payload.get("selected_user")
                    continue
                if "selected_users" in action_payload:
                    extracted[field_key] = action_payload.get("selected_users")
                    continue
                if "selected_channels" in action_payload:
                    extracted[field_key] = action_payload.get("selected_channels")
                    continue
                if "selected_conversations" in action_payload:
                    extracted[field_key] = action_payload.get("selected_conversations")
                    continue
                if "type" in action_payload:
                    extracted[field_key] = action_payload.get("type")

        return extracted

    def _resolve_channel_id(self, channel: str) -> str:
        channel_value = channel.strip()
        if re.fullmatch(r"[CGD][A-Z0-9]+", channel_value):
            return channel_value

        target_name = (
            channel_value[1:] if channel_value.startswith("#") else channel_value
        )
        target_name = target_name.strip().lower()
        if not target_name:
            raise ValueError("channel must be a non-empty string")

        cursor = ""
        while True:
            payload: dict[str, Any] = {
                "exclude_archived": "true",
                "limit": "1000",
                "types": "public_channel",
            }
            if cursor:
                payload["cursor"] = cursor

            page = self._post_form("https://slack.com/api/conversations.list", payload)
            channels = page.get("channels")
            if isinstance(channels, list):
                for item in channels:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    channel_id = item.get("id")
                    if (
                        isinstance(name, str)
                        and isinstance(channel_id, str)
                        and name.strip().lower() == target_name
                    ):
                        return channel_id

            metadata = page.get("response_metadata")
            next_cursor = ""
            if isinstance(metadata, dict):
                cursor_value = metadata.get("next_cursor")
                if isinstance(cursor_value, str):
                    next_cursor = cursor_value.strip()

            if not next_cursor:
                break
            cursor = next_cursor

        raise ValueError(
            "Slack channel name could not be resolved to channel ID. "
            "Use a channel ID (for example C123...) or ensure the bot can list channels."
        )

    def _parse_slack_response(self, body: str) -> dict[str, Any]:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("Slack API returned invalid JSON") from exc

        if not isinstance(parsed, dict):
            raise ValueError("Slack API returned unexpected response type")

        if not parsed.get("ok", False):
            slack_error = parsed.get("error", "unknown_error")
            details: list[str] = []
            needed = parsed.get("needed")
            provided = parsed.get("provided")
            if isinstance(needed, str) and needed.strip():
                details.append(f"needed={needed.strip()}")
            if isinstance(provided, str) and provided.strip():
                details.append(f"provided={provided.strip()}")

            if details:
                raise ValueError(
                    f"Slack API error: {slack_error} ({'; '.join(details)})"
                )
            raise ValueError(f"Slack API error: {slack_error}")

        return parsed

    def _post_json(self, api_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        import traceback
        print(f"[SlackPlugin][DEBUG] _post_json called: api_url={api_url}")
        print(f"[SlackPlugin][DEBUG] bot_token={self.bot_token[:8]}... (length={len(self.bot_token)})")
        print(f"[SlackPlugin][DEBUG] payload: {json.dumps(payload, indent=2)}")
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            api_url,
            data=data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.bot_token}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"[SlackPlugin][ERROR] HTTPError: {exc.code} {body}")
            traceback.print_exc()
            raise ValueError(f"Slack HTTP error {exc.code}: {body}") from exc
        except error.URLError as exc:
            print(f"[SlackPlugin][ERROR] URLError: {exc.reason}")
            traceback.print_exc()
            raise ValueError(f"Failed to reach Slack API: {exc.reason}") from exc
        print(f"[SlackPlugin][DEBUG] _post_json response body: {body}")
        return self._parse_slack_response(body)

    def _post_form(self, api_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = urlencode(payload).encode("utf-8")
        req = request.Request(
            api_url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "Authorization": f"Bearer {self.bot_token}",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"Slack HTTP error {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise ValueError(f"Failed to reach Slack API: {exc.reason}") from exc

        return self._parse_slack_response(body)

    def post_message(self, channel: str, text: str) -> dict[str, Any]:
        """Send a plain text message to a Slack channel."""
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel must be a non-empty string")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")

        payload = {"channel": channel.strip(), "text": text.strip()}
        parsed = self._post_json(self.api_url, payload)

        return {
            "status": "success",
            "channel": parsed.get("channel", channel.strip()),
            "ts": parsed.get("ts"),
            "message": "Message sent to Slack",
        }

    def post_form_message(
        self,
        form: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a Slack message with Block Kit content that can act as a lightweight form."""
        if not isinstance(form, dict):
            raise ValueError("form must be an object")

        channel = form.get("channel", self.default_channel)
        text = form.get("text", "Please complete this form.")
        blocks = form.get("blocks")

        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel must be a non-empty string")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")

        payload = {
            "channel": channel.strip(),
            "text": text.strip(),
            "blocks": self._validate_blocks(blocks),
        }
        parsed = self._post_json(self.api_url, payload)

        return {
            "status": "success",
            "action": "post_form_message",
            "channel": parsed.get("channel", channel.strip()),
            "ts": parsed.get("ts"),
            "message": "Slack form message posted",
        }

    def open_modal_form(
        self,
        form: dict[str, Any],
    ) -> dict[str, Any]:
        """Open a Slack modal with input blocks."""
        if not isinstance(form, dict):
            raise ValueError("form must be an object")

        trigger_id = form.get("trigger_id")
        title = form.get("title")
        submit_label = form.get("submit_label", "Submit")
        blocks = form.get("blocks")
        callback_id = form.get("callback_id")
        close_label = form.get("close_label", "Cancel")
        private_metadata = form.get("private_metadata")

        if not isinstance(trigger_id, str) or not trigger_id.strip():
            raise ValueError("trigger_id must be a non-empty string")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        if len(title.strip()) > 24:
            raise ValueError("title must be 24 characters or fewer for Slack modals")
        if not isinstance(submit_label, str) or not submit_label.strip():
            raise ValueError("submit_label must be a non-empty string")
        if len(submit_label.strip()) > 24:
            raise ValueError(
                "submit_label must be 24 characters or fewer for Slack modals"
            )
        if not isinstance(close_label, str) or not close_label.strip():
            raise ValueError("close_label must be a non-empty string")
        if len(close_label.strip()) > 24:
            raise ValueError(
                "close_label must be 24 characters or fewer for Slack modals"
            )
        if callback_id is not None and (
            not isinstance(callback_id, str) or not callback_id.strip()
        ):
            raise ValueError("callback_id must be a non-empty string when provided")
        if private_metadata is not None and not isinstance(private_metadata, str):
            raise ValueError("private_metadata must be a string when provided")

        view: dict[str, Any] = {
            "type": "modal",
            "title": {"type": "plain_text", "text": title.strip()},
            "submit": {"type": "plain_text", "text": submit_label.strip()},
            "close": {"type": "plain_text", "text": close_label.strip()},
            "blocks": self._validate_blocks(blocks),
        }
        if isinstance(callback_id, str) and callback_id.strip():
            view["callback_id"] = callback_id.strip()
        if isinstance(private_metadata, str):
            view["private_metadata"] = private_metadata

        parsed = self._post_json(
            "https://slack.com/api/views.open",
            {
                "trigger_id": trigger_id.strip(),
                "view": view,
            },
        )
        view_payload = (
            parsed.get("view") if isinstance(parsed.get("view"), dict) else {}
        )
        return {
            "status": "success",
            "action": "open_modal_form",
            "view_id": view_payload.get("id"),
            "external_id": view_payload.get("external_id"),
            "callback_id": view_payload.get(
                "callback_id",
                callback_id.strip() if isinstance(callback_id, str) else None,
            ),
            "message": "Slack modal form opened",
        }

    def _upload_file_bytes(
        self,
        filename: str,
        file_bytes: bytes,
        channel: str | None = None,
        title: str | None = None,
        initial_comment: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("filename must be a non-empty string")
        if not isinstance(file_bytes, bytes) or not file_bytes:
            raise ValueError("file content must be non-empty")
        if channel is not None and (
            not isinstance(channel, str) or not channel.strip()
        ):
            raise ValueError("channel must be a non-empty string when provided")
        if title is not None and not isinstance(title, str):
            raise ValueError("title must be a string when provided")
        if initial_comment is not None and not isinstance(initial_comment, str):
            raise ValueError("initial_comment must be a string when provided")

        target_channel = (
            channel.strip()
            if isinstance(channel, str) and channel.strip()
            else self.default_channel
        )
        target_channel_id = self._resolve_channel_id(target_channel)
        file_title = (
            title.strip()
            if isinstance(title, str) and title.strip()
            else filename.strip()
        )

        pre_upload = self._post_form(
            "https://slack.com/api/files.getUploadURLExternal",
            {
                "filename": filename.strip(),
                "length": str(len(file_bytes)),
            },
        )

        upload_url = pre_upload.get("upload_url")
        file_id = pre_upload.get("file_id")
        if not isinstance(upload_url, str) or not upload_url.strip():
            raise ValueError("Slack API did not return a valid upload_url")
        if not isinstance(file_id, str) or not file_id.strip():
            raise ValueError("Slack API did not return a valid file_id")

        upload_req = request.Request(
            upload_url,
            data=file_bytes,
            headers={
                "Content-Type": "application/octet-stream",
            },
            method="POST",
        )
        try:
            with request.urlopen(upload_req, timeout=30):
                pass
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"Slack upload URL HTTP error {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise ValueError(
                f"Failed to upload file to Slack URL: {exc.reason}"
            ) from exc

        completion_payload: dict[str, Any] = {
            "files": json.dumps(
                [
                    {
                        "id": file_id,
                        "title": file_title,
                    }
                ]
            ),
            "channel_id": target_channel_id,
        }
        if isinstance(initial_comment, str) and initial_comment.strip():
            completion_payload["initial_comment"] = initial_comment.strip()

        completed = self._post_form(
            "https://slack.com/api/files.completeUploadExternal",
            completion_payload,
        )

        files = completed.get("files")
        uploaded_file = files[0] if isinstance(files, list) and files else {}

        return {
            "status": "success",
            "action": "upload_local_file",
            "channel": target_channel,
            "channel_id": target_channel_id,
            "file_id": uploaded_file.get("id", file_id),
            "file_name": uploaded_file.get("name", filename.strip()),
            "title": uploaded_file.get("title", file_title),
            "message": "File uploaded to Slack",
        }

    def upload_text_file(
        self,
        filename: str,
        content: str,
        channel: str | None = None,
        title: str | None = None,
        initial_comment: str | None = None,
    ) -> dict[str, Any]:
        """Upload a text file to Slack using files.getUploadURLExternal + files.completeUploadExternal."""
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("filename must be a non-empty string")
        if not isinstance(content, str) or not content:
            raise ValueError("content must be a non-empty string")
        result = self._upload_file_bytes(
            filename=filename.strip(),
            file_bytes=content.encode("utf-8"),
            channel=channel,
            title=title,
            initial_comment=initial_comment,
        )
        try:
            file_info = self._fetch_file_info(result["file_id"])
            self._save_file_record(
                local_file_path=filename.strip(),
                file_id=result["file_id"],
                filename=result["file_name"],
                title=result["title"],
                channel=result["channel"],
                channel_id=result["channel_id"],
                permalink=file_info.get("permalink"),
                url_private=file_info.get("url_private"),
            )
            result["permalink"] = file_info.get("permalink")
            result["url_private"] = file_info.get("url_private")
        except Exception:
            pass
        return result

    def upload_local_file(
        self,
        file_path: str,
        channel: str | None = None,
        title: str | None = None,
        initial_comment: str | None = None,
    ) -> dict[str, Any]:
        """Upload a local file path to Slack."""
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("file_path must be a non-empty string")

        local_path = Path(file_path.strip()).expanduser().resolve()
        if not local_path.exists() or not local_path.is_file():
            raise ValueError("file_path does not exist")

        try:
            file_bytes = local_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Failed to read local file: {exc}") from exc

        result = self._upload_file_bytes(
            filename=local_path.name,
            file_bytes=file_bytes,
            channel=channel,
            title=title,
            initial_comment=initial_comment,
        )
        result["action"] = "upload_local_file"
        result["local_file_path"] = str(local_path)
        try:
            file_info = self._fetch_file_info(result["file_id"])
            self._save_file_record(
                local_file_path=str(local_path),
                file_id=result["file_id"],
                filename=result["file_name"],
                title=result["title"],
                channel=result["channel"],
                channel_id=result["channel_id"],
                permalink=file_info.get("permalink"),
                url_private=file_info.get("url_private"),
            )
            result["permalink"] = file_info.get("permalink")
            result["url_private"] = file_info.get("url_private")
        except Exception:
            pass
        return result

    def _get_mongo_collection(self) -> Any:
        """Return the slack_files MongoDB collection, or None if unavailable."""
        try:
            from pymongo import MongoClient as _MongoClient
        except ImportError:
            return None
        mongo_uri = os.getenv("MONGODB_URI", "").strip()
        if not mongo_uri:
            return None
        try:
            client = _MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            db_name = os.getenv("MONGODB_DATABASE", "").strip()
            if not db_name:
                from urllib.parse import urlparse as _urlparse
                parsed_uri = _urlparse(mongo_uri)
                raw_path = parsed_uri.path.lstrip("/")
                db_name = raw_path.split("?")[0] if raw_path else ""
            if not db_name:
                db_name = "dynamic_exec"
            return client[db_name]["slack_files"]
        except Exception:
            return None

    def _fetch_file_info(self, file_id: str) -> dict[str, Any]:
        """Call files.info and return the file object dict."""
        if not isinstance(file_id, str) or not file_id.strip():
            raise ValueError("file_id must be a non-empty string")
        req = request.Request(
            f"https://slack.com/api/files.info?file={file_id}",
            headers={"Authorization": f"Bearer {self.bot_token}"},
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"Slack files.info HTTP error {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise ValueError(f"Failed to reach Slack files.info: {exc.reason}") from exc
        parsed = self._parse_slack_response(body)
        return parsed.get("file") if isinstance(parsed.get("file"), dict) else {}

    def _save_file_record(
        self,
        local_file_path: str,
        file_id: str,
        filename: str,
        title: str,
        channel: str,
        channel_id: str,
        permalink: str | None,
        url_private: str | None,
    ) -> None:
        """Upsert a file upload record in the MongoDB slack_files collection."""
        collection = self._get_mongo_collection()
        if collection is None:
            return
        from datetime import datetime as _datetime
        record: dict[str, Any] = {
            "local_file_path": local_file_path,
            "file_id": file_id,
            "filename": filename,
            "title": title,
            "channel": channel,
            "channel_id": channel_id,
            "permalink": permalink,
            "url_private": url_private,
            "uploaded_at": _datetime.utcnow().isoformat(),
        }
        try:
            collection.update_one(
                {"local_file_path": local_file_path},
                {"$set": record},
                upsert=True,
            )
        except Exception:
            pass

    def get_file(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Retrieve a file by its original local path.

        If the file exists locally, returns immediately.
        Otherwise, queries MongoDB for the Slack upload record, downloads the
        file from Slack (url_private), and writes it to the original path.

        Args: {"path": "/original/local/path/to/file.pdf"}
        """
        if not isinstance(args, dict):
            raise ValueError("args must be a dict")
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")

        local_path = Path(path.strip()).expanduser().resolve()

        if local_path.exists() and local_path.is_file():
            return {
                "status": "success",
                "path": str(local_path),
                "source": "local",
                "message": "File found locally",
            }

        collection = self._get_mongo_collection()
        if collection is None:
            raise ValueError(
                "File does not exist locally and MongoDB is not available for lookup"
            )

        record = collection.find_one({"local_file_path": str(local_path)})
        if record is None:
            record = collection.find_one({"local_file_path": path.strip()})
        if record is None:
            raise ValueError(f"No file record found for path: {local_path}")

        url_private = record.get("url_private")
        if not isinstance(url_private, str) or not url_private.strip():
            raise ValueError(
                "File record found in MongoDB but has no url_private for Slack download"
            )

        dl_req = request.Request(
            url_private,
            headers={"Authorization": f"Bearer {self.bot_token}"},
            method="GET",
        )
        try:
            with request.urlopen(dl_req, timeout=60) as response:
                file_bytes = response.read()
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(
                f"Failed to download file from Slack: HTTP {exc.code}: {body}"
            ) from exc
        except error.URLError as exc:
            raise ValueError(
                f"Failed to download file from Slack: {exc.reason}"
            ) from exc

        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(file_bytes)
        except OSError as exc:
            raise ValueError(f"Failed to write file to {local_path}: {exc}") from exc

        return {
            "status": "success",
            "path": str(local_path),
            "source": "slack",
            "file_id": record.get("file_id"),
            "permalink": record.get("permalink"),
            "message": "File retrieved from Slack and written to original path",
        }
