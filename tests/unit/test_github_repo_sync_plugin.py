from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib import error

import pytest

from plugins.integrations.github_repo_sync_plugin import GitHubRepoSyncPlugin


class _FakeHTTPResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = exc_type
        _ = exc
        _ = tb


def test_rejects_file_path_outside_allowed_prefix() -> None:
    plugin = GitHubRepoSyncPlugin(
        token="token",
        repo_owner="owner",
        repo_name="repo",
        branch="main",
        allowed_path_prefix="deploy/heroku",
    )

    with pytest.raises(ValueError, match="outside allowed_path_prefix"):
        plugin.upsert_text_file("plugins/system_tools/streamlit_plugin.py", "content")


def test_rejects_repo_owner_with_spaces() -> None:
    with pytest.raises(ValueError, match="repo_owner must not contain spaces"):
        GitHubRepoSyncPlugin(
            token="token",
            repo_owner="Paul Aglipay",
            repo_name="dynamic-exec-service-streamlit-app",
            branch="main",
            allowed_path_prefix="deploy/heroku",
        )


def test_allows_upsert_to_root_procfile(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):
        _ = timeout
        method = req.get_method()
        if method == "GET":
            raise error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)
        body = req.data.decode("utf-8") if isinstance(req.data, bytes) else "{}"
        captured["payload"] = json.loads(body)
        return _FakeHTTPResponse(
            {
                "commit": {"sha": "procfile-sha", "html_url": "https://github.com/owner/repo/commit/procfile-sha"},
                "content": {"html_url": "https://github.com/owner/repo/blob/main/Procfile"},
            }
        )

    monkeypatch.setattr("plugins.integrations.github_repo_sync_plugin.request.urlopen", _fake_urlopen)

    plugin = GitHubRepoSyncPlugin(
        token="token",
        repo_owner="owner",
        repo_name="repo",
    )
    result = plugin.upsert_text_file("Procfile", "web: streamlit run app.py\n")

    assert result["status"] == "success"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["message"] == "Update file from dynamic-exec-service"


def test_commit_streamlit_app_creates_new_file(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):
        _ = timeout
        method = req.get_method()
        url = req.full_url

        if method == "GET":
            raise error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)

        assert method == "PUT"
        body = req.data.decode("utf-8") if isinstance(req.data, bytes) else "{}"
        payload = json.loads(body)
        captured["payload"] = payload
        return _FakeHTTPResponse(
            {
                "commit": {
                    "sha": "abc123",
                    "html_url": "https://github.com/owner/repo/commit/abc123",
                },
                "content": {
                    "html_url": "https://github.com/owner/repo/blob/main/deploy/heroku/streamlit_app.py",
                },
            }
        )

    monkeypatch.setattr("plugins.integrations.github_repo_sync_plugin.request.urlopen", _fake_urlopen)

    plugin = GitHubRepoSyncPlugin(
        token="token",
        repo_owner="owner",
        repo_name="repo",
        branch="main",
        allowed_path_prefix="deploy/heroku",
    )

    result = plugin.commit_streamlit_app(
        app_content="import streamlit as st\nst.title('Hi')\n",
        file_path="deploy/heroku/streamlit_app.py",
        commit_message="Update Streamlit app",
    )

    assert result["status"] == "success"
    assert result["operation"] == "create"
    assert result["commit_sha"] == "abc123"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    decoded_content = base64.b64decode(payload["content"]).decode("utf-8")
    assert "st.title('Hi')" in decoded_content
    assert payload["branch"] == "main"
    assert "sha" not in payload


def test_upsert_includes_sha_for_existing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):
        _ = timeout
        method = req.get_method()
        if method == "GET":
            return _FakeHTTPResponse({"sha": "existing-sha"})

        body = req.data.decode("utf-8") if isinstance(req.data, bytes) else "{}"
        payload = json.loads(body)
        captured["payload"] = payload
        return _FakeHTTPResponse(
            {
                "commit": {"sha": "next-sha", "html_url": "https://github.com/owner/repo/commit/next-sha"},
                "content": {"html_url": "https://github.com/owner/repo/blob/main/deploy/heroku/streamlit_app.py"},
            }
        )

    monkeypatch.setattr("plugins.integrations.github_repo_sync_plugin.request.urlopen", _fake_urlopen)

    plugin = GitHubRepoSyncPlugin(
        token="token",
        repo_owner="owner",
        repo_name="repo",
        branch="main",
        allowed_path_prefix="deploy/heroku",
    )

    result = plugin.upsert_text_file(
        file_path="deploy/heroku/streamlit_app.py",
        content="print('updated')\n",
        commit_message="Update existing",
    )

    assert result["status"] == "success"
    assert result["operation"] == "update"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["sha"] == "existing-sha"


