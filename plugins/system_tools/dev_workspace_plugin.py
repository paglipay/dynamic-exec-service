"""Development workspace plugin for creating and managing application workspaces."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_DANGEROUS_COMMAND_PATTERNS = re.compile(
    r"""
      (?:^|\s|;|&|\|)           # word boundary
      (?:
        rm\s+-rf\s+/             # rm -rf /
      | del\s+/[sq].*\s+[a-zA-Z]: # del /s /q C:
      | format\s+[a-zA-Z]:       # format C:
      | mkfs                     # mkfs.*
      | fdisk                    # fdisk
      | shutdown                 # shutdown
      | reboot                   # reboot
      | halt                     # halt
      | poweroff                 # poweroff
      | (?:sudo|su)\s            # sudo / su
      | dd\s+if=                 # dd if=
      | (?::){1,}\s*\(\s*\){1,}\s*\{  # fork bomb  :(){:|:&};:
      )
    """,
    re.VERBOSE | re.IGNORECASE,
)

_SAFE_PROJECT_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_\-]{0,63}$")
_SAFE_RELATIVE_PATH = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _\-./\\]*$")


class DevWorkspacePlugin:
    """Manage sandboxed development workspaces and scaffold application projects.

    Each workspace is an isolated directory under WORKSPACES_ROOT.  The AI
    agent can write arbitrary source files, run shell commands (cwd locked to
    the workspace), and list / read files — all through this plugin.

    Environment variables:
        WORKSPACES_ROOT  Path to the root directory that holds all workspaces.
                         Defaults to ``generated_data/workspaces``.
    """

    _default_root = os.path.join(os.getenv("BASE_DATA_DIR", "generated_data"), "workspaces")

    def __init__(self, workspaces_root: str | None = None) -> None:
        raw_root = workspaces_root or os.getenv("WORKSPACES_ROOT") or self._default_root
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ValueError("workspaces_root must be a non-empty string")
        self._root = Path(raw_root.strip()).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_workspace(self, workspace_name: str) -> Path:
        """Resolve and return a workspace directory path."""
        if not isinstance(workspace_name, str) or not workspace_name.strip():
            raise ValueError("workspace_name must be a non-empty string")
        name = workspace_name.strip()
        if not _SAFE_PROJECT_NAME.match(name):
            raise ValueError(
                "workspace_name must start with a letter and contain only "
                "letters, digits, underscores, or hyphens (max 64 chars)"
            )
        workspace_dir = (self._root / name).resolve()
        try:
            workspace_dir.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("workspace_name must not escape the workspaces root") from exc
        return workspace_dir

    def _resolve_file_path(self, workspace_dir: Path, relative_path: str) -> Path:
        """Resolve a relative file path inside a workspace directory."""
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("relative_path must be a non-empty string")
        rp = relative_path.strip().replace("\\", "/")
        if ".." in rp.split("/"):
            raise ValueError("relative_path must not contain '..' components")
        # Normalise path separators for the current OS
        candidate = workspace_dir / Path(rp)
        resolved = candidate.resolve()
        try:
            resolved.relative_to(workspace_dir)
        except ValueError as exc:
            raise ValueError("relative_path must stay inside the workspace directory") from exc
        return resolved

    @staticmethod
    def _check_command(command: str) -> None:
        """Raise ValueError if the command matches known destructive patterns."""
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        if _DANGEROUS_COMMAND_PATTERNS.search(command):
            raise ValueError(
                "Command was rejected because it matches a known destructive pattern. "
                "Use a safer equivalent or contact the workspace administrator."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scaffold_project(
        self,
        project_name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new workspace directory with a skeleton README.

        Args:
            project_name: Unique workspace identifier (letters/digits/-/_).
            description:  Optional one-line description written into README.md.
        Returns:
            ``{"status": "success", "workspace": project_name, "path": str}``
        """
        workspace_dir = self._resolve_workspace(project_name)
        already_existed = workspace_dir.exists()
        workspace_dir.mkdir(parents=True, exist_ok=True)

        readme = workspace_dir / "README.md"
        if not readme.exists():
            readme_text = f"# {project_name}\n\n"
            if isinstance(description, str) and description.strip():
                readme_text += description.strip() + "\n"
            readme.write_text(readme_text, encoding="utf-8")

        return {
            "status": "success",
            "workspace": project_name,
            "path": str(workspace_dir),
            "already_existed": already_existed,
        }

    def write_file(
        self,
        workspace_name: str,
        relative_path: str,
        content: str,
    ) -> dict[str, Any]:
        """Write (create or overwrite) a file inside a workspace.

        Args:
            workspace_name: Target workspace identifier.
            relative_path:  Path relative to the workspace root (e.g. ``src/app.py``).
            content:        Full text content to write.
        Returns:
            ``{"status": "success", "path": str, "bytes_written": int}``
        """
        workspace_dir = self._resolve_workspace(workspace_name)
        if not workspace_dir.exists():
            raise ValueError(f"Workspace '{workspace_name}' does not exist. Call scaffold_project first.")
        if not isinstance(content, str):
            raise ValueError("content must be a string")

        target = self._resolve_file_path(workspace_dir, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        target.write_bytes(encoded)

        return {
            "status": "success",
            "workspace": workspace_name,
            "path": str(target),
            "relative_path": relative_path,
            "bytes_written": len(encoded),
        }

    def read_file(
        self,
        workspace_name: str,
        relative_path: str,
        max_chars: int = 20000,
    ) -> dict[str, Any]:
        """Read a file from a workspace.

        Args:
            workspace_name: Target workspace identifier.
            relative_path:  Path relative to the workspace root.
            max_chars:      Maximum characters to return (default 20 000).
        Returns:
            ``{"status": "success", "content": str, "truncated": bool}``
        """
        workspace_dir = self._resolve_workspace(workspace_name)
        if not workspace_dir.exists():
            raise ValueError(f"Workspace '{workspace_name}' does not exist.")

        target = self._resolve_file_path(workspace_dir, relative_path)
        if not target.exists() or not target.is_file():
            raise ValueError(f"File '{relative_path}' does not exist in workspace '{workspace_name}'.")

        if not isinstance(max_chars, int) or max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")

        try:
            raw = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError(f"Could not read file: {exc}") from exc

        truncated = len(raw) > max_chars
        return {
            "status": "success",
            "workspace": workspace_name,
            "relative_path": relative_path,
            "content": raw[:max_chars],
            "truncated": truncated,
            "total_chars": len(raw),
        }

    def list_files(
        self,
        workspace_name: str,
        sub_path: str = "",
    ) -> dict[str, Any]:
        """List files and directories inside a workspace.

        Args:
            workspace_name: Target workspace identifier.
            sub_path:       Optional subdirectory to list (relative to workspace root).
        Returns:
            ``{"status": "success", "entries": [{"name": str, "type": "file"|"dir", "size": int|None}]}``
        """
        workspace_dir = self._resolve_workspace(workspace_name)
        if not workspace_dir.exists():
            raise ValueError(f"Workspace '{workspace_name}' does not exist.")

        if sub_path and sub_path.strip():
            target_dir = self._resolve_file_path(workspace_dir, sub_path.strip())
        else:
            target_dir = workspace_dir

        if not target_dir.exists() or not target_dir.is_dir():
            raise ValueError(f"Directory '{sub_path}' does not exist in workspace '{workspace_name}'.")

        entries: list[dict[str, Any]] = []
        for child in sorted(target_dir.iterdir(), key=lambda p: (p.is_file(), p.name)):
            entry: dict[str, Any] = {
                "name": child.name,
                "type": "file" if child.is_file() else "dir",
                "size": child.stat().st_size if child.is_file() else None,
            }
            entries.append(entry)

        return {
            "status": "success",
            "workspace": workspace_name,
            "sub_path": sub_path or "",
            "entries": entries,
        }

    def run_command(
        self,
        workspace_name: str,
        command: str,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Run a shell command inside a workspace directory.

        The command runs with the workspace as its working directory.
        Commands matching known destructive patterns are rejected.

        Args:
            workspace_name:   Target workspace identifier.
            command:          Shell command string (e.g. ``pip install flask``).
            timeout_seconds:  Maximum execution time (default 120 s, max 600 s).
        Returns:
            ``{"status": "success"|"error", "stdout": str, "stderr": str, "exit_code": int}``
        """
        workspace_dir = self._resolve_workspace(workspace_name)
        if not workspace_dir.exists():
            raise ValueError(f"Workspace '{workspace_name}' does not exist. Call scaffold_project first.")

        self._check_command(command)

        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        timeout_seconds = min(timeout_seconds, 600)

        # On Windows use shell=True so PATH-dependent tools (npm, git, node) resolve.
        # On Unix also use shell=True for the same reason.
        try:
            result = subprocess.run(
                command,
                cwd=str(workspace_dir),
                shell=True,  # noqa: S602 — intentional, guarded by blocklist above
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={**os.environ},
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "workspace": workspace_name,
                "command": command,
                "stdout": "",
                "stderr": f"Command timed out after {timeout_seconds} seconds.",
                "exit_code": -1,
            }
        except Exception as exc:
            return {
                "status": "error",
                "workspace": workspace_name,
                "command": command,
                "stdout": "",
                "stderr": str(exc),
                "exit_code": -1,
            }

        _MAX_OUTPUT = 8000
        stdout = result.stdout[:_MAX_OUTPUT] if result.stdout else ""
        stderr = result.stderr[:_MAX_OUTPUT] if result.stderr else ""

        return {
            "status": "success" if result.returncode == 0 else "error",
            "workspace": workspace_name,
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
        }

    def list_workspaces(self) -> dict[str, Any]:
        """List all workspaces under the configured root.

        Returns:
            ``{"status": "success", "workspaces": [{"name": str, "path": str}]}``
        """
        workspaces: list[dict[str, str]] = []
        if self._root.exists():
            for child in sorted(self._root.iterdir()):
                if child.is_dir():
                    workspaces.append({"name": child.name, "path": str(child)})

        return {
            "status": "success",
            "root": str(self._root),
            "workspaces": workspaces,
        }

    def delete_workspace(
        self,
        workspace_name: str,
    ) -> dict[str, Any]:
        """Permanently delete a workspace directory and all its contents.

        Args:
            workspace_name: Workspace identifier to remove.
        Returns:
            ``{"status": "success", "workspace": str}``
        """
        workspace_dir = self._resolve_workspace(workspace_name)
        if not workspace_dir.exists():
            raise ValueError(f"Workspace '{workspace_name}' does not exist.")

        shutil.rmtree(workspace_dir)

        return {
            "status": "success",
            "workspace": workspace_name,
            "message": f"Workspace '{workspace_name}' deleted.",
        }
