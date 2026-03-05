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


def test_history_storage_falls_back_to_memory_when_redis_unavailable() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._redis_client = None
    plugin._conversation_store = {}

    expected_messages = [{"role": "user", "content": "hello"}]
    plugin._save_conversation_history("conv-1", expected_messages)

    loaded_messages = plugin._load_conversation_history("conv-1")

    assert loaded_messages == expected_messages


def test_history_storage_uses_redis_when_available() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._history_ttl_seconds = 321
    plugin._conversation_redis_prefix = "openai_function_calling:conversation"

    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.calls: list[tuple[str, int, str]] = []

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def setex(self, key: str, ttl: int, value: str) -> None:
            self.calls.append((key, ttl, value))
            self.values[key] = value

    fake_redis = FakeRedis()
    plugin._redis_client = fake_redis

    expected_messages = [{"role": "assistant", "content": "done"}]
    plugin._save_conversation_history("conv-2", expected_messages)
    loaded_messages = plugin._load_conversation_history("conv-2")

    assert fake_redis.calls
    saved_key, saved_ttl, _saved_value = fake_redis.calls[0]
    assert saved_key.endswith(":conv-2")
    assert saved_ttl == 321
    assert loaded_messages == expected_messages


def test_history_storage_returns_empty_for_invalid_redis_json() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)

    class FakeRedisInvalidPayload:
        def get(self, _key: str) -> str:
            return "not-json"

    plugin._redis_client = FakeRedisInvalidPayload()

    loaded_messages = plugin._load_conversation_history("conv-3")

    assert loaded_messages == []
