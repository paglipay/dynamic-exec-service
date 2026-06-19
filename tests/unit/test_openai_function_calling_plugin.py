from __future__ import annotations

import json
import os

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


def test_tool_mapping_includes_openai_sdk_generate_image_only() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)

    mapping = plugin._build_tool_mapping()
    targets = set(mapping.values())

    assert (
        "plugins.integrations.openai_sdk_plugin",
        "OpenAISDKPlugin",
        "generate_image",
    ) in targets
    assert (
        "plugins.integrations.openai_sdk_plugin",
        "OpenAISDKPlugin",
        "generate_text",
    ) not in targets
    assert (
        "plugins.integrations.openai_sdk_plugin",
        "OpenAISDKPlugin",
        "generate_text_with_history",
    ) not in targets
    assert (
        "plugins.integrations.openai_sdk_plugin",
        "OpenAISDKPlugin",
        "reply_with_plugins",
    ) not in targets


def test_generate_image_tool_description_mentions_arg_order() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)

    description = plugin._build_tool_description(
        "plugins.integrations.openai_sdk_plugin",
        "OpenAISDKPlugin",
        "generate_image",
    )

    assert "exact order" in description
    assert "gpt-image-1" in description


def test_mongodb_find_documents_tool_description_mentions_arg_order() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)

    description = plugin._build_tool_description(
        "plugins.mongodb_plugin",
        "MongoDBPlugin",
        "find_documents",
    )

    assert "exact order" in description
    assert "sort_or_null" in description


def test_mongodb_text_search_tool_description_mentions_index_requirement() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)

    description = plugin._build_tool_description(
        "plugins.mongodb_plugin",
        "MongoDBPlugin",
        "text_search",
    )

    assert "text index" in description
    assert "create_text_index" in description


def test_mongodb_update_documents_tool_description_mentions_operation_result() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)

    description = plugin._build_tool_description(
        "plugins.mongodb_plugin",
        "MongoDBPlugin",
        "update_documents",
    )

    assert "exact order" in description
    assert "operation_result" in description
    assert "no_match" in description


def test_system_prompt_requires_mongodb_write_verification() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._slack_images_root = "generated_data/slack_downloads/images"

    prompt = plugin._build_system_prompt()

    assert "inserted_id" in prompt
    assert "operation_result" in prompt


def test_execute_tool_call_returns_error_for_mongodb_write_no_match() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._tool_name_to_target = {
        "plugin_tool_001": (
            "plugins.mongodb_plugin",
            "MongoDBPlugin",
            "update_documents",
        )
    }

    class FakeExecutor:
        @staticmethod
        def instantiate(_module_name: str, _class_name: str, _constructor_args: dict) -> None:
            return None

        @staticmethod
        def call_method(_module_name: str, _method_name: str, _args: list):
            return {
                "status": "success",
                "action": "update_documents",
                "matched_count": 0,
                "modified_count": 0,
                "upserted_id": None,
                "operation_result": "no_match",
            }

    plugin.executor = FakeExecutor()

    output = plugin._execute_tool_call("plugin_tool_001", json.dumps({"constructor_args": {}, "args": []}))
    parsed = json.loads(output)

    assert parsed["status"] == "error"
    assert "matched zero documents" in parsed["message"]


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
    plugin._build_tools = lambda *_args, **_kwargs: []

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

    final_text, executed_tool_calls, analyzed_image_paths = plugin._execute_chat_turn(messages, "gpt-4.1-mini", 1)

    assert final_text == "Hello from assistant"
    assert executed_tool_calls == 0
    assert analyzed_image_paths == []
    assert messages[-1] == {"role": "assistant", "content": "Hello from assistant"}


def test_execute_chat_turn_with_enable_tools_false_skips_tool_definitions() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._tool_name_to_target = {}

    def _fail_build_tools(*_args, **_kwargs):
        raise AssertionError("_build_tools should not be called when enable_tools=False")

    plugin._build_tools = _fail_build_tools

    class FakeMessage:
        content = "Plain answer, no tools needed"
        tool_calls = None

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    captured_kwargs: dict = {}

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            captured_kwargs.update(kwargs)
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    plugin.client = FakeClient()
    messages = [{"role": "user", "content": "hi"}]

    final_text, executed_tool_calls, analyzed_image_paths = plugin._execute_chat_turn(
        messages, "gpt-4.1-mini", 1, enable_tools=False
    )

    assert final_text == "Plain answer, no tools needed"
    assert executed_tool_calls == 0
    assert analyzed_image_paths == []
    assert "tools" not in captured_kwargs
    assert "tool_choice" not in captured_kwargs


class _FakeFunction:
    def __init__(self, name: str) -> None:
        self.name = name
        self.arguments = "{}"


class _FakeToolCall:
    def __init__(self, name: str, call_id: str) -> None:
        self.id = call_id
        self.function = _FakeFunction(name)

    def model_dump(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


class _FakeMessage:
    def __init__(self, content, tool_calls) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message) -> None:
        self.message = message


class _FakeChatResponse:
    def __init__(self, message) -> None:
        self.choices = [_FakeChoice(message)]


def test_execute_chat_turn_forces_finalization_call_when_rounds_exhausted() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._tool_name_to_target = {}
    plugin._build_tools = lambda *_args, **_kwargs: [{"type": "function", "function": {"name": "do_thing"}}]
    plugin._execute_tool_call = lambda _name, _args: json.dumps({"status": "success", "result": "ok"})

    calls: list[dict] = []

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) <= 2:
                return _FakeChatResponse(_FakeMessage("", [_FakeToolCall("do_thing", f"call_{len(calls)}")]))
            return _FakeChatResponse(_FakeMessage("Final answer after forced finalization", None))

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    plugin.client = FakeClient()
    messages = [{"role": "user", "content": "hi"}]

    final_text, executed_tool_calls, analyzed_image_paths = plugin._execute_chat_turn(
        messages, "gpt-4.1-mini", 2
    )

    assert final_text == "Final answer after forced finalization"
    assert executed_tool_calls == 2
    assert analyzed_image_paths == []
    assert len(calls) == 3
    assert "tools" not in calls[-1]
    assert "tool_choice" not in calls[-1]
    assert messages[-1] == {"role": "assistant", "content": "Final answer after forced finalization"}


