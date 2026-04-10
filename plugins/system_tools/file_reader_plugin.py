"""File reader plugin for reading and extracting content from files on disk.

Provides a unified interface for OpenAI function calls to read text, PDF,
DOCX, CSV/TSV, and Excel files stored under the configured base directory
(default: generated_data/).  Pass an absolute path when allow_outside_base_dir
is True (the default) to reach any file on the filesystem, or use relative
paths within base_dir.

Typical Slack-sourced files land in:
  generated_data/slack_downloads/<filename>

Any file in the filesystem can be read by the OpenAI agent using the same
methods, making Slack-uploaded and pre-existing files interchangeable.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any


class FileReaderPlugin:
    """Read and extract content from files, scoped to a base directory.

    Constructor args:
        base_dir (str): Root directory for relative path resolution.
                        Defaults to "generated_data".
        allow_outside_base_dir (bool): When True (default), absolute paths
                        are accepted so saved-file paths from other plugins
                        work without conversion.
    """

    def __init__(
        self,
        base_dir: str = "generated_data",
        allow_outside_base_dir: bool = True,
    ) -> None:
        if not isinstance(base_dir, str) or not base_dir.strip():
            raise ValueError("base_dir must be a non-empty string")
        if not isinstance(allow_outside_base_dir, bool):
            raise ValueError("allow_outside_base_dir must be a boolean")

        self._base = Path(base_dir).resolve()
        self._allow_outside = allow_outside_base_dir

        if not self._base.exists():
            self._base.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    def _resolve_path(self, path_value: str) -> Path:
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError("path must be a non-empty string")

        raw = Path(path_value.strip())
        resolved = raw.resolve() if raw.is_absolute() else (self._base / raw).resolve()

        if not self._allow_outside:
            try:
                resolved.relative_to(self._base)
            except ValueError as exc:
                raise ValueError("path must be inside base_dir") from exc

        return resolved

    # ---------------------------------------------------------------------------
    # Public methods
    # ---------------------------------------------------------------------------

    def list_directory(self, directory: str = ".") -> dict[str, Any]:
        """List files and subdirectories within the base directory.

        Args:
            directory: Relative path within base_dir to list. Defaults to root.
        """
        if not isinstance(directory, str) or not directory.strip():
            directory = "."

        raw = Path(directory.strip())
        if raw.is_absolute():
            raise ValueError("directory must be a relative path")

        target = (self._base / raw).resolve()
        try:
            target.relative_to(self._base)
        except ValueError as exc:
            raise ValueError("directory must be within base_dir") from exc

        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(f"directory does not exist: {directory}")

        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        return {
            "status": "success",
            "base_dir": str(self._base),
            "directory": str(target.relative_to(self._base)).replace("\\", "/") or ".",
            "entries": [
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "relative_path": str(entry.relative_to(self._base)).replace("\\", "/"),
                    **({"size_bytes": entry.stat().st_size} if entry.is_file() else {}),
                }
                for entry in entries
            ],
        }

    def read_text_file(self, file_path: str, max_chars: int = 20000) -> dict[str, Any]:
        """Read the contents of a plain text or markdown file.

        Args:
            file_path: Path to the .txt or .md file (relative to base_dir or absolute).
            max_chars: Maximum characters to return. Defaults to 20000.
        """
        if not isinstance(max_chars, int) or max_chars < 1:
            raise ValueError("max_chars must be an integer >= 1")

        path = self._resolve_path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file not found: {file_path}")
        if path.suffix.lower() not in {".txt", ".md", ".text", ".log", ".csv", ".tsv"}:
            raise ValueError("read_text_file supports .txt, .md, .text, .log, .csv, and .tsv files")

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"Failed to read file: {exc}") from exc

        truncated = len(content) > max_chars
        return {
            "status": "success",
            "file_path": str(path),
            "relative_path": str(path.relative_to(self._base)).replace("\\", "/") if path.is_relative_to(self._base) else None,
            "size_bytes": path.stat().st_size,
            "char_count": len(content),
            "truncated": truncated,
            "content": content[:max_chars],
        }

    def read_pdf_text(self, file_path: str, max_chars: int = 20000) -> dict[str, Any]:
        """Extract text content from a PDF file.

        Args:
            file_path: Path to the .pdf file (relative to base_dir or absolute).
            max_chars: Maximum characters to return across all pages. Defaults to 20000.
        """
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
        except Exception as exc:
            raise ValueError("pypdf is not installed. Install requirements.txt dependencies.") from exc

        if not isinstance(max_chars, int) or max_chars < 1:
            raise ValueError("max_chars must be an integer >= 1")

        path = self._resolve_path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file not found: {file_path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError("file_path must be a .pdf file")

        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            raise ValueError(f"Failed to open PDF: {exc}") from exc

        page_count = len(reader.pages)
        chunks: list[str] = []
        consumed = 0

        for i, page in enumerate(reader.pages, start=1):
            if consumed >= max_chars:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if not text.strip():
                continue
            remaining = max_chars - consumed
            trimmed = text[:remaining]
            chunks.append(f"[Page {i}]\n{trimmed}")
            consumed += len(trimmed)

        extracted = "\n\n".join(chunks).strip()
        return {
            "status": "success",
            "file_path": str(path),
            "relative_path": str(path.relative_to(self._base)).replace("\\", "/") if path.is_relative_to(self._base) else None,
            "page_count": page_count,
            "char_count": len(extracted),
            "truncated": consumed >= max_chars,
            "text": extracted,
        }

    def read_docx_text(self, file_path: str, max_chars: int = 20000) -> dict[str, Any]:
        """Extract text content from a DOCX file, including table cell content.

        Args:
            file_path: Path to the .docx file (relative to base_dir or absolute).
            max_chars: Maximum characters to return. Defaults to 20000.
        """
        try:
            from docx import Document as DocxDocument  # type: ignore[import-not-found]
        except Exception as exc:
            raise ValueError("python-docx is not installed. Install requirements.txt dependencies.") from exc

        if not isinstance(max_chars, int) or max_chars < 1:
            raise ValueError("max_chars must be an integer >= 1")

        path = self._resolve_path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file not found: {file_path}")
        if path.suffix.lower() not in {".docx", ".doc"}:
            raise ValueError("file_path must be a .docx file")

        try:
            document = DocxDocument(str(path))
        except Exception as exc:
            raise ValueError(f"Failed to open DOCX: {exc}") from exc

        chunks: list[str] = []
        current_size = 0

        def _add(text: str) -> bool:
            nonlocal current_size
            text = text.strip()
            if not text:
                return True
            remaining = max_chars - current_size
            if remaining <= 0:
                return False
            trimmed = text[:remaining]
            chunks.append(trimmed)
            current_size += len(trimmed)
            return current_size < max_chars

        for paragraph in getattr(document, "paragraphs", []):
            try:
                text = str(paragraph.text or "")
            except Exception:
                text = ""
            if not _add(text):
                break

        for table in getattr(document, "tables", []):
            try:
                rows = getattr(table, "rows", [])
            except Exception:
                continue
            for row in rows:
                try:
                    cells = getattr(row, "cells", [])
                    cell_texts = [str(getattr(c, "text", "") or "").strip() for c in cells]
                    row_text = "\t".join(cell_texts)
                except Exception:
                    row_text = ""
                if not _add(row_text):
                    break

        extracted = "\n".join(chunks).strip()
        return {
            "status": "success",
            "file_path": str(path),
            "relative_path": str(path.relative_to(self._base)).replace("\\", "/") if path.is_relative_to(self._base) else None,
            "char_count": len(extracted),
            "truncated": current_size >= max_chars,
            "text": extracted,
        }

    def parse_csv_tsv(
        self,
        file_path: str,
        max_rows: int = 25,
        delimiter: str = "auto",
    ) -> dict[str, Any]:
        """Parse a CSV or TSV file and return rows as a list of dicts.

        Args:
            file_path: Path to the .csv or .tsv file (relative to base_dir or absolute).
            max_rows: Maximum data rows to return (excludes header). Defaults to 25.
            delimiter: Column separator — "auto" (detect), "comma", or "tab". Defaults to "auto".
        """
        if not isinstance(max_rows, int) or max_rows < 1:
            raise ValueError("max_rows must be an integer >= 1")
        if delimiter not in {"auto", "comma", "tab", ",", "\t"}:
            raise ValueError("delimiter must be 'auto', 'comma', or 'tab'")

        path = self._resolve_path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file not found: {file_path}")
        if path.suffix.lower() not in {".csv", ".tsv", ".txt"}:
            raise ValueError("file_path must be a .csv, .tsv, or .txt file")

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"Failed to read file: {exc}") from exc

        if delimiter == "auto":
            sep = "\t" if path.suffix.lower() == ".tsv" or "\t" in (content[:2000]) else ","
        elif delimiter in {"tab", "\t"}:
            sep = "\t"
        else:
            sep = ","

        lines = [line for line in content.splitlines() if line.strip()]
        if len(lines) < 2:
            return {
                "status": "success",
                "file_path": str(path),
                "row_count": 0,
                "headers": [],
                "rows": [],
            }

        reader = csv.reader(lines, delimiter=sep)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return {"status": "success", "file_path": str(path), "row_count": 0, "headers": [], "rows": []}

        headers = [str(h).strip() for h in raw_headers]
        rows: list[dict[str, str]] = []
        for row in reader:
            if len(rows) >= max_rows:
                break
            values = [str(v).strip() for v in row]
            if not any(values):
                continue
            values = (values + [""] * len(headers))[: len(headers)]
            row_data = {h: values[i] for i, h in enumerate(headers) if h}
            if row_data:
                rows.append(row_data)

        return {
            "status": "success",
            "file_path": str(path),
            "relative_path": str(path.relative_to(self._base)).replace("\\", "/") if path.is_relative_to(self._base) else None,
            "delimiter_used": "tab" if sep == "\t" else "comma",
            "headers": headers,
            "row_count": len(rows),
            "rows": rows,
        }

    def summarize_excel(self, file_path: str, max_preview_rows: int = 5) -> dict[str, Any]:
        """Return a workbook summary including sheet names and a first-sheet preview.

        Args:
            file_path: Path to the .xlsx/.xlsm/.xls file (relative to base_dir or absolute).
            max_preview_rows: Rows to include in the first-sheet preview. Defaults to 5.
        """
        if not isinstance(max_preview_rows, int) or max_preview_rows < 1:
            raise ValueError("max_preview_rows must be an integer >= 1")

        path = self._resolve_path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file not found: {file_path}")
        if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
            raise ValueError("file_path must be an Excel file (.xlsx, .xlsm, .xls)")

        from plugins.system_tools.excel_plugin import ExcelPlugin  # noqa: PLC0415

        plugin = ExcelPlugin(base_dir=str(self._base), allow_outside_base_dir=True)

        sheet_result = plugin.list_sheet_names(str(path))
        sheet_names = sheet_result.get("sheet_names", [])
        if not isinstance(sheet_names, list):
            sheet_names = []

        summary: dict[str, Any] = {
            "status": "success",
            "file_path": str(path),
            "relative_path": str(path.relative_to(self._base)).replace("\\", "/") if path.is_relative_to(self._base) else None,
            "sheet_count": len(sheet_names),
            "sheet_names": sheet_names,
        }

        if sheet_names:
            try:
                preview_result = plugin.preview_sheet(
                    {
                        "file_path": str(path),
                        "sheet": sheet_names[0],
                        "max_rows": max_preview_rows,
                    }
                )
                summary["first_sheet_preview"] = {
                    "sheet_name": preview_result.get("sheet_name"),
                    "column_names": preview_result.get("column_names"),
                    "total_row_count": preview_result.get("total_row_count"),
                    "preview_row_count": preview_result.get("preview_row_count"),
                    "preview_rows": preview_result.get("preview_rows"),
                }
            except Exception as exc:
                summary["first_sheet_preview_error"] = str(exc)

        return summary

    def read_image_for_vision(
        self,
        file_path: str,
        max_long_edge: int = 1024,
    ) -> dict[str, Any]:
        """Read an image file and return it as a base64-encoded data URL for vision analysis.

        Args:
            file_path: Path to the image (relative to base_dir or absolute).
            max_long_edge: Resize so the longest edge is at most this many pixels (default 1024).

        Returns:
            {"status": "success", "data_url": "data:image/jpeg;base64,...",
             "width": int, "height": int, "original_width": int, "original_height": int,
             "mime": str, "file_name": str}
        """
        import base64
        import mimetypes

        resolved = self._resolve_path(file_path)
        if not resolved.exists() or not resolved.is_file():
            # Fall back to resolving from CWD so paths like "media_storage/..." work
            # even when base_dir is "generated_data".
            cwd_resolved = Path(file_path.strip()).resolve()
            if cwd_resolved.exists() and cwd_resolved.is_file():
                resolved = cwd_resolved
            else:
                raise ValueError(f"Image file does not exist: {file_path}")

        raw_bytes = resolved.read_bytes()

        # Detect MIME type from extension
        mime, _ = mimetypes.guess_type(resolved.name)
        if not isinstance(mime, str) or not mime.startswith("image/"):
            # Check magic bytes for common formats
            if raw_bytes[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif raw_bytes[:3] == b"\xff\xd8\xff":
                mime = "image/jpeg"
            elif raw_bytes[:6] in (b"GIF87a", b"GIF89a"):
                mime = "image/gif"
            elif raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                mime = "image/png"

        original_width = original_height = width = height = 0
        try:
            from PIL import Image as _PILImage

            with _PILImage.open(io.BytesIO(raw_bytes)) as img:
                original_width, original_height = img.size
                if max_long_edge > 0 and max(img.size) > max_long_edge:
                    ratio = max_long_edge / max(img.size)
                    new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
                    img = img.resize(new_size, _PILImage.LANCZOS)
                width, height = img.size
                buf = io.BytesIO()
                save_format = "JPEG" if mime == "image/jpeg" else "PNG"
                save_kwargs: dict[str, Any] = {"format": save_format}
                if save_format == "JPEG":
                    save_kwargs["quality"] = 85
                img.convert("RGB" if save_format == "JPEG" else "RGBA" if img.mode == "RGBA" else "RGB").save(
                    buf, **save_kwargs
                )
                raw_bytes = buf.getvalue()
        except Exception:
            # PIL unavailable or failed — use raw bytes as-is
            width = original_width
            height = original_height

        encoded = base64.b64encode(raw_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{encoded}"

        return {
            "status": "success",
            "data_url": data_url,
            "mime": mime,
            "file_name": resolved.name,
            "width": width,
            "height": height,
            "original_width": original_width,
            "original_height": original_height,
        }
