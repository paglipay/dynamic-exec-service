from __future__ import annotations

import base64
from email import message_from_bytes, policy

import pytest

from plugins.integrations.gmail_plugin import GmailPlugin


class _ExecuteResult:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeMessagesAPI:
    def __init__(self):
        self.last_send_body = None

    def list(self, **kwargs):
        return _ExecuteResult(
            {
                "messages": [
                    {"id": "m-1", "threadId": "t-1"},
                    {"id": "m-2", "threadId": "t-2"},
                ],
                "resultSizeEstimate": 2,
            }
        )

    def get(self, **kwargs):
        message_id = kwargs.get("id")
        return _ExecuteResult(
            {
                "id": message_id,
                "threadId": f"thread-{message_id}",
                "snippet": f"snippet-{message_id}",
                "internalDate": "1700000000000",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "alice@example.com"},
                        {"name": "To", "value": "bob@example.com"},
                        {"name": "Subject", "value": f"subject-{message_id}"},
                        {"name": "Date", "value": "Sat, 01 Mar 2026 10:00:00 +0000"},
                    ]
                },
            }
        )

    def send(self, **kwargs):
        self.last_send_body = kwargs.get("body")
        return _ExecuteResult(
            {
                "id": "sent-1",
                "threadId": "thread-sent-1",
                "labelIds": ["SENT"],
            }
        )


class _FakeUsersAPI:
    def __init__(self):
        self.messages_api = _FakeMessagesAPI()

    def messages(self):
        return self.messages_api

    def getProfile(self, **kwargs):
        return _ExecuteResult(
            {
                "emailAddress": "owner@example.com",
                "messagesTotal": 42,
                "threadsTotal": 24,
                "historyId": "abc123",
            }
        )


class _FakeService:
    def __init__(self):
        self.users_api = _FakeUsersAPI()

    def users(self):
        return self.users_api


@pytest.fixture
def plugin() -> GmailPlugin:
    gmail_plugin = GmailPlugin(service=_FakeService())
    gmail_plugin._import_google_dependencies = lambda: (None, None, None, None, Exception)
    return gmail_plugin


def test_get_profile_returns_summary(plugin: GmailPlugin) -> None:
    result = plugin.get_profile()
    assert result["status"] == "success"
    assert result["email_address"] == "owner@example.com"
    assert result["messages_total"] == 42


def test_list_messages_returns_concise_message_data(plugin: GmailPlugin) -> None:
    result = plugin.list_messages(query="subject:demo", max_results=10)
    assert result["status"] == "success"
    assert result["count"] == 2
    assert result["messages"][0]["subject"] == "subject-m-1"


def test_send_email_encodes_raw_message(plugin: GmailPlugin) -> None:
    result = plugin.send_email(
        to="receiver@example.com",
        subject="Test",
        body_text="Hello from test",
        cc=["team@example.com"],
    )

    assert result["status"] == "success"

    raw = plugin.service.users().messages_api.last_send_body["raw"]
    decoded = base64.urlsafe_b64decode(raw.encode("utf-8")).decode("utf-8")
    assert "To: receiver@example.com" in decoded
    assert "Cc: team@example.com" in decoded
    assert "Subject: Test" in decoded


def test_send_email_with_attachment(plugin: GmailPlugin, tmp_path) -> None:
    attachment = tmp_path / "notes.txt"
    attachment.write_text("Attachment content", encoding="utf-8")

    result = plugin.send_email(
        to="receiver@example.com",
        subject="Attachment test",
        body_text="See attached",
        attachments=[str(attachment)],
    )

    assert result["status"] == "success"
    assert result["attachment_count"] == 1

    raw = plugin.service.users().messages_api.last_send_body["raw"]
    decoded_bytes = base64.urlsafe_b64decode(raw.encode("utf-8"))
    parsed = message_from_bytes(decoded_bytes, policy=policy.default)
    attachments = list(parsed.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "notes.txt"


def test_list_messages_validates_max_results(plugin: GmailPlugin) -> None:
    with pytest.raises(ValueError, match="max_results"):
        plugin.list_messages(max_results=0)
