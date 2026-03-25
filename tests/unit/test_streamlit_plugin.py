from __future__ import annotations

from pathlib import Path

import pytest

from plugins.system_tools.streamlit_plugin import StreamlitPlugin


class _FakeProcess:
    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._running = True

    def poll(self) -> int | None:
        return None if self._running else self.returncode

    def terminate(self) -> None:
        self._running = False
        self.returncode = 0

    def kill(self) -> None:
        self._running = False
        self.returncode = -9

    def wait(self, timeout: int | None = None) -> int:
        _ = timeout
        if self.returncode is None:
            self.returncode = 0
            self._running = False
        return self.returncode


def test_create_app_file(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))

    result = plugin.create_app_file("apps/demo_app.py", title="Demo App")

    assert result["status"] == "success"
    created = tmp_path / "apps" / "demo_app.py"
    assert created.exists()
    content = created.read_text(encoding="utf-8")
    assert "import streamlit as st" in content
    assert "Demo App" in content


def test_create_app_file_with_submit_form_template(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))

    result = plugin.create_app_file(
        "apps/form_app.py",
        title="Submit Form",
        template="simple_submit_form",
    )

    assert result["status"] == "success"
    assert result["template"] == "simple_submit_form"

    created = tmp_path / "apps" / "form_app.py"
    content = created.read_text(encoding="utf-8")
    assert "with st.form(key=\"simple_submit_form\")" in content
    assert "st.form_submit_button(\"Submit\")" in content


def test_create_app_file_with_named_payload(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))
    description = "Collect user details and include a short project note."

    result = plugin.create_app_file(
        {
            "file_path": "apps/payload_form.py",
            "title": "Payload Submit Form",
            "template": "simple_submit_form",
            "description": description,
        }
    )

    assert result["status"] == "success"
    assert result["template"] == "simple_submit_form"
    assert result["description"] == description
    assert result["used_custom_content"] is False
    created = tmp_path / "apps" / "payload_form.py"
    assert created.exists()
    content = created.read_text(encoding="utf-8")
    assert description in content


def test_create_app_file_with_custom_app_content(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))
    custom_content = (
        "import streamlit as st\n\n"
        "st.set_page_config(page_title='Payload-Defined App')\n"
        "st.title('Payload-Defined App')\n"
        "with st.form(key='payload_form'):\n"
        "    ticket = st.text_input('Ticket ID')\n"
        "    submitted = st.form_submit_button('Submit')\n"
        "if submitted:\n"
        "    st.success(f'Received {ticket}')\n"
    )

    result = plugin.create_app_file(
        {
            "file_path": "apps/custom_payload_app.py",
            "title": "Ignored when app_content used",
            "template": "basic",
            "description": "Custom content from JSON",
            "app_content": custom_content,
        }
    )

    assert result["status"] == "success"
    assert result["used_custom_content"] is True
    created = tmp_path / "apps" / "custom_payload_app.py"
    assert created.exists()
    assert created.read_text(encoding="utf-8") == custom_content


def test_create_app_file_rejects_existing_without_overwrite(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))
    existing = tmp_path / "apps" / "existing.py"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("old", encoding="utf-8")

    with pytest.raises(ValueError, match="file_path already exists"):
        plugin.create_app_file("apps/existing.py", title="New Title")


def test_create_app_file_overwrites_existing_when_enabled(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))
    existing = tmp_path / "apps" / "existing.py"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("old", encoding="utf-8")

    result = plugin.create_app_file(
        {
            "file_path": "apps/existing.py",
            "title": "Overwritten App",
            "template": "basic",
            "overwrite_existing": True,
        }
    )

    assert result["status"] == "success"
    assert result["overwrote_existing"] is True
    assert result["restarted_app"] is False
    content = existing.read_text(encoding="utf-8")
    assert "Overwritten App" in content


