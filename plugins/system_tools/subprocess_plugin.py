"""Subprocess plugin for running Python scripts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


class SubprocessPlugin:
    """Execute Python scripts via subprocess using the current interpreter."""

    def __init__(self, base_dir: str = ".", allow_outside_base_dir: bool = True) -> None:
        if not isinstance(base_dir, str) or not base_dir:
            raise ValueError("base_dir must be a non-empty string")
        if not isinstance(allow_outside_base_dir, bool):
            raise ValueError("allow_outside_base_dir must be a boolean")

        self.base_dir = Path(base_dir).resolve()
        self.allow_outside_base_dir = allow_outside_base_dir

        if not self.base_dir.exists() or not self.base_dir.is_dir():
            raise ValueError("base_dir must point to an existing directory")

    def _resolve_script_path(self, script_path: str) -> Path:
        if not isinstance(script_path, str) or not script_path.strip():
            raise ValueError("script_path must be a non-empty string")

        candidate = Path(script_path.strip())
        resolved = candidate.resolve() if candidate.is_absolute() else (self.base_dir / candidate).resolve()

        if not resolved.exists() or not resolved.is_file():
            raise ValueError("script_path does not exist")
        if resolved.suffix.lower() != ".py":
            raise ValueError("Only .py files are allowed")

        if not self.allow_outside_base_dir:
            try:
                resolved.relative_to(self.base_dir)
            except ValueError as exc:
                raise ValueError("script_path must be inside base_dir") from exc

        return resolved

    def run_python_script(
        self,
        script_path: str,
        args: list[Any] | None = None,
        cwd: str | None = None,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """Run a Python script and return stdout/stderr/exit_code."""
        if args is None:
            args = []
        if not isinstance(args, list):
            raise ValueError("args must be an array")
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be an integer > 0")

        resolved_script = self._resolve_script_path(script_path)

        working_dir: Path | None = None
        if cwd is not None:
            if not isinstance(cwd, str) or not cwd.strip():
                raise ValueError("cwd must be a non-empty string when provided")
            cwd_candidate = Path(cwd.strip())
            working_dir = cwd_candidate.resolve() if cwd_candidate.is_absolute() else (self.base_dir / cwd_candidate).resolve()
            if not working_dir.exists() or not working_dir.is_dir():
                raise ValueError("cwd must point to an existing directory")

        command = [sys.executable, str(resolved_script), *[str(item) for item in args]]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                cwd=str(working_dir) if working_dir is not None else None,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Script execution timed out") from exc

        return {
            "status": "success",
            "script": str(resolved_script),
            "cwd": str(working_dir) if working_dir is not None else None,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command": command,
        }
