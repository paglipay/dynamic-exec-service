"""Cross-platform system introspection plugin."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


class TerminalIntrospectionPlugin:
    """Read-only terminal-style diagnostics for the local runtime environment."""

    def __init__(self, base_dir: str = ".") -> None:
        if not isinstance(base_dir, str) or not base_dir:
            raise ValueError("base_dir must be a non-empty string")

        self.base_dir = Path(base_dir).resolve()
        if not self.base_dir.exists() or not self.base_dir.is_dir():
            raise ValueError("base_dir must point to an existing directory")

    def _resolve_directory(self, directory: str | None) -> Path:
        if directory is None:
            return self.base_dir
        if not isinstance(directory, str) or not directory:
            raise ValueError("directory must be a non-empty string when provided")

        resolved = (self.base_dir / directory).resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError("directory does not exist")
        return resolved

    def get_environment_summary(self) -> dict[str, Any]:
        """Return runtime and platform details for the current process."""
        return {
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "base_dir": str(self.base_dir),
        }

    def list_directory(self, directory: str | None = None) -> dict[str, Any]:
        """List files/directories in a target folder."""
        target = self._resolve_directory(directory)
        entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))

        return {
            "path": str(target),
            "entries": [
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                }
                for entry in entries
            ],
        }

    def discover_folder_structure(
        self,
        directory: str | None = None,
        max_depth: int = 3,
        max_entries: int = 500,
    ) -> dict[str, Any]:
        """Return a bounded recursive directory tree."""
        if not isinstance(max_depth, int) or max_depth < 0:
            raise ValueError("max_depth must be an integer >= 0")
        if not isinstance(max_entries, int) or max_entries <= 0:
            raise ValueError("max_entries must be an integer > 0")

        root = self._resolve_directory(directory)
        total_entries = 0
        truncated = False

        def walk(path: Path, depth: int) -> list[dict[str, Any]]:
            nonlocal total_entries, truncated

            if depth > max_depth or truncated:
                return []

            children: list[dict[str, Any]] = []
            for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                if total_entries >= max_entries:
                    truncated = True
                    break

                total_entries += 1
                node: dict[str, Any] = {
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                }

                if child.is_dir() and depth < max_depth:
                    node["children"] = walk(child, depth + 1)

                children.append(node)

            return children

        tree = walk(root, 0)
        return {
            "root": str(root),
            "max_depth": max_depth,
            "max_entries": max_entries,
            "total_entries": total_entries,
            "truncated": truncated,
            "tree": tree,
        }

    def pip_freeze(self) -> dict[str, Any]:
        """Run `python -m pip freeze` using the active interpreter."""
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pip", "freeze"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("pip freeze timed out") from exc

        packages = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        return {
            "return_code": completed.returncode,
            "packages": packages,
            "stderr": completed.stderr.strip(),
        }
