"""Slack integration plugin for posting messages to channels."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request


class SlackPlugin:
    """Post messages to Slack using the chat.postMessage Web API."""

    def __init__(
        self,
        bot_token: str,
        default_channel: str = "#general",
        api_url: str = "https://slack.com/api/chat.postMessage",
    ) -> None:
        if not isinstance(bot_token, str) or not bot_token.strip():
            raise ValueError("bot_token must be a non-empty string")
        if not isinstance(default_channel, str) or not default_channel.strip():
            raise ValueError("default_channel must be a non-empty string")
        if api_url != "https://slack.com/api/chat.postMessage":
            raise ValueError("api_url must be https://slack.com/api/chat.postMessage")

        self.bot_token = bot_token.strip()
        self.default_channel = default_channel.strip()
        self.api_url = api_url

    def post_message(self, channel: str, text: str) -> dict[str, Any]:
        """Send a plain text message to a Slack channel."""
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel must be a non-empty string")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")

        payload = {"channel": channel.strip(), "text": text.strip()}
        data = json.dumps(payload).encode("utf-8")

        req = request.Request(
            self.api_url,
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

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("Slack API returned invalid JSON") from exc

        if not isinstance(parsed, dict):
            raise ValueError("Slack API returned unexpected response type")

        if not parsed.get("ok", False):
            slack_error = parsed.get("error", "unknown_error")
            raise ValueError(f"Slack API error: {slack_error}")

        return {
            "status": "success",
            "channel": parsed.get("channel", channel.strip()),
            "ts": parsed.get("ts"),
            "message": "Message sent to Slack",
        }
