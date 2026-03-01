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
            raise ValueError("SLACK_BOT_TOKEN must be set in environment or provided as bot_token")
        if not isinstance(default_channel, str) or not default_channel.strip():
            raise ValueError("default_channel must be a non-empty string")
        if api_url != "https://slack.com/api/chat.postMessage":
            raise ValueError("api_url must be https://slack.com/api/chat.postMessage")

        self.bot_token = token.strip()
        self.default_channel = default_channel.strip()
        self.api_url = api_url

    def _resolve_channel_id(self, channel: str) -> str:
        channel_value = channel.strip()
        if re.fullmatch(r"[CGD][A-Z0-9]+", channel_value):
            return channel_value

        target_name = channel_value[1:] if channel_value.startswith("#") else channel_value
        target_name = target_name.strip().lower()
        if not target_name:
            raise ValueError("channel must be a non-empty string")

        cursor = ""
        while True:
            payload: dict[str, Any] = {
                "exclude_archived": "true",
                "limit": "1000",
                "types": "public_channel,private_channel",
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
                raise ValueError(f"Slack API error: {slack_error} ({'; '.join(details)})")
            raise ValueError(f"Slack API error: {slack_error}")

        return parsed

    def _post_json(self, api_url: str, payload: dict[str, Any]) -> dict[str, Any]:
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
            raise ValueError(f"Slack HTTP error {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise ValueError(f"Failed to reach Slack API: {exc.reason}") from exc

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
        if channel is not None and (not isinstance(channel, str) or not channel.strip()):
            raise ValueError("channel must be a non-empty string when provided")
        if title is not None and not isinstance(title, str):
            raise ValueError("title must be a string when provided")
        if initial_comment is not None and not isinstance(initial_comment, str):
            raise ValueError("initial_comment must be a string when provided")

        target_channel = channel.strip() if isinstance(channel, str) and channel.strip() else self.default_channel
        target_channel_id = self._resolve_channel_id(target_channel)
        file_title = title.strip() if isinstance(title, str) and title.strip() else filename.strip()

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
            raise ValueError(f"Failed to upload file to Slack URL: {exc.reason}") from exc

        completion_payload: dict[str, Any] = {
            "files": json.dumps([
                {
                    "id": file_id,
                    "title": file_title,
                }
            ]),
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
            "action": "upload_text_file",
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
        return self._upload_file_bytes(
            filename=filename.strip(),
            file_bytes=content.encode("utf-8"),
            channel=channel,
            title=title,
            initial_comment=initial_comment,
        )

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
        return result