def test_upsert_text_file_with_named_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):
        _ = timeout
        method = req.get_method()
        if method == "GET":
            raise error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)

        body = req.data.decode("utf-8") if isinstance(req.data, bytes) else "{}"
        payload = json.loads(body)
        captured["payload"] = payload
        return _FakeHTTPResponse(
            {
                "commit": {"sha": "named-arg-sha", "html_url": "https://github.com/owner/repo/commit/named-arg-sha"},
                "content": {"html_url": "https://github.com/owner/repo/blob/main/deploy/heroku/streamlit_app.py"},
            }
        )

    monkeypatch.setattr("plugins.integrations.github_repo_sync_plugin.request.urlopen", _fake_urlopen)

    plugin = GitHubRepoSyncPlugin(
        token="token",
        repo_owner="owner",
        repo_name="repo",
        branch="main",
        allowed_path_prefix="deploy/heroku",
    )

    result = plugin.upsert_text_file(
        {
            "file_path": "deploy/heroku/streamlit_app.py",
            "content": "import streamlit as st\nst.title('Named payload')\n",
            "commit_message": "Named payload upsert",
        }
    )

    assert result["status"] == "success"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["message"] == "Named payload upsert"


def test_commit_streamlit_app_with_named_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout=0):
        _ = timeout
        method = req.get_method()
        if method == "GET":
            raise error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)

        body = req.data.decode("utf-8") if isinstance(req.data, bytes) else "{}"
        payload = json.loads(body)
        captured["payload"] = payload
        return _FakeHTTPResponse(
            {
                "commit": {"sha": "streamlit-named-sha", "html_url": "https://github.com/owner/repo/commit/streamlit-named-sha"},
                "content": {"html_url": "https://github.com/owner/repo/blob/main/deploy/heroku/streamlit_app.py"},
            }
        )

    monkeypatch.setattr("plugins.integrations.github_repo_sync_plugin.request.urlopen", _fake_urlopen)

    plugin = GitHubRepoSyncPlugin(
        token="token",
        repo_owner="owner",
        repo_name="repo",
        branch="main",
        allowed_path_prefix="deploy/heroku",
    )

    result = plugin.commit_streamlit_app(
        {
            "app_content": "import streamlit as st\nst.title('From commit_streamlit_app payload')\n",
            "file_path": "deploy/heroku/streamlit_app.py",
            "commit_message": "Named payload streamlit commit",
        }
    )

    assert result["status"] == "success"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["message"] == "Named payload streamlit commit"


def test_commit_streamlit_app_bundle_includes_heroku_files(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []

    plugin = GitHubRepoSyncPlugin(
        token="token",
        repo_owner="owner",
        repo_name="repo",
        branch="main",
        allowed_path_prefix="deploy/heroku",
    )

    def _fake_upsert_text_file(file_path, content=None, commit_message=""):
        if isinstance(file_path, dict):
            payload = file_path
            file_path = payload.get("file_path", "")
            content = payload.get("content")
            commit_message = payload.get("commit_message", commit_message)
        calls.append((str(file_path), str(content), str(commit_message)))
        return {
            "status": "success",
            "repo": "owner/repo",
            "branch": "main",
            "file_path": str(file_path),
            "operation": "update",
            "commit_sha": "sha-for-" + str(file_path),
            "actions_runs_url": "https://github.com/owner/repo/actions",
        }

    monkeypatch.setattr(plugin, "upsert_text_file", _fake_upsert_text_file)

    result = plugin.commit_streamlit_app(
        {
            "app_content": "import streamlit as st\nst.title('Bundle')\n",
            "file_path": "deploy/heroku/streamlit_app.py",
            "commit_message": "Bundle update",
            "include_heroku_files": True,
        }
    )

    assert result["status"] == "success"
    assert result["operation"] == "bundle_update"
    committed_paths = [item[0] for item in calls]
    assert committed_paths == [
        "deploy/heroku/streamlit_app.py",
        "Procfile",
        "requirements.txt",
        "runtime.txt",
    ]
    assert "deploy_files" in result
