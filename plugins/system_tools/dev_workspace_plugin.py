"""Development workspace plugin for creating and managing application workspaces."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error as _urlerror, request as _urlrequest

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
        timeout_seconds = min(timeout_seconds, 1800)

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

    # ------------------------------------------------------------------
    # Persistent background server management
    # ------------------------------------------------------------------

    # In-process registry: {workspace_name: {server_name: Popen}}
    _servers: dict[str, dict[str, Any]] = {}

    def start_server(
        self,
        workspace_name: str,
        script: str,
        port: int,
        server_name: str = "default",
        extra_args: list[str] | None = None,
        startup_wait_seconds: int = 15,
    ) -> dict[str, Any]:
        """Start a Python script as a persistent background HTTP server inside a workspace.

        The script is started with the workspace as its cwd and kept alive
        across tool calls for the life of the service process.  Use
        ``query_server`` to send requests and ``stop_server`` to terminate.

        Args:
            workspace_name:        Target workspace identifier.
            script:                Python script path relative to the workspace
                                   (e.g. ``llm_server.py``).
            port:                  Port the server will listen on.
            server_name:           Logical name for this server (default ``"default"``).
                                   Allows multiple servers per workspace.
            extra_args:            Extra CLI args passed to the script
                                   (e.g. ``["--host", "127.0.0.1"]``).
            startup_wait_seconds:  How long to wait for /health to respond
                                   before returning (default 15 s).
        Returns:
            ``{"status": "success"|"error", "url": str, "pid": int}``
        """
        workspace_dir = self._resolve_workspace(workspace_name)
        if not workspace_dir.exists():
            raise ValueError(f"Workspace '{workspace_name}' does not exist.")

        if not isinstance(port, int) or not (1024 <= port <= 65535):
            raise ValueError("port must be an integer between 1024 and 65535")
        if not isinstance(server_name, str) or not server_name.strip():
            raise ValueError("server_name must be a non-empty string")
        if not isinstance(startup_wait_seconds, int) or startup_wait_seconds <= 0:
            raise ValueError("startup_wait_seconds must be a positive integer")

        script_path = self._resolve_file_path(workspace_dir, script)
        if not script_path.exists():
            raise ValueError(f"Script '{script}' does not exist in workspace '{workspace_name}'.")

        # Stop any existing server with the same name.
        self._kill_server(workspace_name, server_name)

        cmd = [sys.executable, str(script_path), "--port", str(port)]
        if extra_args:
            cmd.extend([str(a) for a in extra_args])

        proc = subprocess.Popen(
            cmd,
            cwd=str(workspace_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ},
        )

        ws_servers = self._servers.setdefault(workspace_name, {})
        ws_servers[server_name] = {"proc": proc, "port": port, "script": script, "url": f"http://127.0.0.1:{port}"}

        # Wait for /health to respond.
        url = f"http://127.0.0.1:{port}/health"
        deadline = time.monotonic() + startup_wait_seconds
        last_error = ""
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = b""
                try:
                    out = proc.stdout.read(2000) if proc.stdout else b""
                except Exception:
                    pass
                return {
                    "status": "error",
                    "workspace": workspace_name,
                    "server_name": server_name,
                    "message": f"Server process exited early (rc={proc.returncode}). Output: {out.decode('utf-8', errors='replace')}",
                }
            try:
                with _urlrequest.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        return {
                            "status": "success",
                            "workspace": workspace_name,
                            "server_name": server_name,
                            "pid": proc.pid,
                            "url": f"http://127.0.0.1:{port}",
                            "port": port,
                        }
            except Exception as exc:
                last_error = str(exc)
            time.sleep(1)

        return {
            "status": "error",
            "workspace": workspace_name,
            "server_name": server_name,
            "message": f"Server did not respond within {startup_wait_seconds}s. Last error: {last_error}",
        }

    def query_server(
        self,
        workspace_name: str,
        path: str,
        body: dict[str, Any] | None = None,
        server_name: str = "default",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Send an HTTP request to a running workspace server.

        GET requests are used when *body* is ``None``; POST when *body* is provided.

        Args:
            workspace_name:  Target workspace identifier.
            path:            URL path (e.g. ``"/generate"`` or ``"/health"``).
            body:            Optional JSON-serialisable dict for POST body.
            server_name:     Logical server name (default ``"default"``).
            timeout_seconds: Request timeout (default 120 s for slow models).
        Returns:
            ``{"status": "success", "response": dict}`` or ``{"status": "error", ...}``
        """
        ws_servers = self._servers.get(workspace_name, {})
        info = ws_servers.get(server_name)
        if not info:
            raise ValueError(
                f"No server named '{server_name}' is registered for workspace '{workspace_name}'. "
                "Call start_server first."
            )
        proc: Any = info.get("proc")
        if proc is not None and proc.poll() is not None:
            raise ValueError(
                f"Server '{server_name}' in workspace '{workspace_name}' has stopped "
                f"(exit code {proc.returncode}). Call start_server to restart it."
            )

        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("path must be a string starting with '/'")
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")

        url = f"http://127.0.0.1:{info['port']}{path}"
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            req = _urlrequest.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            req = _urlrequest.Request(url, method="GET")

        try:
            with _urlrequest.urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = {"raw": raw}
                return {"status": "success", "workspace": workspace_name, "server_name": server_name, "response": parsed}
        except _urlerror.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return {"status": "error", "workspace": workspace_name, "http_status": exc.code, "message": raw}
        except Exception as exc:
            return {"status": "error", "workspace": workspace_name, "message": str(exc)}

    def server_status(
        self,
        workspace_name: str,
        server_name: str = "default",
    ) -> dict[str, Any]:
        """Check whether a named workspace server is running.

        Returns:
            ``{"status": "success", "running": bool, "pid": int|None, "url": str|None}``
        """
        ws_servers = self._servers.get(workspace_name, {})
        info = ws_servers.get(server_name)
        if not info:
            return {
                "status": "success",
                "workspace": workspace_name,
                "server_name": server_name,
                "running": False,
                "pid": None,
                "url": None,
                "message": "No server registered under that name.",
            }
        proc: Any = info.get("proc")
        running = proc is not None and proc.poll() is None
        return {
            "status": "success",
            "workspace": workspace_name,
            "server_name": server_name,
            "running": running,
            "pid": proc.pid if proc else None,
            "url": info.get("url"),
            "exit_code": proc.returncode if proc and not running else None,
        }

    def stop_server(
        self,
        workspace_name: str,
        server_name: str = "default",
    ) -> dict[str, Any]:
        """Stop a running workspace server.

        Sends a POST /shutdown request first; falls back to SIGTERM/SIGKILL.

        Returns:
            ``{"status": "success", "stopped": bool}``
        """
        return self._kill_server(workspace_name, server_name)

    def _kill_server(self, workspace_name: str, server_name: str) -> dict[str, Any]:
        ws_servers = self._servers.get(workspace_name, {})
        info = ws_servers.pop(server_name, None)
        if not info:
            return {"status": "success", "workspace": workspace_name, "server_name": server_name, "stopped": False, "message": "No server was registered."}

        proc: Any = info.get("proc")
        if proc is None or proc.poll() is not None:
            return {"status": "success", "workspace": workspace_name, "server_name": server_name, "stopped": True}

        # Graceful: POST /shutdown
        try:
            shutdown_url = f"http://127.0.0.1:{info['port']}/shutdown"
            _urlrequest.urlopen(
                _urlrequest.Request(shutdown_url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"),
                timeout=4,
            )
            proc.wait(timeout=6)
        except Exception:
            pass

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

        return {"status": "success", "workspace": workspace_name, "server_name": server_name, "stopped": True, "exit_code": proc.returncode}