def test_create_app_file_overwrite_restarts_running_same_script(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    existing = tmp_path / "apps" / "existing.py"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("old", encoding="utf-8")

    started_processes: list[_FakeProcess] = []

    def _fake_popen(command, cwd=None, stdout=None, stderr=None, text=None):
        _ = command
        _ = cwd
        _ = stdout
        _ = stderr
        _ = text
        process = _FakeProcess(pid=7000 + len(started_processes))
        started_processes.append(process)
        return process

    monkeypatch.setattr("plugins.system_tools.streamlit_plugin.subprocess.Popen", _fake_popen)

    plugin = StreamlitPlugin(base_dir=str(tmp_path))
    original_process = _FakeProcess(pid=6999)
    plugin._process = original_process
    plugin._active_script = str(existing.resolve())

    result = plugin.create_app_file(
        {
            "file_path": "apps/existing.py",
            "title": "Restarted App",
            "template": "basic",
            "overwrite_existing": True,
            "restart_on_overwrite": True,
            "start_port": 8611,
            "start_host": "127.0.0.1",
            "start_headless": True,
        }
    )

    assert result["status"] == "success"
    assert result["overwrote_existing"] is True
    assert result["restarted_app"] is True
    assert "app_start" in result
    assert result["app_start"]["url"] == "http://127.0.0.1:8611"
    assert len(started_processes) == 1
    assert original_process.poll() == 0


def test_create_app_file_rejects_non_boolean_restart_on_overwrite(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))

    with pytest.raises(ValueError, match="restart_on_overwrite must be a boolean"):
        plugin.create_app_file(
            {
                "file_path": "apps/bad_restart_type.py",
                "title": "Bad",
                "template": "basic",
                "restart_on_overwrite": "true",
            }
        )


def test_create_app_file_rejects_non_boolean_overwrite_existing(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))

    with pytest.raises(ValueError, match="overwrite_existing must be a boolean"):
        plugin.create_app_file(
            {
                "file_path": "apps/bad_overwrite_type.py",
                "title": "Bad",
                "template": "basic",
                "overwrite_existing": "true",
            }
        )


def test_create_app_file_rejects_blank_description(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))

    with pytest.raises(ValueError, match="description must be a non-empty string when provided"):
        plugin.create_app_file(
            {
                "file_path": "apps/bad_description.py",
                "title": "Bad",
                "template": "basic",
                "description": "   ",
            }
        )


def test_create_app_file_rejects_blank_app_content(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))

    with pytest.raises(ValueError, match="app_content must be a non-empty string when provided"):
        plugin.create_app_file(
            {
                "file_path": "apps/bad_app_content.py",
                "title": "Bad",
                "template": "basic",
                "app_content": "   ",
            }
        )


def test_create_app_file_with_named_payload_and_auto_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def _fake_popen(command, cwd=None, stdout=None, stderr=None, text=None):
        captured["command"] = command
        captured["cwd"] = cwd
        return _FakeProcess(pid=5656)

    monkeypatch.setattr("plugins.system_tools.streamlit_plugin.subprocess.Popen", _fake_popen)

    plugin = StreamlitPlugin(base_dir=str(tmp_path))
    result = plugin.create_app_file(
        {
            "file_path": "apps/auto_start_form.py",
            "title": "Auto Start Form",
            "template": "simple_submit_form",
            "auto_start": True,
            "start_port": 8601,
            "start_host": "127.0.0.1",
            "start_headless": True,
        }
    )

    assert result["status"] == "success"
    assert "app_start" in result
    assert result["app_start"]["pid"] == 5656
    assert result["app_start"]["url"] == "http://127.0.0.1:8601"
    assert captured["cwd"] == str(tmp_path / "apps")


def test_create_app_file_rejects_non_boolean_auto_start(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path))

    with pytest.raises(ValueError, match="auto_start must be a boolean"):
        plugin.create_app_file(
            {
                "file_path": "apps/bad_payload.py",
                "title": "Bad",
                "template": "basic",
                "auto_start": "yes",
            }
        )


def test_start_status_and_stop_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script_file = tmp_path / "app.py"
    script_file.write_text("print('hello')\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def _fake_popen(command, cwd=None, stdout=None, stderr=None, text=None):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        captured["text"] = text
        return _FakeProcess(pid=4242)

    monkeypatch.setattr("plugins.system_tools.streamlit_plugin.subprocess.Popen", _fake_popen)

    plugin = StreamlitPlugin(base_dir=str(tmp_path), host="127.0.0.1", port=8501)
    start_result = plugin.start_app("app.py")

    assert start_result["status"] == "success"
    assert start_result["pid"] == 4242
    assert start_result["url"] == "http://127.0.0.1:8501"
    assert isinstance(captured["command"], list)
    assert captured["cwd"] == str(tmp_path)

    status_result = plugin.status()
    assert status_result["status"] == "success"
    assert status_result["running"] is True
    assert status_result["pid"] == 4242

    stop_result = plugin.stop_app()
    assert stop_result["status"] == "success"
    assert stop_result["stopped"] is True

    status_after_stop = plugin.status()
    assert status_after_stop["running"] is False


def test_reject_start_outside_base_dir(tmp_path: Path) -> None:
    plugin = StreamlitPlugin(base_dir=str(tmp_path), allow_outside_base_dir=False)
    outside_file = tmp_path.parent / "outside_app.py"
    outside_file.write_text("print('x')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inside base_dir"):
        plugin.start_app(str(outside_file))