"""Generated text file CRUD plugin module."""

from __future__ import annotations

from pathlib import Path


class TextFileCRUDPlugin:
    """CRUD operations for .txt/.md files within a base directory."""

    def __init__(self, base_dir: str = "generated_data") -> None:
        if not isinstance(base_dir, str) or not base_dir:
            raise ValueError("base_dir must be a non-empty string")
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_filename(self, filename: str) -> Path:
        if not isinstance(filename, str) or not filename:
            raise ValueError("filename must be a non-empty string")
        if not filename.endswith((".txt", ".md")):
            raise ValueError("Only .txt and .md files are allowed")
        if "/" in filename or "\\" in filename:
            raise ValueError("filename must not contain path separators")

        file_path = (self.base_dir / filename).resolve()
        if file_path.parent != self.base_dir:
            raise ValueError("Invalid file path")
        return file_path

    def create_text(self, filename: str, content: str):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        file_path = self._resolve_filename(filename)
        if file_path.exists():
            raise ValueError("File already exists")
        file_path.write_text(content, encoding="utf-8")
        return {"status": "success", "action": "create", "filename": filename}

    def read_text(self, filename: str):
        file_path = self._resolve_filename(filename)
        if not file_path.exists():
            raise ValueError("File does not exist")
        return {"status": "success", "action": "read", "filename": filename, "content": file_path.read_text(encoding="utf-8")}

    def update_text(self, filename: str, content: str):
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        file_path = self._resolve_filename(filename)
        if not file_path.exists():
            raise ValueError("File does not exist")
        file_path.write_text(content, encoding="utf-8")
        return {"status": "success", "action": "update", "filename": filename}

    def delete_text(self, filename: str):
        file_path = self._resolve_filename(filename)
        if not file_path.exists():
            raise ValueError("File does not exist")
        file_path.unlink()
        return {"status": "success", "action": "delete", "filename": filename}

    def list_text_files(self):
        files = sorted(
            path.name
            for path in self.base_dir.iterdir()
            if path.is_file() and path.suffix in {".txt", ".md"}
        )
        return {"status": "success", "action": "list", "files": files}
