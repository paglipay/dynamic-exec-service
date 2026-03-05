from __future__ import annotations

import app as app_module


def test_duplicate_event_uses_redis_atomic_set_nx() -> None:
    original_client = app_module._slack_dedupe_redis_client
    original_seen = dict(app_module._processed_slack_events)

    class FakeRedis:
        def __init__(self) -> None:
            self.keys: set[str] = set()

        def set(self, key: str, _value: str, ex: int | None = None, nx: bool | None = None):
            assert ex == app_module.SLACK_EVENT_TTL_SECONDS
            assert nx is True
            if key in self.keys:
                return False
            self.keys.add(key)
            return True

    try:
        app_module._processed_slack_events.clear()
        app_module._slack_dedupe_redis_client = FakeRedis()

        event_data = {"event_id": "evt-1"}
        event = {"channel": "C1", "user": "U1", "ts": "1", "text": "hello"}

        assert app_module._is_duplicate_slack_event(event_data, event) is False
        assert app_module._is_duplicate_slack_event(event_data, event) is True
    finally:
        app_module._slack_dedupe_redis_client = original_client
        app_module._processed_slack_events.clear()
        app_module._processed_slack_events.update(original_seen)


def test_duplicate_event_falls_back_to_memory_when_redis_fails() -> None:
    original_client = app_module._slack_dedupe_redis_client
    original_seen = dict(app_module._processed_slack_events)

    class FakeRedisError:
        def set(self, *_args, **_kwargs):
            raise RuntimeError("redis unavailable")

    try:
        app_module._processed_slack_events.clear()
        app_module._slack_dedupe_redis_client = FakeRedisError()

        event_data = {"event_id": "evt-2"}
        event = {"channel": "C2", "user": "U2", "ts": "2", "text": "world"}

        assert app_module._is_duplicate_slack_event(event_data, event) is False
        assert app_module._is_duplicate_slack_event(event_data, event) is True
    finally:
        app_module._slack_dedupe_redis_client = original_client
        app_module._processed_slack_events.clear()
        app_module._processed_slack_events.update(original_seen)


def test_duplicate_event_uses_fingerprint_when_event_id_missing() -> None:
    original_client = app_module._slack_dedupe_redis_client
    original_seen = dict(app_module._processed_slack_events)

    try:
        app_module._processed_slack_events.clear()
        app_module._slack_dedupe_redis_client = None

        event_data = {}
        event = {"channel": "C3", "user": "U3", "ts": "3", "text": "fallback-id"}

        assert app_module._is_duplicate_slack_event(event_data, event) is False
        assert app_module._is_duplicate_slack_event(event_data, event) is True
    finally:
        app_module._slack_dedupe_redis_client = original_client
        app_module._processed_slack_events.clear()
        app_module._processed_slack_events.update(original_seen)
