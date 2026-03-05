from __future__ import annotations

from plugins.integrations.conversation_history_manager import ConversationHistoryManager
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


def test_redis_health_check_reports_memory_fallback() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._redis_client = None
    plugin._conversation_store = {"conv-fallback": [{"role": "user", "content": "test"}]}
    plugin._history_ttl_seconds = 123

    result = plugin.redis_health_check("conv-fallback")

    assert result["status"] == "success"
    assert result["backend"] == "memory"
    assert result["history_messages"] == 1
    assert result["redis_ping"] is None
    assert result["round_trip_ok"] is None


def test_redis_health_check_reports_redis_round_trip_success() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._conversation_redis_prefix = "openai_function_calling:conversation"
    plugin._history_ttl_seconds = 604800

    class FakeRedisHealthy:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def ping(self) -> bool:
            return True

        def setex(self, key: str, _ttl: int, value: str) -> None:
            self.values[key] = value

        def get(self, key: str) -> str | None:
            return self.values.get(key)

        def delete(self, key: str) -> None:
            self.values.pop(key, None)

    plugin._redis_client = FakeRedisHealthy()

    result = plugin.redis_health_check()

    assert result["status"] == "success"
    assert result["backend"] == "redis"
    assert result["redis_ping"] is True
    assert result["round_trip_ok"] is True


def test_redis_health_check_requires_non_empty_conversation_id() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._redis_client = None

    try:
        plugin.redis_health_check("   ")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "conversation_id must be a non-empty string" in str(exc)


def test_execute_chat_turn_appends_final_assistant_message_to_history() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._tool_name_to_target = {}
    plugin._build_tools = lambda: []

    class FakeMessage:
        content = "Hello from assistant"
        tool_calls = None

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        @staticmethod
        def create(**_kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    plugin.client = FakeClient()
    messages = [{"role": "user", "content": "hi"}]

    final_text, executed_tool_calls = plugin._execute_chat_turn(messages, "gpt-4.1-mini", 1)

    assert final_text == "Hello from assistant"
    assert executed_tool_calls == 0
    assert messages[-1] == {"role": "assistant", "content": "Hello from assistant"}


def test_save_history_compacts_and_keeps_recent_messages() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._redis_client = None
    plugin._conversation_store = {}
    plugin._history_manager = ConversationHistoryManager(
        max_messages=6,
        keep_last_messages=2,
        max_estimated_tokens=100000,
        summary_max_chars=600,
    )

    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]

    plugin._save_conversation_history("conv-compact", messages)
    stored = plugin._load_conversation_history("conv-compact")

    assert len(stored) <= 6
    assert stored[0] == {"role": "system", "content": "System prompt."}
    assert stored[1]["role"] == "system"
    assert "Conversation summary (older context):" in stored[1]["content"]
    assert stored[-2] == {"role": "user", "content": "u3"}
    assert stored[-1] == {"role": "assistant", "content": "a3"}


def test_save_history_compacts_for_token_budget_even_if_message_count_small() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._redis_client = None
    plugin._conversation_store = {}
    plugin._history_manager = ConversationHistoryManager(
        max_messages=20,
        keep_last_messages=2,
        max_estimated_tokens=40,
        summary_max_chars=500,
    )

    long_text = "x" * 400
    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "latest question"},
        {"role": "assistant", "content": "latest answer"},
    ]

    plugin._save_conversation_history("conv-token", messages)
    stored = plugin._load_conversation_history("conv-token")

    assert stored[0] == {"role": "system", "content": "System prompt."}
    assert stored[1]["role"] == "system"
    assert "Conversation summary (older context):" in stored[1]["content"]
    assert stored[-2] == {"role": "user", "content": "latest question"}
    assert stored[-1] == {"role": "assistant", "content": "latest answer"}
