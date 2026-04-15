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

        exif_b64 = self._extract_exif_b64(file_bytes, local_path.suffix)

        result = self._upload_file_bytes(
            filename=local_path.name,
            file_bytes=file_bytes,
            channel=channel,
            title=title,
            initial_comment=initial_comment,
        )
        result["action"] = "upload_local_file"
        result["local_file_path"] = str(local_path)

        # Write the core MongoDB record immediately after a successful Slack upload.
        # This guarantees EXIF and file identity are always persisted, independent of
        # whether the subsequent files.info call succeeds.
        self._save_file_record(
            local_file_path=str(local_path),
            file_id=result["file_id"],
            filename=result["file_name"],
            title=result["title"],
            channel=result["channel"],
            channel_id=result["channel_id"],
            permalink=None,
            url_private=None,
            exif_b64=exif_b64,
        )
        if exif_b64:
            result["exif_preserved"] = True

        # Fetch permalink / url_private and update the record with them separately.
        try:
            file_info = self._fetch_file_info(result["file_id"])
            permalink = file_info.get("permalink")
            url_private = file_info.get("url_private")
            if permalink or url_private:
                collection = self._get_mongo_collection()
                if collection is not None:
                    collection.update_one(
                        {"local_file_path": str(local_path)},
                        {"$set": {"permalink": permalink, "url_private": url_private}},
                    )
            result["permalink"] = permalink
            result["url_private"] = url_private
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

    @staticmethod
    def _extract_exif_b64(file_bytes: bytes, suffix: str) -> str | None:
        """Extract the raw EXIF block from a JPEG and return it as a base64 string.

        Returns None for non-JPEG files or when no EXIF is present.
        """
        if suffix.lower() not in (".jpg", ".jpeg"):
            return None
        try:
            import base64 as _b64
            import piexif as _piexif
            exif_dict = _piexif.load(file_bytes)
            # Only store if there's at least one non-empty IFD
            if not any(exif_dict.get(ifd) for ifd in ("0th", "Exif", "GPS", "1st")):
                return None
            return _b64.b64encode(_piexif.dump(exif_dict)).decode("ascii")
        except Exception:
            return None

    @staticmethod
    def _reembed_exif(file_path: Path, exif_b64: str) -> None:
        """Re-insert the stored EXIF block into a JPEG file on disk."""
        if file_path.suffix.lower() not in (".jpg", ".jpeg"):
            return
        try:
            import base64 as _b64
            import piexif as _piexif
            exif_bytes = _b64.b64decode(exif_b64)
            _piexif.insert(exif_bytes, file_path.read_bytes(), str(file_path))
        except Exception:
            pass

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
        exif_b64: str | None = None,
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
        if exif_b64 is not None:
            record["exif_b64"] = exif_b64
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

        # Re-embed the original EXIF block if one was saved (Slack strips EXIF on upload)
        exif_b64 = record.get("exif_b64")
        exif_restored = False
        if isinstance(exif_b64, str) and exif_b64:
            self._reembed_exif(local_path, exif_b64)
            exif_restored = True

        return {
            "status": "success",
            "path": str(local_path),
            "source": "slack",
            "file_id": record.get("file_id"),
            "permalink": record.get("permalink"),
            "exif_restored": exif_restored,
            "message": "File retrieved from Slack and written to original path",
        }

    def get_file_exif(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Return parsed EXIF data for a file — from local disk or from the
        MongoDB slack_files record (exif_b64 field) if the file is gone.

        Args: {"path": "/path/to/image.jpg"}

        Returns human-readable fields including:
          - orientation (int tag + plain label, e.g. "Rotate 90 CW")
          - gps_latitude / gps_longitude as decimal floats
          - gps_lat_ref / gps_lon_ref (N/S, E/W)
          - gps_altitude (metres)
          - gps_img_direction (compass bearing 0-360)
          - gps_img_direction_ref ("T" = True North, "M" = Magnetic)
          - google_maps_url  (when GPS coords are available)
          - make, model, datetime_original, software
        """
        if not isinstance(args, dict):
            raise ValueError("args must be a dict")
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")

        try:
            import base64 as _b64
            import piexif as _piexif
        except ImportError:
            raise ValueError("piexif is required for EXIF parsing")

        local_path = Path(path.strip()).expanduser().resolve()
        exif_dict: dict[str, Any] | None = None
        loaded_from_disk = False

        # Try loading from the local file first
        if local_path.exists() and local_path.is_file():
            try:
                exif_dict = _piexif.load(str(local_path))
                loaded_from_disk = True
            except Exception:
                exif_dict = None

        # Fall back to the base64 blob stored in MongoDB
        if exif_dict is None:
            collection = self._get_mongo_collection()
            if collection is not None:
                record = collection.find_one({"local_file_path": str(local_path)})
                if record is None:
                    record = collection.find_one({"local_file_path": path.strip()})
                if record and isinstance(record.get("exif_b64"), str):
                    try:
                        raw = _b64.b64decode(record["exif_b64"])
                        exif_dict = _piexif.load(raw)
                    except Exception:
                        exif_dict = None

        # If EXIF was read from disk and MongoDB record is missing exif_b64, backfill it now
        if loaded_from_disk and exif_dict is not None:
            try:
                collection = self._get_mongo_collection()
                if collection is not None:
                    existing = collection.find_one(
                        {"local_file_path": str(local_path)},
                        {"_id": 1, "exif_b64": 1},
                    )
                    if existing is not None and not existing.get("exif_b64"):
                        exif_b64 = self._extract_exif_b64(
                            local_path.read_bytes(), local_path.suffix
                        )
                        if exif_b64:
                            collection.update_one(
                                {"local_file_path": str(local_path)},
                                {"$set": {"exif_b64": exif_b64}},
                            )
            except Exception:
                pass

        if exif_dict is None:
            return {
                "status": "success",
                "path": str(local_path),
                "exif": None,
                "message": "No EXIF data found for this file",
            }

        def _rational_to_float(value: Any) -> float | None:
            """Convert piexif rational (numerator, denominator) to float."""
            if isinstance(value, (list, tuple)) and len(value) == 2:
                num, den = value
                return float(num) / float(den) if den else None
            return None

        def _dms_to_decimal(dms: Any, ref: bytes | str) -> float | None:
            """Convert DMS rational tuple list to signed decimal degrees."""
            if not isinstance(dms, (list, tuple)) or len(dms) != 3:
                return None
            try:
                d = _rational_to_float(dms[0])
                m = _rational_to_float(dms[1])
                s = _rational_to_float(dms[2])
                if d is None or m is None or s is None:
                    return None
                decimal = d + m / 60.0 + s / 3600.0
                ref_str = ref.decode("ascii") if isinstance(ref, bytes) else str(ref)
                if ref_str.upper() in ("S", "W"):
                    decimal = -decimal
                return round(decimal, 8)
            except Exception:
                return None

        _ORIENTATION_LABELS = {
            1: "Normal",
            2: "Mirror horizontal",
            3: "Rotate 180",
            4: "Mirror vertical",
            5: "Mirror horizontal, Rotate 270 CW",
            6: "Rotate 90 CW",
            7: "Mirror horizontal, Rotate 90 CW",
            8: "Rotate 270 CW",
        }

        ifd0 = exif_dict.get("0th", {})
        exif_ifd = exif_dict.get("Exif", {})
        gps_ifd = exif_dict.get("GPS", {})

        # --- Orientation ---
        orientation_tag = ifd0.get(_piexif.ImageIFD.Orientation)
        orientation_label = _ORIENTATION_LABELS.get(orientation_tag)

        # --- Basic metadata ---
        def _decode(val: Any) -> str | None:
            if isinstance(val, bytes):
                return val.decode("utf-8", errors="replace").strip().rstrip("\x00")
            return str(val) if val is not None else None

        make = _decode(ifd0.get(_piexif.ImageIFD.Make))
        model = _decode(ifd0.get(_piexif.ImageIFD.Model))
        software = _decode(ifd0.get(_piexif.ImageIFD.Software))
        datetime_original = _decode(exif_ifd.get(_piexif.ExifIFD.DateTimeOriginal))

        # --- GPS ---
        lat_raw = gps_ifd.get(_piexif.GPSIFD.GPSLatitude)
        lat_ref = gps_ifd.get(_piexif.GPSIFD.GPSLatitudeRef)
        lon_raw = gps_ifd.get(_piexif.GPSIFD.GPSLongitude)
        lon_ref = gps_ifd.get(_piexif.GPSIFD.GPSLongitudeRef)
        alt_raw = gps_ifd.get(_piexif.GPSIFD.GPSAltitude)
        alt_ref = gps_ifd.get(_piexif.GPSIFD.GPSAltitudeRef)  # 0=above, 1=below sea level
        direction_raw = gps_ifd.get(_piexif.GPSIFD.GPSImgDirection)
        direction_ref = gps_ifd.get(_piexif.GPSIFD.GPSImgDirectionRef)

        lat = _dms_to_decimal(lat_raw, lat_ref or b"N") if lat_raw else None
        lon = _dms_to_decimal(lon_raw, lon_ref or b"E") if lon_raw else None
        alt = _rational_to_float(alt_raw)
        if alt is not None and alt_ref == 1:
            alt = -alt
        direction = _rational_to_float(direction_raw)
        direction_ref_str = direction_ref.decode("ascii").strip() if isinstance(direction_ref, bytes) else None

        maps_url: str | None = None
        if lat is not None and lon is not None:
            maps_url = f"https://maps.google.com/?q={lat},{lon}"

        result: dict[str, Any] = {
            "status": "success",
            "path": str(local_path),
            "exif": {
                "orientation": orientation_tag,
                "orientation_label": orientation_label,
                "make": make,
                "model": model,
                "software": software,
                "datetime_original": datetime_original,
                "gps_latitude": lat,
                "gps_latitude_ref": _decode(lat_ref),
                "gps_longitude": lon,
                "gps_longitude_ref": _decode(lon_ref),
                "gps_altitude_m": round(alt, 2) if alt is not None else None,
                "gps_img_direction": round(direction, 2) if direction is not None else None,
                "gps_img_direction_ref": direction_ref_str,
                "google_maps_url": maps_url,
            },
        }
        return result

    def backfill_exif(self, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Retroactively extract and save EXIF data into MongoDB for all slack_files
        records that are missing the exif_b64 field AND whose local file is still
        on disk.

        Safe to call multiple times — skips records that already have exif_b64.

        Args: {} (no arguments required)
        Returns: counts of updated, skipped, and failed records.
        """
        collection = self._get_mongo_collection()
        if collection is None:
            raise ValueError("MongoDB is not available")

        updated = 0
        skipped_no_file = 0
        skipped_no_exif = 0
        skipped_already_set = 0
        failed = 0

        try:
            cursor = collection.find(
                {"exif_b64": {"$exists": False}},
                {"_id": 1, "local_file_path": 1},
            )
            records = list(cursor)
        except Exception as exc:
            raise ValueError(f"Failed to query MongoDB: {exc}") from exc

        for rec in records:
            local_file_path = rec.get("local_file_path", "")
            file_path = Path(local_file_path)
            if not file_path.exists() or not file_path.is_file():
                skipped_no_file += 1
                continue
            try:
                file_bytes = file_path.read_bytes()
                exif_b64 = self._extract_exif_b64(file_bytes, file_path.suffix)
                if exif_b64 is None:
                    skipped_no_exif += 1
                    continue
                collection.update_one(
                    {"_id": rec["_id"]},
                    {"$set": {"exif_b64": exif_b64}},
                )
                updated += 1
            except Exception:
                failed += 1

        return {
            "status": "success",
            "updated": updated,
            "skipped_file_not_on_disk": skipped_no_file,
            "skipped_no_exif_in_file": skipped_no_exif,
            "failed": failed,
            "message": f"Backfill complete. {updated} record(s) updated.",
        }
