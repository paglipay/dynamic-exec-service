from __future__ import annotations

import hashlib
import hmac
import json
import time

import app as app_module
from plugins.integrations.slack_plugin import SlackPlugin


def _signed_form_payload(payload: dict[str, object], secret: str) -> tuple[dict[str, str], dict[str, str]]:
    raw_body = f"payload={json.dumps(payload, separators=(',', ':'))}"
    timestamp = str(int(time.time()))
    basestring = f"v0:{timestamp}:{raw_body}".encode("utf-8")
    signature = "v0=" + hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    headers = {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    form_data = {"payload": json.dumps(payload, separators=(",", ":"))}
    return headers, form_data


def test_extract_view_submission_values_flattens_common_input_types() -> None:
    values = {
        "name_block": {
            "name_input": {"type": "plain_text_input", "value": "Alice"},
        },
        "role_block": {
            "role_select": {
                "type": "static_select",
                "selected_option": {"text": {"type": "plain_text", "text": "Admin"}, "value": "admin"},
            }
        },
        "people_block": {
            "reviewers": {
                "type": "multi_users_select",
                "selected_users": ["U123", "U456"],
            }
        },
    }

    extracted = SlackPlugin.extract_view_submission_values(values)

    assert extracted == {
        "name_block.name_input": "Alice",
        "role_block.role_select": "admin",
        "people_block.reviewers": ["U123", "U456"],
    }


def test_slack_interactivity_stores_view_submission(client) -> None:
    app_module.signing_secret = "test-signing-secret"
    app_module._slack_form_submissions.clear()

    payload = {
        "type": "view_submission",
        "api_app_id": "A123",
        "team": {"id": "T123"},
        "user": {"id": "U123", "username": "alice"},
        "view": {
            "id": "V123",
            "callback_id": "device_request_form",
            "private_metadata": "ticket-42",
            "state": {
                "values": {
                    "summary_block": {
                        "summary_input": {"type": "plain_text_input", "value": "Need two laptops"}
                    }
                }
            },
        },
    }
    headers, form_data = _signed_form_payload(payload, app_module.signing_secret)

    response = client.post("/slack/interactivity", data=form_data, headers=headers)

    assert response.status_code == 200
    assert response.get_json() == {"response_action": "clear"}

    list_response = client.get("/slack/form-submissions?limit=5")
    assert list_response.status_code == 200
    body = list_response.get_json()
    assert body["status"] == "success"
    assert body["count"] == 1
    assert body["submissions"][0]["callback_id"] == "device_request_form"
    assert body["submissions"][0]["values"] == {
        "summary_block.summary_input": "Need two laptops"
    }


def test_slack_interactivity_rejects_invalid_signature(client) -> None:
    app_module.signing_secret = "test-signing-secret"
    payload = {"type": "view_submission", "view": {"state": {"values": {}}}}
    headers, form_data = _signed_form_payload(payload, "wrong-secret")

    response = client.post("/slack/interactivity", data=form_data, headers=headers)

    assert response.status_code == 401
    assert response.get_json() == {"status": "error", "message": "Invalid Slack signature"}