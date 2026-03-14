from __future__ import annotations

import app as app_module


def test_extract_slack_message_text_prefers_event_text() -> None:
    event = {
        "text": "Can you read this?",
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [{"type": "text", "text": "ignored fallback"}],
                    }
                ],
            }
        ],
    }

    assert app_module._extract_slack_message_text(event) == "Can you read this?"


def test_extract_slack_message_text_uses_blocks_when_text_is_empty() -> None:
    event = {
        "text": "",
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {"type": "text", "text": "Site"},
                            {"type": "text", "text": "Loc Code"},
                            {"type": "text", "text": "School Name"},
                        ],
                    },
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {"type": "text", "text": "MANN"},
                            {"type": "text", "text": "UCLA COMM SCH7574"},
                            {"type": "text", "text": "Horace Mann UCLA Community School"},
                        ],
                    },
                ],
            }
        ],
    }

    extracted = app_module._extract_slack_message_text(event)

    assert "Site" in extracted
    assert "Loc Code" in extracted
    assert "MANN" in extracted
    assert "UCLA COMM SCH7574" in extracted


def test_extract_slack_message_text_uses_attachments_when_blocks_are_empty() -> None:
    event = {
        "text": "",
        "attachments": [
            {
                "title": "Pasted Spreadsheet",
                "text": "Site\tLoc Code\tSchool Name",
                "fields": [
                    {"title": "Site", "value": "MANN"},
                    {"title": "Loc Code", "value": "UCLA COMM SCH7574"},
                ],
            }
        ],
    }

    extracted = app_module._extract_slack_message_text(event)

    assert "Pasted Spreadsheet" in extracted
    assert "Site\tLoc Code\tSchool Name" in extracted
    assert "UCLA COMM SCH7574" in extracted


def test_extract_slack_message_text_uses_nested_message_payload() -> None:
    event = {
        "text": "",
        "message": {
            "text": "",
            "attachments": [
                {
                    "fallback": "Site Loc Code School Name",
                    "fields": [{"title": "City", "value": "LOS ANGELES, CA"}],
                }
            ],
        },
    }

    extracted = app_module._extract_slack_message_text(event)

    assert "Site Loc Code School Name" in extracted
    assert "LOS ANGELES, CA" in extracted


def test_unreadable_slack_preview_text_detection() -> None:
    assert app_module._is_unreadable_slack_preview_text("[no preview available]") is True
    assert app_module._is_unreadable_slack_preview_text("No Preview Available") is True
    assert app_module._is_unreadable_slack_preview_text("Site\tLoc Code") is False
