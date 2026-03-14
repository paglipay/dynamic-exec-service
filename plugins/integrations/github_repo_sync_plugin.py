"""GitHub repository sync plugin for committing Streamlit app updates."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib import error, request
from urllib.parse import quote


class GitHubRepoSyncPlugin:
    """Commit file updates to a configured GitHub repository using Contents API."""

    @staticmethod
    def _validate_repo_identifier(value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must be a non-empty string")
        if any(ch.isspace() for ch in normalized):
            raise ValueError(f"{field_name} must not contain spaces")
        if "/" in normalized or "\\" in normalized:
            raise ValueError(f"{field_name} must not contain path separators")
        return normalized

    def __init__(
        self,
        token: str | None = None,
        repo_owner: str | None = None,
        repo_name: str | None = None,
        branch: str = "main",
        allowed_path_prefix: str = "deploy/heroku",
        allowed_root_files: list[str] | None = None,
    ) -> None:
        resolved_token = token or os.getenv("GITHUB_TOKEN")
        resolved_owner = repo_owner or os.getenv("GITHUB_REPO_OWNER")
        resolved_repo = repo_name or os.getenv("GITHUB_REPO_NAME")

        if not isinstance(resolved_token, str) or not resolved_token.strip():
            raise ValueError("token must be provided (or set GITHUB_TOKEN)")
        if not isinstance(resolved_owner, str) or not resolved_owner.strip():
            raise ValueError("repo_owner must be provided (or set GITHUB_REPO_OWNER)")
        if not isinstance(resolved_repo, str) or not resolved_repo.strip():
            raise ValueError("repo_name must be provided (or set GITHUB_REPO_NAME)")
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError("branch must be a non-empty string")
        if not isinstance(allowed_path_prefix, str) or not allowed_path_prefix.strip():
            raise ValueError("allowed_path_prefix must be a non-empty string")
        if allowed_root_files is not None and not isinstance(allowed_root_files, list):
            raise ValueError("allowed_root_files must be an array when provided")

        self.token = resolved_token.strip()
        self.repo_owner = self._validate_repo_identifier(resolved_owner, "repo_owner")
        self.repo_name = self._validate_repo_identifier(resolved_repo, "repo_name")
        self.branch = branch.strip()
        self.allowed_path_prefix = allowed_path_prefix.strip().strip("/")
        raw_root_files = allowed_root_files or ["Procfile", "requirements.txt", "runtime.txt"]
        normalized_root_files: set[str] = set()
        for file_name in raw_root_files:
            if not isinstance(file_name, str) or not file_name.strip():
                raise ValueError("allowed_root_files entries must be non-empty strings")
            normalized_file_name = file_name.strip().replace("\\", "/").lstrip("/")
            if "/" in normalized_file_name:
                raise ValueError("allowed_root_files entries must be root-level filenames")
            normalized_root_files.add(normalized_file_name)
        self.allowed_root_files = normalized_root_files
        self._api_base = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}"

    def _ensure_allowed_path(self, file_path: str) -> str:
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("file_path must be a non-empty string")

        normalized = file_path.strip().replace("\\", "/").lstrip("/")
        if ".." in normalized.split("/"):
            raise ValueError("file_path must not include parent traversal")
        if normalized in self.allowed_root_files:
            return normalized
        if not normalized.startswith(self.allowed_path_prefix + "/") and normalized != self.allowed_path_prefix:
            raise ValueError("file_path is outside allowed_path_prefix")

        return normalized

    def _github_request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "dynamic-exec-service-github-sync",
        }

        data: bytes | None = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        req = request.Request(url=url, data=data, headers=headers, method=method)

        try:
            with request.urlopen(req, timeout=25) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"GitHub API error {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise ValueError(f"Failed to reach GitHub API: {exc.reason}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub API returned invalid JSON") from exc

        if not isinstance(parsed, dict):
            raise ValueError("GitHub API returned unexpected response type")

        return parsed

    def _get_existing_file_sha(self, file_path: str) -> str | None:
        encoded_path = quote(file_path, safe="/")
        url = f"{self._api_base}/contents/{encoded_path}?ref={quote(self.branch, safe='')}"

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dynamic-exec-service-github-sync",
        }
        req = request.Request(url=url, headers=headers, method="GET")

        try:
            with request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            if exc.code == 404:
                return None
            body = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"GitHub API error {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise ValueError(f"Failed to reach GitHub API: {exc.reason}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub API returned invalid JSON") from exc

        if not isinstance(parsed, dict):
            raise ValueError("GitHub API returned unexpected response type")

        sha = parsed.get("sha")
        if isinstance(sha, str) and sha.strip():
            return sha.strip()
        return None

    def upsert_text_file(
        self,
        file_path: str | dict[str, Any],
        content: str | None = None,
        commit_message: str = "Update file from dynamic-exec-service",
    ) -> dict[str, Any]:
        """Create or update a text file in the configured GitHub repository."""
        if isinstance(file_path, dict):
            payload = file_path
            file_path = payload.get("file_path", "")
            content = payload.get("content")
            commit_message = payload.get("commit_message", commit_message)

        normalized_path = self._ensure_allowed_path(file_path)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ValueError("commit_message must be a non-empty string")

        existing_sha = self._get_existing_file_sha(normalized_path)

        encoded_path = quote(normalized_path, safe="/")
        url = f"{self._api_base}/contents/{encoded_path}"
        payload: dict[str, Any] = {
            "message": commit_message.strip(),
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        if isinstance(existing_sha, str) and existing_sha:
            payload["sha"] = existing_sha

        response = self._github_request("PUT", url, payload=payload)
        commit = response.get("commit", {}) if isinstance(response.get("commit"), dict) else {}
        content_info = response.get("content", {}) if isinstance(response.get("content"), dict) else {}

        return {
            "status": "success",
            "repo": f"{self.repo_owner}/{self.repo_name}",
            "branch": self.branch,
            "file_path": normalized_path,
            "operation": "update" if existing_sha else "create",
            "commit_sha": commit.get("sha"),
            "commit_url": commit.get("html_url"),
            "file_url": content_info.get("html_url"),
            "actions_runs_url": f"https://github.com/{self.repo_owner}/{self.repo_name}/actions",
        }

    def commit_streamlit_app(
        self,
        app_content: str | dict[str, Any],
        file_path: str = "deploy/heroku/streamlit_app.py",
        commit_message: str = "Update Streamlit app via dynamic-exec-service",
    ) -> dict[str, Any]:
        """Commit Streamlit app source file to repository for CI/CD deployment."""
        include_heroku_files = False
        procfile_content: str | None = None
        requirements_content: str | None = None
        runtime_content: str | None = None

        if isinstance(app_content, dict):
            payload = app_content
            app_content = payload.get("app_content", "")
            file_path = payload.get("file_path", file_path)
            commit_message = payload.get("commit_message", commit_message)
            include_heroku_files = payload.get("include_heroku_files", False)
            procfile_content = payload.get("procfile_content")
            requirements_content = payload.get("requirements_content")
            runtime_content = payload.get("runtime_content")

        if not isinstance(include_heroku_files, bool):
            raise ValueError("include_heroku_files must be a boolean")
        if procfile_content is not None and (not isinstance(procfile_content, str) or not procfile_content.strip()):
            raise ValueError("procfile_content must be a non-empty string when provided")
        if requirements_content is not None and (not isinstance(requirements_content, str) or not requirements_content.strip()):
            raise ValueError("requirements_content must be a non-empty string when provided")
        if runtime_content is not None and (not isinstance(runtime_content, str) or not runtime_content.strip()):
            raise ValueError("runtime_content must be a non-empty string when provided")

        app_result = self.upsert_text_file(
            file_path=file_path,
            content=app_content,
            commit_message=commit_message,
        )

        if not include_heroku_files:
            return app_result

        streamlit_procfile = procfile_content or (
            f"web: streamlit run {file_path} --server.port=$PORT --server.address=0.0.0.0 --server.headless=true\n"
        )
        streamlit_requirements = requirements_content or "streamlit\n"
        streamlit_runtime = runtime_content or "python-3.12.8\n"

        procfile_result = self.upsert_text_file(
            file_path="Procfile",
            content=streamlit_procfile,
            commit_message="Ensure Heroku Procfile for Streamlit deployment",
        )
        requirements_result = self.upsert_text_file(
            file_path="requirements.txt",
            content=streamlit_requirements,
            commit_message="Ensure requirements for Streamlit deployment",
        )
        runtime_result = self.upsert_text_file(
            file_path="runtime.txt",
            content=streamlit_runtime,
            commit_message="Ensure runtime for Streamlit deployment",
        )

        return {
            "status": "success",
            "repo": app_result.get("repo"),
            "branch": app_result.get("branch"),
            "operation": "bundle_update",
            "streamlit_app": app_result,
            "deploy_files": {
                "Procfile": procfile_result,
                "requirements.txt": requirements_result,
                "runtime.txt": runtime_result,
            },
            "actions_runs_url": app_result.get("actions_runs_url"),
        }