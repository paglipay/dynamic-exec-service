from __future__ import annotations

from plugins.integrations.openai_plugin import OpenAIFunctionCallingPlugin


def test_gmail_send_email_tool_description_mentions_attachments() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)

    description = plugin._build_tool_description(
        "plugins.integrations.gmail_plugin",
        "GmailPlugin",
        "send_email",
    )

    assert "attachments_or_null" in description
    assert "generated_data/notes.txt" in description


def test_system_prompt_requires_attachment_count_verification() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._slack_images_root = "generated_data/slack_downloads/images"

    prompt = plugin._build_system_prompt()

    assert "attachment_count > 0" in prompt
