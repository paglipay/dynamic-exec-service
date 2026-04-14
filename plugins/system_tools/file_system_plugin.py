"""Filesystem plugin restricted to operations inside a base directory."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any


class FileSystemPlugin:
    """Read and mutate filesystem paths within a confined base directory."""

    def __init__(self, base_dir: str = "generated_data") -> None:
        if not isinstance(base_dir, str) or not base_dir.strip():
            raise ValueError("base_dir must be a non-empty string")

        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

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
        if candidate.is_absolute():
            raise ValueError("Absolute paths are not allowed")

        target = (self.base_dir / candidate).resolve()
        try:
            target.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError("Invalid path") from exc

        return target

    def list_directory(self, directory: str = ".") -> dict[str, Any]:
        target = self._resolve_path(directory, allow_current=True)
        if not target.exists() or not target.is_dir():
            raise ValueError("directory does not exist")

        entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        return {
            "status": "success",
            "base_dir": str(self.base_dir),
            "directory": str(target.relative_to(self.base_dir)).replace("\\", "/") or ".",
            "entries": [
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "relative_path": str(entry.relative_to(self.base_dir)).replace("\\", "/"),
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
            "directory": str(target.relative_to(self.base_dir)).replace("\\", "/"),
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
            "source": str(source.relative_to(self.base_dir)).replace("\\", "/"),
            "destination": str(moved_path.relative_to(self.base_dir)).replace("\\", "/"),
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
            "target": str(target.relative_to(self.base_dir)).replace("\\", "/"),
            "recursive": recursive,
        }

    def path_info(self, target_path: str = ".") -> dict[str, Any]:
        target = self._resolve_path(target_path, allow_current=True)
        if not target.exists():
            raise ValueError("target path does not exist")

        relative_path = str(target.relative_to(self.base_dir)).replace("\\", "/") or "."
        return {
            "status": "success",
            "base_dir": str(self.base_dir),
            "target": relative_path,
            "type": "directory" if target.is_dir() else "file",
            "size_bytes": target.stat().st_size if target.is_file() else None,
        }

    def create_zip_archive(self, file_paths: list[str], output_zip_path: str) -> dict[str, Any]:
        """Create a ZIP archive from a list of files.
        
        Args:
            file_paths: List of relative paths to files/directories to include
            output_zip_path: Relative path where the ZIP archive should be created
            
        Returns:
            Status dict with the created ZIP file path
        """
        if not isinstance(file_paths, list):
            raise ValueError("file_paths must be a list")
        if not file_paths:
            raise ValueError("file_paths list cannot be empty")
        if not isinstance(output_zip_path, str) or not output_zip_path.strip():
            raise ValueError("output_zip_path must be a non-empty string")

        # Validate all source files exist
        resolved_files = []
        for file_path in file_paths:
            try:
                resolved = self._resolve_path(file_path)
                if not resolved.exists():
                    raise ValueError(f"file does not exist: {file_path}")
                resolved_files.append(resolved)
            except ValueError as exc:
                raise ValueError(f"Invalid file path '{file_path}': {exc}") from exc

        # Resolve output zip path
        output_zip = self._resolve_path(output_zip_path)
        if output_zip.exists():
            raise ValueError("output_zip_path already exists")

        # Create parent directory if needed
        output_zip.parent.mkdir(parents=True, exist_ok=True)

        # Create the ZIP archive
        try:
            with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for resolved_path in resolved_files:
                    if resolved_path.is_file():
                        arcname = str(resolved_path.relative_to(self.base_dir)).replace("\\", "/")
                        zf.write(resolved_path, arcname=arcname)
                    elif resolved_path.is_dir():
                        for item in resolved_path.rglob("*"):
                            if item.is_file():
                                arcname = str(item.relative_to(self.base_dir)).replace("\\", "/")
                                zf.write(item, arcname=arcname)
        except Exception as exc:
            # Clean up if something went wrong
            if output_zip.exists():
                output_zip.unlink()
            raise ValueError(f"Failed to create ZIP archive: {exc}") from exc

        return {
            "status": "success",
            "action": "create_zip_archive",
            "output_zip": str(output_zip.relative_to(self.base_dir)).replace("\\", "/"),
            "files_included": len(resolved_files),
            "size_bytes": output_zip.stat().st_size,
        }