def test_execute_chat_turn_raises_with_tool_counts_when_finalization_also_empty() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._tool_name_to_target = {}
    plugin._build_tools = lambda *_args, **_kwargs: [{"type": "function", "function": {"name": "do_thing"}}]
    plugin._execute_tool_call = lambda _name, _args: json.dumps({"status": "success", "result": "ok"})

    class FakeCompletions:
        @staticmethod
        def create(**_kwargs):
            return _FakeChatResponse(_FakeMessage("", [_FakeToolCall("do_thing", "call_x")]))

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    plugin.client = FakeClient()
    messages = [{"role": "user", "content": "hi"}]

    try:
        plugin._execute_chat_turn(messages, "gpt-4.1-mini", 1)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Exceeded max tool-calling rounds" in str(exc)
        assert "do_thing" in str(exc)


def test_generate_with_function_calls_rejects_non_bool_enable_tools() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin.default_model = None

    try:
        plugin.generate_with_function_calls("hi", enable_tools="nope")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "enable_tools must be a boolean" in str(exc)


def test_generate_with_function_calls_and_history_rejects_non_bool_enable_tools() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin.default_model = None

    try:
        plugin.generate_with_function_calls_and_history("conv-1", "hi", enable_tools=1)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "enable_tools must be a boolean" in str(exc)


def _with_env(name: str, value: str | None, func):
    """Run func() with env var `name` temporarily set to `value`, then restore it."""
    old_value = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        return func()
    finally:
        if old_value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old_value


def _stub_plugin_for_generate(build_tools_result):
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin.default_model = None
    plugin._tool_name_to_target = {}
    plugin._slack_images_root = "generated_data/slack_downloads/images"
    plugin._build_tools = lambda *_args, **_kwargs: build_tools_result

    class FakeMessage:
        content = "Plain answer"
        tool_calls = None

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    captured_kwargs: dict = {}

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            captured_kwargs.update(kwargs)
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    plugin.client = FakeClient()
    return plugin, captured_kwargs


def test_generate_with_function_calls_falls_back_to_env_var_when_disabled() -> None:
    plugin, captured_kwargs = _stub_plugin_for_generate([{"type": "function", "function": {"name": "dummy"}}])

    result = _with_env(
        "OPENAI_TOOLS_ENABLED", "false", lambda: plugin.generate_with_function_calls("hi there")
    )

    assert result["tools_enabled"] is False
    assert "tools" not in captured_kwargs
    assert "tool_choice" not in captured_kwargs


def test_generate_with_function_calls_explicit_enable_tools_overrides_env_var() -> None:
    plugin, captured_kwargs = _stub_plugin_for_generate([{"type": "function", "function": {"name": "dummy"}}])

    result = _with_env(
        "OPENAI_TOOLS_ENABLED",
        "false",
        lambda: plugin.generate_with_function_calls("hi there", enable_tools=True),
    )

    assert result["tools_enabled"] is True
    assert captured_kwargs.get("tools")
    assert captured_kwargs.get("tool_choice") == "auto"


def test_generate_with_function_calls_defaults_enabled_when_env_var_unset() -> None:
    plugin, captured_kwargs = _stub_plugin_for_generate([{"type": "function", "function": {"name": "dummy"}}])

    result = _with_env("OPENAI_TOOLS_ENABLED", None, lambda: plugin.generate_with_function_calls("hi there"))

    assert result["tools_enabled"] is True
    assert captured_kwargs.get("tools")


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


def test_compaction_drops_orphan_tool_message_from_tail() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._redis_client = None
    plugin._conversation_store = {}
    plugin._history_manager = ConversationHistoryManager(
        max_messages=4,
        keep_last_messages=2,
        max_estimated_tokens=100000,
        summary_max_chars=600,
    )

    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "turn 1 response"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "plugin_tool_001", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "{\"status\":\"success\"}"},
    ]

    # Simulate a bad persisted state where tool response survives but its assistant tool_calls turn is dropped.
    bad_history = [messages[0], messages[1], messages[2], messages[4]]
    plugin._save_conversation_history("conv-orphan-tool", bad_history)
    stored = plugin._load_conversation_history("conv-orphan-tool")

    assert not any(message.get("role") == "tool" for message in stored)


def test_compaction_keeps_valid_assistant_tool_sequence() -> None:
    plugin = OpenAIFunctionCallingPlugin.__new__(OpenAIFunctionCallingPlugin)
    plugin._redis_client = None
    plugin._conversation_store = {}
    plugin._history_manager = ConversationHistoryManager(
        max_messages=5,
        keep_last_messages=2,
        max_estimated_tokens=100000,
        summary_max_chars=600,
    )

    messages = [
        {"role": "system", "content": "System prompt."},
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "older reply"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-2", "function": {"name": "plugin_tool_002", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-2", "content": "{\"status\":\"success\"}"},
    ]

    plugin._save_conversation_history("conv-valid-tool-seq", messages)
    stored = plugin._load_conversation_history("conv-valid-tool-seq")

    assert any(message.get("role") == "assistant" and message.get("tool_calls") for message in stored)
    assert any(message.get("role") == "tool" and message.get("tool_call_id") == "call-2" for message in stored)
