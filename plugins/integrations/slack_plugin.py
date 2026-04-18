"""Slack integration plugin for posting messages to channels."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


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
        # Map modal_view fields to _open_modal_form signature
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
        return self._open_modal_form(form)

    def request_modal_with_button(self, args: dict[str, Any]) -> dict[str, Any]:
        """Post a message with a button to trigger a modal. Args: channel, button_text, message_text, callback_id, modal_view (optional)."""
        import traceback
        logger.debug("request_modal_with_button called with args: %s", args)
        channel = args.get("channel", self.default_channel)
        button_text = args.get("button_text", "Open Modal")
        message_text = args.get("message_text", "Click the button to open a modal.")
        callback_id = args.get("callback_id", "open_modal_button")
        modal_view = args.get("modal_view")
        logger.debug("channel=%s button_text=%s message_text=%s callback_id=%s", channel, button_text, message_text, callback_id)
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel must be a non-empty string")
        if not isinstance(button_text, str) or not button_text.strip():
            raise ValueError("button_text must be a non-empty string")
        if not isinstance(message_text, str) or not message_text.strip():
            raise ValueError("message_text must be a non-empty string")
        if not isinstance(callback_id, str) or not callback_id.strip():
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
                    logger.warning("Failed to connect to Redis: %s", exc)
                    redis_client = None
            import uuid
            modal_id = str(uuid.uuid4())
            if redis_client:
                try:
                    redis_client.set(f"slack:modal_view:{modal_id}", json.dumps(modal_view), ex=86400)
                    button_value = f"modalview:{modal_id}"
                    redis_key = modal_id
                except Exception as exc:
                    logger.warning("Failed to store modal_view in Redis: %s", exc)
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
        logger.debug("Sending chat.postMessage payload: %s", json.dumps(payload))
        try:
            response = self._post_json(self.api_url, payload)
        except Exception as exc:
            logger.error("Exception in _post_json: %s", exc, exc_info=True)
            return {
                "status": "error",
                "action": "request_modal_with_button",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "message": "Failed to post message with modal trigger button",
            }
        logger.debug("chat.postMessage response: %s", response)
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
        logger.debug("_post_json called: api_url=%s token_prefix=%s...", api_url, self.bot_token[:8])
        logger.debug("_post_json payload: %s", json.dumps(payload))
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
            logger.error("_post_json HTTPError: %s %s", exc.code, body)
            raise ValueError(f"Slack HTTP error {exc.code}: {body}") from exc
        except error.URLError as exc:
            logger.error("_post_json URLError: %s", exc.reason)
            raise ValueError(f"Failed to reach Slack API: {exc.reason}") from exc
        logger.debug("_post_json response body: %s", body)
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

    def post_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a message to a Slack channel.

        For plain text replies, pass only channel and text.
        For rich interactive content (buttons, inputs, sections), also pass blocks —
        a list of Slack Block Kit block objects.

        Args:
            channel: Channel name (e.g. "#general") or ID (e.g. "C123ABC").
            text:    Fallback text shown in notifications and plain-text clients.
            blocks:  Optional list of Block Kit block dicts for rich formatting.
        """
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel must be a non-empty string")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")

        payload: dict[str, Any] = {"channel": channel.strip(), "text": text.strip()}
        if blocks is not None:
            payload["blocks"] = self._validate_blocks(blocks)

        parsed = self._post_json(self.api_url, payload)

        return {
            "status": "success",
            "channel": parsed.get("channel", channel.strip()),
            "ts": parsed.get("ts"),
            "message": "Message sent to Slack",
        }

    def _open_modal_form(
        self,
        form: dict[str, Any],
    ) -> dict[str, Any]:
        """Internal: open a Slack modal from a flat form dict. Use open_modal() instead."""
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
            "action": "open_modal",
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

    def upload_content(
        self,
        filename: str,
        content: str,
        channel: str | None = None,
        title: str | None = None,
        initial_comment: str | None = None,
    ) -> dict[str, Any]:
        """Upload generated text content as a file to Slack.

        Use this when you have a string (a report, markdown, JSON, etc.) that you
        want to post as a file.  The content is encoded to UTF-8 bytes and uploaded
        directly — no file needs to exist on disk first.

        For uploading a file that already exists on disk, use upload_local_file instead.

        Args:
            filename:        Name to give the file in Slack (e.g. "report.md").
            content:         The text content of the file.
            channel:         Target channel name or ID. Defaults to the plugin's default_channel.
            title:           Display title in Slack. Defaults to filename.
            initial_comment: Optional message to accompany the file.
        """
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
        except Exception as exc:
            logger.warning(
                "upload_content: failed to fetch file info or save MongoDB record "
                "for %s (file_id=%s): %s",
                filename,
                result.get("file_id"),
                exc,
            )
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

        exif_dict = self._extract_exif_dict(file_bytes, local_path.suffix)

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
            exif_dict=exif_dict,
        )
        if exif_dict:
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
        except Exception as exc:
            logger.warning(
                "upload_local_file: failed to fetch file info or update permalink/url_private "
                "for %s (file_id=%s): %s — file was uploaded but download recovery may not work",
                local_path,
                result.get("file_id"),
                exc,
            )

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
            # Resolve DB name: explicit env var wins, then URI path, then logged error.
            db_name = os.getenv("MONGODB_DATABASE", "").strip()
            if not db_name:
                from urllib.parse import urlparse as _urlparse
                parsed_uri = _urlparse(mongo_uri)
                raw_path = parsed_uri.path.lstrip("/")
                db_name = raw_path.split("?")[0].strip() if raw_path else ""
            if not db_name:
                logger.error(
                    "MONGODB_DATABASE is not set and could not be extracted from MONGODB_URI. "
                    "Set MONGODB_DATABASE in your .env to ensure slack_files records go to the "
                    "correct database. Falling back to 'dynamic_exec' — records may be lost."
                )
                db_name = "dynamic_exec"
            return client[db_name]["slack_files"]
        except Exception as exc:
            logger.warning("SlackPlugin: failed to connect to MongoDB: %s", exc)
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
    def _extract_exif_dict(file_bytes: bytes, suffix: str) -> dict | None:
        """Extract EXIF as a JSON-serializable dict from a JPEG file.
        Returns None for non-JPEG files or when no EXIF is present.
        """
        if suffix.lower() not in (".jpg", ".jpeg"):
            return None
        try:
            import piexif as _piexif
            exif_dict = _piexif.load(file_bytes)
            # Only store if there's at least one non-empty IFD
            if not any(exif_dict.get(ifd) for ifd in ("0th", "Exif", "GPS", "1st")):
                return None
            # Convert bytes to strings for JSON serialization
            def decode_bytes(obj):
                if isinstance(obj, dict):
                    return {k: decode_bytes(v) for k, v in obj.items()}
                if isinstance(obj, (list, tuple)):
                    return [decode_bytes(x) for x in obj]
                if isinstance(obj, bytes):
                    try:
                        return obj.decode("utf-8", errors="replace")
                    except Exception:
                        return str(obj)
                return obj
            return {k: decode_bytes(v) for k, v in exif_dict.items()}
        except Exception:
            return None

    @staticmethod
    def _extract_exif_b64(file_bytes: bytes, suffix: str) -> str | None:
        """Return the raw EXIF block from a JPEG as a base64 string, or None."""
        if suffix.lower() not in (".jpg", ".jpeg"):
            return None
        try:
            import base64 as _b64
            import piexif as _piexif
            exif_dict = _piexif.load(file_bytes)
            if not any(exif_dict.get(ifd) for ifd in ("0th", "Exif", "GPS", "1st")):
                return None
            return _b64.b64encode(_piexif.dump(exif_dict)).decode("ascii")
        except Exception:
            return None

    @staticmethod
    def _exif_dict_mongo_to_b64(mongo_exif: dict[str, Any]) -> str | None:
        """Reconstruct exif_b64 from a MongoDB repr-format exif_dict.

        MongoDB stores EXIF as {ifd_name: {str(tag_id): repr(value)}} written by
        _extract_exif_full().  This method reverses that encoding:
          1. Parses each repr value back to its original Python type via ast.literal_eval
             (handles bytes literals, tuples, ints correctly).
          2. Converts string keys back to integer tag IDs.
          3. Calls piexif.dump() to produce the binary EXIF block.
          4. Returns it base64-encoded, ready for _reembed_exif().

        Returns None if conversion fails or produces no usable EXIF.
        """
        try:
            import ast as _ast
            import base64 as _b64
            import piexif as _piexif

            converted: dict[str, dict[int, Any]] = {}
            for ifd_name, tags in mongo_exif.items():
                if not isinstance(tags, dict):
                    continue
                parsed_ifd: dict[int, Any] = {}
                for key_str, val_repr in tags.items():
                    try:
                        parsed_ifd[int(key_str)] = _ast.literal_eval(str(val_repr))
                    except Exception:
                        pass
                if parsed_ifd:
                    converted[ifd_name] = parsed_ifd

            if not converted:
                return None

            # piexif.dump expects all standard IFD keys even if empty
            for ifd in ("0th", "Exif", "GPS", "1st"):
                if ifd not in converted:
                    converted[ifd] = {}

            exif_bytes = _piexif.dump(converted)
            if not exif_bytes:
                return None
            return _b64.b64encode(exif_bytes).decode("ascii")
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

    # ------------------------------------------------------------------
    # media_files redesign — unified intake, relative paths, typed EXIF
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_exif_intake(
        file_bytes: bytes,
        suffix: str,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Extract EXIF once from raw bytes and return both forms needed for storage.

        Returns:
            exif_b64  — raw EXIF block as base64; use with _reembed_exif to restore
                        a downloaded file after Slack strips EXIF.
            exif      — clean typed dict with human-readable fields for querying/display:
                        {make, model, datetime_original, orientation,
                         gps: {lat, lon, alt_m, direction, direction_ref}}
        Call this ONCE on the original bytes before saving to disk or uploading to Slack.
        """
        if suffix.lower() not in (".jpg", ".jpeg"):
            return None, None
        try:
            import base64 as _b64
            import piexif as _piexif

            raw_exif = _piexif.load(file_bytes)
            # Only proceed if there is at least one non-empty IFD
            if not any(raw_exif.get(ifd) for ifd in ("0th", "Exif", "GPS", "1st")):
                return None, None

            exif_b64 = _b64.b64encode(_piexif.dump(raw_exif)).decode("ascii")

            def _r2f(v: Any) -> float | None:
                """Rational tuple → float."""
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    try:
                        return float(v[0]) / float(v[1]) if v[1] else None
                    except Exception:
                        return None
                return None

            def _dms(dms: Any, ref: Any) -> float | None:
                if not isinstance(dms, (list, tuple)) or len(dms) != 3:
                    return None
                d, m, s = _r2f(dms[0]), _r2f(dms[1]), _r2f(dms[2])
                if None in (d, m, s):
                    return None
                val = d + m / 60 + s / 3600
                ref_s = ref.decode("ascii").upper() if isinstance(ref, bytes) else str(ref).upper()
                return round(-val if ref_s in ("S", "W") else val, 8)

            def _dec(v: Any) -> str | None:
                if isinstance(v, bytes):
                    return v.decode("utf-8", errors="replace").strip().rstrip("\x00") or None
                return str(v).strip() or None if v is not None else None

            ifd0 = raw_exif.get("0th", {})
            exif_ifd = raw_exif.get("Exif", {})
            gps_ifd = raw_exif.get("GPS", {})

            lat = _dms(
                gps_ifd.get(_piexif.GPSIFD.GPSLatitude),
                gps_ifd.get(_piexif.GPSIFD.GPSLatitudeRef, b"N"),
            )
            lon = _dms(
                gps_ifd.get(_piexif.GPSIFD.GPSLongitude),
                gps_ifd.get(_piexif.GPSIFD.GPSLongitudeRef, b"E"),
            )
            alt_r = _r2f(gps_ifd.get(_piexif.GPSIFD.GPSAltitude))
            alt_ref = gps_ifd.get(_piexif.GPSIFD.GPSAltitudeRef, 0)
            alt_m = round((-alt_r if alt_ref == 1 else alt_r), 2) if alt_r is not None else None
            dir_r = _r2f(gps_ifd.get(_piexif.GPSIFD.GPSImgDirection))
            dir_ref = gps_ifd.get(_piexif.GPSIFD.GPSImgDirectionRef)

            gps: dict[str, Any] | None = None
            if lat is not None and lon is not None:
                gps = {
                    "lat": lat,
                    "lon": lon,
                    "alt_m": alt_m,
                    "direction": round(dir_r, 2) if dir_r is not None else None,
                    "direction_ref": _dec(dir_ref),
                    "google_maps_url": f"https://maps.google.com/?q={lat},{lon}",
                }

            orientation = ifd0.get(_piexif.ImageIFD.Orientation)
            exif_typed: dict[str, Any] = {
                "make": _dec(ifd0.get(_piexif.ImageIFD.Make)),
                "model": _dec(ifd0.get(_piexif.ImageIFD.Model)),
                "software": _dec(ifd0.get(_piexif.ImageIFD.Software)),
                "datetime_original": _dec(exif_ifd.get(_piexif.ExifIFD.DateTimeOriginal)),
                "orientation": orientation,
                "gps": gps,
            }

            return exif_b64, exif_typed

        except Exception:
            return None, None

    @staticmethod
    def _to_relative_path(full_path: str | Path, base_dir: str | Path) -> str:
        """Convert an absolute path to a relative path from base_dir.

        Returns the relative path string (e.g. 'slack_downloads/photo.jpg').
        If path is already relative or cannot be made relative, returns as-is.
        """
        try:
            return str(Path(full_path).resolve().relative_to(Path(base_dir).resolve()))
        except ValueError:
            # Not under base_dir — return the filename only as a safe fallback
            return Path(full_path).name

    def _get_media_collection(self) -> Any:
        """Return the media_files MongoDB collection, or None if unavailable."""
        try:
            from pymongo import MongoClient as _MC
            uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
            db_name = os.environ.get("MONGODB_DB", "slack_bot")
            client = _MC(uri, serverSelectionTimeoutMS=3000)
            return client[db_name]["media_files"]
        except Exception as exc:
            logger.warning("_get_media_collection: unavailable: %s", exc)
            return None

    def intake_media(
        self,
        raw_bytes: bytes,
        filename: str,
        base_dir: str,
        channel: str | None = None,
        url_private: str | None = None,
        slack_file_id: str | None = None,
        permalink: str | None = None,
        subfolder: str = "slack_downloads",
        source: str = "slack_share",
    ) -> dict[str, Any]:
        """Unified media intake — save file, extract EXIF, write media_files record.

        This is the single entry point for all incoming files regardless of
        whether they arrive via a Slack file_share event or the app's upload
        endpoint. Call it once with the raw bytes before anything else.

        Args:
            raw_bytes:      Raw file bytes (pre-Slack-upload; EXIF intact).
            filename:       Original filename (e.g. 'Soto_test00E.jpg').
            base_dir:       Root data directory (e.g. 'generated_data').
            channel:        Slack channel ID or name.
            url_private:    Slack url_private for later re-download.
            slack_file_id:  Slack file_id.
            permalink:      Slack permalink.
            subfolder:      Subdirectory under base_dir (default 'slack_downloads').
            source:         'slack_share' | 'upload' | other.

        Returns dict with: relative_path, full_path, filename, exif, exif_b64, gps,
            file_hash, already_existed (bool).
        """
        import hashlib
        from datetime import datetime as _dt

        if not isinstance(raw_bytes, bytes) or not raw_bytes:
            raise ValueError("raw_bytes must be non-empty bytes")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("filename must be a non-empty string")

        base = Path(base_dir).resolve()
        target_dir = base / subfolder
        target_dir.mkdir(parents=True, exist_ok=True)

        # Sanitise filename
        safe_name = re.sub(r"[^\w.\-]", "_", filename.strip())
        suffix = Path(safe_name).suffix.lower()
        full_path = target_dir / safe_name

        # Deduplication by SHA-256
        file_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
        collection = self._get_media_collection()
        already_existed = False

        if collection is not None:
            existing = collection.find_one({"file_hash": file_hash})
            if existing:
                already_existed = True
                stored_rel = existing.get("relative_path", "")
                stored_full = (base / stored_rel).resolve() if stored_rel else full_path
                return {
                    "status": "success",
                    "already_existed": True,
                    "relative_path": stored_rel,
                    "full_path": str(stored_full),
                    "filename": existing.get("filename", filename),
                    "exif": existing.get("exif"),
                    "exif_b64": existing.get("exif_b64"),
                    "gps": existing.get("exif", {}).get("gps") if existing.get("exif") else None,
                    "file_hash": file_hash,
                }

        # Extract EXIF from raw bytes NOW — before anything else
        exif_b64, exif_typed = self._extract_exif_intake(raw_bytes, suffix)
        gps = exif_typed.get("gps") if exif_typed else None

        # Write file to disk
        try:
            full_path.write_bytes(raw_bytes)
        except OSError as exc:
            raise ValueError(f"Failed to write {full_path}: {exc}") from exc

        relative_path = self._to_relative_path(full_path, base)

        # Write media_files record synchronously
        if collection is not None:
            now = _dt.utcnow().isoformat()
            record: dict[str, Any] = {
                "file_hash": file_hash,
                "filename": filename,
                "relative_path": relative_path,
                "suffix": suffix,
                "size_bytes": len(raw_bytes),
                "source": source,
                "uploaded_at": now,
            }
            if channel is not None:
                record["channel"] = channel
            if url_private is not None:
                record["url_private"] = url_private
            if slack_file_id is not None:
                record["slack_file_id"] = slack_file_id
            if permalink is not None:
                record["permalink"] = permalink
            if exif_b64 is not None:
                record["exif_b64"] = exif_b64
            if exif_typed is not None:
                record["exif"] = exif_typed
            try:
                collection.update_one(
                    {"file_hash": file_hash},
                    {"$set": record},
                    upsert=True,
                )
            except Exception as exc:
                logger.warning("intake_media: failed to write media_files record: %s", exc)

        return {
            "status": "success",
            "already_existed": already_existed,
            "relative_path": relative_path,
            "full_path": str(full_path),
            "filename": filename,
            "exif": exif_typed,
            "exif_b64": exif_b64,
            "gps": gps,
            "file_hash": file_hash,
        }

    def update_media_slack_fields(
        self,
        file_hash: str,
        slack_file_id: str | None = None,
        url_private: str | None = None,
        permalink: str | None = None,
    ) -> dict[str, Any]:
        """Update the Slack-specific fields on a media_files record after upload.

        Call this after uploading to Slack to store the file_id, url_private,
        and permalink returned by the Slack API.
        """
        collection = self._get_media_collection()
        if collection is None:
            return {"status": "error", "message": "media_files collection unavailable"}
        updates: dict[str, Any] = {}
        if slack_file_id is not None:
            updates["slack_file_id"] = slack_file_id
        if url_private is not None:
            updates["url_private"] = url_private
        if permalink is not None:
            updates["permalink"] = permalink
        if not updates:
            return {"status": "success", "message": "nothing to update"}
        try:
            result = collection.update_one(
                {"file_hash": file_hash},
                {"$set": updates},
            )
            return {
                "status": "success",
                "matched": result.matched_count,
                "modified": result.modified_count,
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def migrate_slack_files(
        self,
        base_dir: str = "generated_data",
    ) -> dict[str, Any]:
        """Migrate existing slack_files records into the new media_files collection.

        For each slack_files record:
          - Converts absolute local_file_path to a relative_path from base_dir
          - Computes file_hash from bytes on disk (if file exists)
          - Promotes exif_b64 and extracts typed exif if missing
          - Writes a media_files record (skips if file_hash already present)

        Safe to run multiple times — uses file_hash for deduplication.
        """
        import hashlib
        from datetime import datetime as _dt

        old_col = self._get_mongo_collection()
        new_col = self._get_media_collection()
        if old_col is None or new_col is None:
            raise ValueError("MongoDB unavailable — cannot migrate")

        base = Path(base_dir).resolve()
        migrated = 0
        skipped_no_file = 0
        skipped_duplicate = 0
        errors = 0

        try:
            records = list(old_col.find({}))
        except Exception as exc:
            raise ValueError(f"Failed to read slack_files: {exc}") from exc

        for rec in records:
            stored_path = rec.get("local_file_path", "")
            if not stored_path:
                errors += 1
                continue

            local_path = Path(stored_path).expanduser().resolve()
            if not local_path.exists() or not local_path.is_file():
                skipped_no_file += 1
                continue

            try:
                raw_bytes = local_path.read_bytes()
                file_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

                if new_col.find_one({"file_hash": file_hash}):
                    skipped_duplicate += 1
                    continue

                relative_path = self._to_relative_path(local_path, base)

                # Promote or extract EXIF — priority order:
                # 1. Extract from raw bytes on disk (most reliable)
                # 2. Existing exif_b64 from record
                # 3. Reconstruct from repr-format exif_dict via ast + piexif.dump
                exif_b64, exif_typed = self._extract_exif_intake(raw_bytes, local_path.suffix)
                if not exif_b64:
                    exif_b64 = rec.get("exif_b64") or self._exif_dict_mongo_to_b64(
                        rec.get("exif_dict") or {}
                    )

                new_rec: dict[str, Any] = {
                    "file_hash": file_hash,
                    "filename": rec.get("filename", local_path.name),
                    "relative_path": relative_path,
                    "suffix": local_path.suffix.lower(),
                    "size_bytes": len(raw_bytes),
                    "source": "migrated_from_slack_files",
                    "channel": rec.get("channel"),
                    "url_private": rec.get("url_private"),
                    "slack_file_id": rec.get("file_id"),
                    "permalink": rec.get("permalink"),
                    "uploaded_at": rec.get("uploaded_at", _dt.utcnow().isoformat()),
                    "migrated_at": _dt.utcnow().isoformat(),
                }
                if exif_b64:
                    new_rec["exif_b64"] = exif_b64
                if exif_typed:
                    new_rec["exif"] = exif_typed

                new_col.update_one(
                    {"file_hash": file_hash},
                    {"$set": new_rec},
                    upsert=True,
                )
                migrated += 1

            except Exception as exc:
                logger.warning("migrate_slack_files: error on %s: %s", stored_path, exc)
                errors += 1

        return {
            "status": "success",
            "migrated": migrated,
            "skipped_not_on_disk": skipped_no_file,
            "skipped_duplicate": skipped_duplicate,
            "errors": errors,
            "message": (
                f"Migration complete. {migrated} record(s) written to media_files. "
                f"{skipped_no_file} skipped (file not on disk). "
                f"{skipped_duplicate} already in media_files."
            ),
        }

    def _save_file_record(
        self,
        local_file_path: str,
        file_id: str | None,
        filename: str,
        title: str,
        channel: str,
        channel_id: str | None,
        permalink: str | None,
        url_private: str | None,
        exif_dict: dict | None = None,
        exif_b64: str | None = None,
        gps: dict | None = None,
    ) -> None:
        """Upsert a file upload record in the MongoDB slack_files collection."""
        collection = self._get_mongo_collection()
        if collection is None:
            return
        from datetime import datetime as _datetime
        record: dict[str, Any] = {
            "local_file_path": local_file_path,
            "filename": filename,
            "title": title,
            "channel": channel,
            "uploaded_at": _datetime.utcnow().isoformat(),
        }
        if file_id is not None:
            record["file_id"] = file_id
        if channel_id is not None:
            record["channel_id"] = channel_id
        if permalink is not None:
            record["permalink"] = permalink
        if url_private is not None:
            record["url_private"] = url_private
        if exif_dict is not None:
            record["exif_dict"] = exif_dict
        if exif_b64 is not None:
            record["exif_b64"] = exif_b64
        if gps is not None:
            record["gps"] = gps
        try:
            collection.update_one(
                {"local_file_path": local_file_path},
                {"$set": record},
                upsert=True,
            )
        except Exception as exc:
            logger.warning(
                "_save_file_record: failed to upsert slack_files record for %s: %s",
                local_file_path,
                exc,
            )

    def get_file(self, args: dict[str, Any]) -> dict[str, Any]:
        """
        Retrieve a file by its original local path, or by filename (+ optional channel).

        If the file exists locally, returns immediately.
        Otherwise, queries MongoDB for the Slack upload record, downloads the
        file from Slack (url_private), and writes it to the original path.

        Args (one of the following):
          {"path": "/original/local/path/to/file.pdf"}
          {"filename": "Soto_test00C.jpg"}
          {"filename": "Soto_test00C.jpg", "channel": "C123ABC"}
        """
        if not isinstance(args, dict):
            raise ValueError("args must be a dict")
        path = args.get("path")
        filename = args.get("filename")
        channel = args.get("channel")

        # Require at least one of path or filename
        has_path = isinstance(path, str) and path.strip()
        has_filename = isinstance(filename, str) and filename.strip()
        if not has_path and not has_filename:
            raise ValueError("args must include either 'path' or 'filename'")

        # --- Path-based lookup (original behaviour) ---
        if has_path:
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
        else:
            # --- Filename (+ optional channel) lookup ---
            collection = self._get_mongo_collection()
            if collection is None:
                raise ValueError(
                    "MongoDB is not available for filename-based lookup"
                )

            query: dict[str, Any] = {"filename": filename.strip()}
            if isinstance(channel, str) and channel.strip():
                query["channel"] = channel.strip()

            record = collection.find_one(query, sort=[("uploaded_at", -1)])

            if record is None:
                raise ValueError(
                    f"No file record found for filename={filename!r}"
                    + (f" channel={channel!r}" if channel else "")
                )

            # Derive local_path from the stored record
            stored_path = record.get("local_file_path", "")
            local_path = Path(stored_path).expanduser().resolve() if stored_path else None

            # If the file is already on disk at its recorded location, return it
            if local_path and local_path.exists() and local_path.is_file():
                return {
                    "status": "success",
                    "path": str(local_path),
                    "source": "local",
                    "message": "File found locally",
                }

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
        filename = args.get("filename")

        # Accept either path or filename — at least one is required
        if not (isinstance(path, str) and path.strip()) and not (isinstance(filename, str) and filename.strip()):
            raise ValueError("args must include either 'path' or 'filename'")

        try:
            import base64 as _b64
            import piexif as _piexif
        except ImportError:
            raise ValueError("piexif is required for EXIF parsing")

        local_path: Path | None = None
        exif_dict: dict[str, Any] | None = None
        loaded_from_disk = False
        record: dict[str, Any] | None = None

        # If a path was given, try loading EXIF from disk first
        if isinstance(path, str) and path.strip():
            local_path = Path(path.strip()).expanduser().resolve()
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
                if local_path is not None:
                    # Path-based lookup
                    record = collection.find_one({"local_file_path": str(local_path)})
                    if record is None:
                        record = collection.find_one({"local_file_path": path.strip() if path else ""})
                else:
                    # Filename-based lookup — most recent matching record
                    record = collection.find_one(
                        {"filename": filename.strip()},
                        sort=[("uploaded_at", -1)],
                    )
                    if record is not None:
                        stored_path = record.get("local_file_path", "")
                        if stored_path:
                            local_path = Path(stored_path).expanduser().resolve()
                if record and isinstance(record.get("exif_b64"), str):
                    try:
                        raw = _b64.b64decode(record["exif_b64"])
                        exif_dict = _piexif.load(raw)
                    except Exception:
                        exif_dict = None

                # Fallback: MongoDB has exif_dict (repr-format from _extract_exif_full)
                # but no exif_b64. Convert repr strings back to Python values via ast.
                if exif_dict is None and record and isinstance(record.get("exif_dict"), dict):
                    try:
                        import ast as _ast
                        converted: dict[str, dict[int, Any]] = {}
                        for ifd_name, tags in record["exif_dict"].items():
                            if not isinstance(tags, dict):
                                continue
                            parsed_ifd: dict[int, Any] = {}
                            for key_str, val_repr in tags.items():
                                try:
                                    parsed_ifd[int(key_str)] = _ast.literal_eval(str(val_repr))
                                except Exception:
                                    pass
                            if parsed_ifd:
                                converted[ifd_name] = parsed_ifd
                        if converted:
                            exif_dict = converted
                    except Exception:
                        pass

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
                "path": str(local_path) if local_path is not None else None,
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
            "path": str(local_path) if local_path is not None else None,
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

    def sync_files(
        self,
        query: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Query slack_files records and ensure every matched file is on local disk.

        For each record this method:
          1. Checks whether the file already exists at its recorded local path.
          2. If not, downloads it from Slack using the stored url_private.
          3. If the file is a JPEG and the record has an exif_b64 blob, re-embeds
             the original EXIF into the downloaded file so metadata is restored.

        This collapses the find → get_file → EXIF-restore loop into a single call.
        The returned file list is ready to pass directly to zip_files or any other
        tool that works with local paths.

        Args:
            query: MongoDB filter dict applied to slack_files (e.g.
                   {"channel": "C123ABC"} or {"filename": {"$regex": "\\.jpg$"}}).
                   Pass None or {} to match all records.
            limit: Maximum number of records to process (default 50).

        Returns a dict with:
            "files"   — list of result dicts, each with:
                          "path"          local file path (ready to use)
                          "filename"      original filename
                          "source"        "local" | "slack" | "error"
                          "exif_restored" True if EXIF was re-embedded
                          "error"         error message when source is "error"
            "total"      number of records matched
            "on_disk"    count already on disk
            "downloaded" count fetched from Slack
            "exif_restored" count of files that had EXIF re-embedded
            "errors"     count of files that could not be retrieved
        """
        base_dir = os.environ.get("BASE_DATA_DIR", "generated_data")
        base = Path(base_dir).resolve()

        # Prefer media_files (new design); fall back to slack_files for legacy records
        media_col = self._get_media_collection()
        legacy_col = self._get_mongo_collection()

        effective_query: dict[str, Any] = query if isinstance(query, dict) else {}

        records: list[dict[str, Any]] = []
        using_media = False
        if media_col is not None:
            try:
                media_records = list(
                    media_col.find(effective_query).sort("uploaded_at", -1).limit(limit)
                )
                if media_records:
                    records = media_records
                    using_media = True
            except Exception:
                pass

        if not records and legacy_col is not None:
            try:
                records = list(
                    legacy_col.find(effective_query).sort("uploaded_at", -1).limit(limit)
                )
            except Exception as exc:
                raise ValueError(f"Failed to query slack_files: {exc}") from exc

        if not records and media_col is None and legacy_col is None:
            raise ValueError("MongoDB is not available for file lookup")

        files: list[dict[str, Any]] = []
        count_on_disk = 0
        count_downloaded = 0
        count_exif_restored = 0
        count_errors = 0

        for rec in records:
            # Resolve path — media_files uses relative_path, slack_files uses local_file_path
            if using_media and rec.get("relative_path"):
                local_path = (base / rec["relative_path"]).resolve()
                stored_path = str(local_path)
            else:
                stored_path = rec.get("local_file_path", "")
                local_path = Path(stored_path).expanduser().resolve() if stored_path else None

            filename = rec.get("filename", local_path.name if local_path else "unknown")
            url_private = rec.get("url_private", "")
            exif_b64 = rec.get("exif_b64", "")

            if local_path is None:
                files.append({
                    "path": None,
                    "filename": filename,
                    "source": "error",
                    "exif_restored": False,
                    "error": "Record has no path",
                })
                count_errors += 1
                continue

            # --- Step 1: ensure file is on disk ---
            source = "local"
            if local_path.exists() and local_path.is_file():
                count_on_disk += 1
            elif isinstance(url_private, str) and url_private.strip():
                # Download from Slack
                dl_req = request.Request(
                    url_private,
                    headers={"Authorization": f"Bearer {self.bot_token}"},
                    method="GET",
                )
                try:
                    with request.urlopen(dl_req, timeout=60) as resp:
                        file_bytes = resp.read()
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(file_bytes)
                    source = "slack"
                    count_downloaded += 1
                    logger.debug("sync_files: downloaded %s from Slack", filename)
                except (error.HTTPError, error.URLError, OSError) as exc:
                    logger.warning("sync_files: failed to download %s: %s", filename, exc)
                    files.append({
                        "path": None,
                        "filename": filename,
                        "source": "error",
                        "exif_restored": False,
                        "error": str(exc),
                    })
                    count_errors += 1
                    continue
            else:
                files.append({
                    "path": None,
                    "filename": filename,
                    "source": "error",
                    "exif_restored": False,
                    "error": "File not on disk and no url_private for download",
                })
                count_errors += 1
                continue

            # --- Step 2: backfill exif_b64 if the record lacks it ---
            # Priority order:
            #   a) File is on disk (local) and has EXIF → extract from raw bytes (best quality)
            #   b) Record has exif_dict (repr format) → reconstruct via ast.literal_eval + piexif.dump
            # In either case, write back to MongoDB so future syncs skip this step.
            active_col = media_col if using_media else legacy_col
            if not exif_b64 and local_path.suffix.lower() in (".jpg", ".jpeg"):
                _new_exif_b64: str | None = None
                try:
                    if source == "local":
                        # File was already on disk — extract directly (Slack hasn't touched it)
                        _raw_bytes = local_path.read_bytes()
                        _new_exif_b64 = self._extract_exif_b64(_raw_bytes, local_path.suffix)
                    if not _new_exif_b64 and isinstance(rec.get("exif_dict"), dict):
                        # Fall back: reconstruct from repr-format exif_dict stored in MongoDB
                        _new_exif_b64 = self._exif_dict_mongo_to_b64(rec["exif_dict"])
                    if _new_exif_b64 and active_col is not None:
                        active_col.update_one(
                            {"_id": rec["_id"]},
                            {"$set": {"exif_b64": _new_exif_b64}},
                        )
                        exif_b64 = _new_exif_b64
                        logger.debug("sync_files: backfilled exif_b64 for %s (source=%s)", filename, source)
                except Exception as exc:
                    logger.warning("sync_files: exif_b64 backfill failed for %s: %s", filename, exc)

            # --- Step 3: re-embed EXIF into JPEG if we have the blob ---
            exif_restored = False
            if (
                isinstance(exif_b64, str)
                and exif_b64
                and local_path.suffix.lower() in (".jpg", ".jpeg")
            ):
                try:
                    self._reembed_exif(local_path, exif_b64)
                    exif_restored = True
                    count_exif_restored += 1
                    logger.debug("sync_files: EXIF re-embedded into %s", filename)
                except Exception as exc:
                    logger.warning("sync_files: EXIF re-embed failed for %s: %s", filename, exc)

            files.append({
                "path": str(local_path),
                "filename": filename,
                "source": source,
                "exif_restored": exif_restored,
            })

        return {
            "status": "success",
            "files": files,
            "total": len(records),
            "on_disk": count_on_disk,
            "downloaded": count_downloaded,
            "exif_restored": count_exif_restored,
            "errors": count_errors,
            "message": (
                f"{len(records)} record(s) processed: "
                f"{count_on_disk} already local, "
                f"{count_downloaded} downloaded from Slack, "
                f"{count_exif_restored} EXIF restored, "
                f"{count_errors} error(s)."
            ),
        }
