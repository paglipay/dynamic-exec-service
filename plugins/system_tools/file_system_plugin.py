"""Filesystem plugin restricted to operations inside a base directory."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


class FileSystemPlugin:
    """Read and mutate filesystem paths within a confined base directory."""

    def __init__(
        self,
        base_dir: str = "generated_data",
        allow_outside_base_dir: bool = False,
    ) -> None:
        if not isinstance(base_dir, str) or not base_dir.strip():
            raise ValueError("base_dir must be a non-empty string")
        if not isinstance(allow_outside_base_dir, bool):
            raise ValueError("allow_outside_base_dir must be a boolean")

        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.allow_outside_base_dir = allow_outside_base_dir

    def _resolve_path(self, relative_path: str, allow_current: bool = False) -> Path:
        if not isinstance(relative_path, str):
            raise ValueError("path must be a string")

        normalized = relative_path.strip()
        if not normalized:
            if allow_current:
                normalized = "."
            else:
                raise ValueError("path must be a non-empty string")

        candidate = Path(normalized)

        if self.allow_outside_base_dir:
            return candidate.resolve() if candidate.is_absolute() else (self.base_dir / candidate).resolve()

        if candidate.is_absolute():
            raise ValueError("Absolute paths are not allowed")

        target = (self.base_dir / candidate).resolve()
        try:
            target.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError("Invalid path") from exc

        return target

    def _relative_label(self, path: Path) -> str:
        """Return a base-relative label when possible, else the absolute path."""
        try:
            return str(path.relative_to(self.base_dir)).replace("\\", "/") or "."
        except ValueError:
            return str(path).replace("\\", "/")

    def list_directory(self, directory: str = ".") -> dict[str, Any]:
        target = self._resolve_path(directory, allow_current=True)
        if not target.exists() or not target.is_dir():
            raise ValueError("directory does not exist")

        entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        return {
            "status": "success",
            "base_dir": str(self.base_dir),
            "directory": self._relative_label(target),
            "entries": [
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "relative_path": self._relative_label(entry),
                }
                for entry in entries
            ],
        }

    def create_directory(self, directory: str) -> dict[str, Any]:
        target = self._resolve_path(directory)
        target.mkdir(parents=True, exist_ok=True)
        return {
            "status": "success",
            "action": "create_directory",
            "directory": self._relative_label(target),
        }

    def move_path(self, source_path: str, destination_path: str) -> dict[str, Any]:
        source = self._resolve_path(source_path)
        destination = self._resolve_path(destination_path)

        if not source.exists():
            raise ValueError("source path does not exist")
        if destination.exists():
            raise ValueError("destination path already exists")

        destination.parent.mkdir(parents=True, exist_ok=True)
        moved_path = Path(shutil.move(str(source), str(destination)))

        return {
            "status": "success",
            "action": "move_path",
            "source": self._relative_label(source),
            "destination": self._relative_label(moved_path),
        }

    def delete_path(self, target_path: str, recursive: bool = False) -> dict[str, Any]:
        if not isinstance(recursive, bool):
            raise ValueError("recursive must be a boolean")

        target = self._resolve_path(target_path)
        if not target.exists():
            raise ValueError("target path does not exist")

        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()

        return {
            "status": "success",
            "action": "delete_path",
            "target": self._relative_label(target),
            "recursive": recursive,
        }

    def path_info(self, target_path: str = ".") -> dict[str, Any]:
        target = self._resolve_path(target_path, allow_current=True)
        if not target.exists():
            raise ValueError("target path does not exist")

        return {
            "status": "success",
            "base_dir": str(self.base_dir),
            "target": self._relative_label(target),
            "type": "directory" if target.is_dir() else "file",
            "size_bytes": target.stat().st_size if target.is_file() else None,
        }

    # ------------------------------------------------------------------
    # Tree replication helpers
    # ------------------------------------------------------------------

    _DEFAULT_IGNORE_DIRS = (".venv", "__pycache__", ".git")

    @staticmethod
    def _normalize_ignore_dirs(value: Any) -> tuple[str, ...]:
        if value is None:
            return FileSystemPlugin._DEFAULT_IGNORE_DIRS
        if not isinstance(value, list):
            raise ValueError("ignore_dirs must be a list of strings when provided")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("ignore_dirs entries must be non-empty strings")
            out.append(item.strip())
        return tuple(out)

    def copy_tree(
        self,
        template_dir: str | dict[str, Any],
        destination_dir: str | None = None,
        copy_files: bool = False,
        ignore_dirs: list[str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Mirror a template directory tree into a destination directory.

        Always creates the destination's directory structure. When `copy_files` is True
        also copies files; existing files are skipped unless `overwrite` is True.
        """
        if isinstance(template_dir, dict):
            payload = template_dir
            resolved_template_dir = payload.get("template_dir")
            resolved_destination_dir = payload.get("destination_dir", destination_dir)
            resolved_copy_files = payload.get("copy_files", copy_files)
            resolved_ignore_dirs = payload.get("ignore_dirs", ignore_dirs)
            resolved_overwrite = payload.get("overwrite", overwrite)
        else:
            resolved_template_dir = template_dir
            resolved_destination_dir = destination_dir
            resolved_copy_files = copy_files
            resolved_ignore_dirs = ignore_dirs
            resolved_overwrite = overwrite

        if not isinstance(resolved_template_dir, str) or not resolved_template_dir.strip():
            raise ValueError("template_dir must be a non-empty string")
        if not isinstance(resolved_destination_dir, str) or not resolved_destination_dir.strip():
            raise ValueError("destination_dir must be a non-empty string")
        if not isinstance(resolved_copy_files, bool):
            raise ValueError("copy_files must be a boolean")
        if not isinstance(resolved_overwrite, bool):
            raise ValueError("overwrite must be a boolean")

        ignore_set = set(self._normalize_ignore_dirs(resolved_ignore_dirs))

        template_path = self._resolve_path(resolved_template_dir)
        if not template_path.exists() or not template_path.is_dir():
            raise ValueError("template_dir does not exist")

        destination_path = self._resolve_path(resolved_destination_dir)
        destination_path.mkdir(parents=True, exist_ok=True)

        dirs_created: list[str] = []
        files_copied: list[str] = []
        files_skipped: list[str] = []

        for root, dirs, files in os.walk(template_path):
            dirs[:] = [d for d in dirs if d not in ignore_set]

            rel_path = Path(root).relative_to(template_path)
            target_root = destination_path / rel_path

            if not target_root.exists():
                target_root.mkdir(parents=True, exist_ok=True)
                dirs_created.append(self._relative_label(target_root))

            if resolved_copy_files:
                for fname in files:
                    src_file = Path(root) / fname
                    dst_file = target_root / fname
                    if dst_file.exists() and not resolved_overwrite:
                        files_skipped.append(self._relative_label(dst_file))
                        continue
                    shutil.copy2(src_file, dst_file)
                    files_copied.append(self._relative_label(dst_file))

        return {
            "status": "success",
            "action": "copy_tree",
            "template_dir": self._relative_label(template_path),
            "destination_dir": self._relative_label(destination_path),
            "copy_files": resolved_copy_files,
            "overwrite": resolved_overwrite,
            "ignore_dirs": list(ignore_set),
            "dirs_created_count": len(dirs_created),
            "files_copied_count": len(files_copied),
            "files_skipped_count": len(files_skipped),
            "dirs_created": dirs_created,
            "files_copied": files_copied,
            "files_skipped": files_skipped,
        }

    def deploy_template_to_projects(
        self,
        template_dir: str | dict[str, Any],
        projects_root: str | None = None,
        project_names: list[str] | None = None,
        copy_files: bool = False,
        ignore_dirs: list[str] | None = None,
        overwrite: bool = False,
        stop_on_error: bool = False,
    ) -> dict[str, Any]:
        """Replicate a template tree into many project directories under a root.

        For each name in `project_names`, ensures `<projects_root>/<name>/` exists and
        mirrors the template into it via `copy_tree`. Returns per-project results plus
        an aggregate summary.
        """
        if isinstance(template_dir, dict):
            payload = template_dir
            resolved_template_dir = payload.get("template_dir")
            resolved_projects_root = payload.get("projects_root", projects_root)
            resolved_project_names = payload.get("project_names", project_names)
            resolved_copy_files = payload.get("copy_files", copy_files)
            resolved_ignore_dirs = payload.get("ignore_dirs", ignore_dirs)
            resolved_overwrite = payload.get("overwrite", overwrite)
            resolved_stop_on_error = payload.get("stop_on_error", stop_on_error)
        else:
            resolved_template_dir = template_dir
            resolved_projects_root = projects_root
            resolved_project_names = project_names
            resolved_copy_files = copy_files
            resolved_ignore_dirs = ignore_dirs
            resolved_overwrite = overwrite
            resolved_stop_on_error = stop_on_error

        if not isinstance(resolved_template_dir, str) or not resolved_template_dir.strip():
            raise ValueError("template_dir must be a non-empty string")
        if not isinstance(resolved_projects_root, str) or not resolved_projects_root.strip():
            raise ValueError("projects_root must be a non-empty string")
        if not isinstance(resolved_project_names, list) or not resolved_project_names:
            raise ValueError("project_names must be a non-empty array of strings")
        for name in resolved_project_names:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("project_names entries must be non-empty strings")
        if not isinstance(resolved_stop_on_error, bool):
            raise ValueError("stop_on_error must be a boolean")

        # Resolve once so we can join project names safely.
        template_path = self._resolve_path(resolved_template_dir)
        if not template_path.exists() or not template_path.is_dir():
            raise ValueError("template_dir does not exist")
        projects_root_path = self._resolve_path(resolved_projects_root)
        projects_root_path.mkdir(parents=True, exist_ok=True)

        per_project: list[dict[str, Any]] = []
        total_dirs = 0
        total_files = 0
        total_skipped = 0
        had_errors = False

        print(f"[deploy] Deploying template to {len(resolved_project_names)} project(s)", flush=True)

        for _proj_index, name in enumerate(resolved_project_names, start=1):
            project_dir = projects_root_path / name.strip()
            print(f"[deploy] [{_proj_index}/{len(resolved_project_names)}] {name}", flush=True)
            try:
                # copy_tree expects strings for both sides; pass through resolver
                # via absolute paths so the same allow_outside_base_dir contract holds.
                result = self.copy_tree(
                    template_dir=str(template_path),
                    destination_dir=str(project_dir),
                    copy_files=resolved_copy_files,
                    ignore_dirs=resolved_ignore_dirs,
                    overwrite=resolved_overwrite,
                )
                total_dirs += result["dirs_created_count"]
                total_files += result["files_copied_count"]
                total_skipped += result["files_skipped_count"]
                print(
                    f"[deploy]   OK — dirs={result['dirs_created_count']} "
                    f"files={result['files_copied_count']} skipped={result['files_skipped_count']}",
                    flush=True,
                )
                per_project.append({
                    "project_name": name,
                    "destination_dir": result["destination_dir"],
                    "status": "success",
                    "dirs_created_count": result["dirs_created_count"],
                    "files_copied_count": result["files_copied_count"],
                    "files_skipped_count": result["files_skipped_count"],
                })
            except Exception as exc:
                had_errors = True
                print(f"[deploy]   ERROR: {exc}", flush=True)
                per_project.append({
                    "project_name": name,
                    "destination_dir": self._relative_label(project_dir),
                    "status": "error",
                    "message": str(exc),
                })
                if resolved_stop_on_error:
                    break

        print(
            f"[deploy] Done — projects={len(resolved_project_names)} "
            f"dirs={total_dirs} files={total_files} skipped={total_skipped} errors={had_errors}",
            flush=True,
        )
        return {
            "status": "success" if not had_errors else "partial",
            "action": "deploy_template_to_projects",
            "template_dir": self._relative_label(template_path),
            "projects_root": self._relative_label(projects_root_path),
            "project_count": len(resolved_project_names),
            "had_errors": had_errors,
            "totals": {
                "dirs_created": total_dirs,
                "files_copied": total_files,
                "files_skipped": total_skipped,
            },
            "results": per_project,
        }


