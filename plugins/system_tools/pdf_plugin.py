"""PDF conversion plugin for extracting text and rendering pages as images."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader  # type: ignore[import-not-found]
except Exception:
    PdfReader = None  # type: ignore[assignment]

try:
    import fitz  # type: ignore[import-not-found]
except Exception:
    fitz = None  # type: ignore[assignment]


class PDFPlugin:
    """Convert PDF files to raw text or page images."""

    def __init__(self, base_dir: str = "generated_data", allow_outside_base_dir: bool = True) -> None:
        if not isinstance(base_dir, str) or not base_dir.strip():
            raise ValueError("base_dir must be a non-empty string")
        if not isinstance(allow_outside_base_dir, bool):
            raise ValueError("allow_outside_base_dir must be a boolean")

        self.base_dir = Path(base_dir).resolve()
        self.allow_outside_base_dir = allow_outside_base_dir
        if not self.base_dir.exists() or not self.base_dir.is_dir():
            raise ValueError("base_dir must point to an existing directory")

    def _resolve_path(self, path_value: str) -> Path:
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError("path must be a non-empty string")

        raw = Path(path_value.strip())
        resolved = raw.resolve() if raw.is_absolute() else (self.base_dir / raw).resolve()

        if not self.allow_outside_base_dir:
            try:
                resolved.relative_to(self.base_dir)
            except ValueError as exc:
                raise ValueError("path must be inside base_dir") from exc

        return resolved

    def _normalize_page_numbers(self, pages: list[int] | None, total_pages: int) -> list[int]:
        if pages is None:
            return list(range(1, total_pages + 1))
        if not isinstance(pages, list) or not pages:
            raise ValueError("pages must be a non-empty array of integers when provided")

        normalized: list[int] = []
        for page in pages:
            if not isinstance(page, int) or page < 1:
                raise ValueError("pages must contain integers >= 1")
            if page > total_pages:
                raise ValueError(f"page out of range: {page}; total_pages={total_pages}")
            if page not in normalized:
                normalized.append(page)
        return normalized

    def pdf_to_text(
        self,
        file_path: str | dict[str, Any],
        pages: list[int] | None = None,
        max_chars: int = 20000,
        save_as: str | None = None,
    ) -> dict[str, Any]:
        """Extract text from PDF pages and optionally save to a text file."""
        if PdfReader is None:
            raise ValueError("pypdf is not installed. Install requirements.txt dependencies.")

        if isinstance(file_path, dict):
            payload = file_path
            resolved_file_path = payload.get("file_path")
            resolved_pages = payload.get("pages", pages)
            resolved_max_chars = payload.get("max_chars", max_chars)
            resolved_save_as = payload.get("save_as", save_as)
        else:
            resolved_file_path = file_path
            resolved_pages = pages
            resolved_max_chars = max_chars
            resolved_save_as = save_as

        if not isinstance(resolved_file_path, str) or not resolved_file_path.strip():
            raise ValueError("file_path must be a non-empty string")
        if not isinstance(resolved_max_chars, int) or resolved_max_chars < 1:
            raise ValueError("max_chars must be an integer >= 1")

        pdf_path = self._resolve_path(resolved_file_path)
        if not pdf_path.exists() or not pdf_path.is_file():
            raise ValueError("file_path does not exist")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError("file_path must be a .pdf file")

        try:
            reader = PdfReader(str(pdf_path))
        except Exception as exc:
            raise ValueError(f"Failed to open PDF file: {exc}") from exc

        total_pages = len(reader.pages)
        selected_pages = self._normalize_page_numbers(resolved_pages, total_pages)

        parts: list[str] = []
        consumed = 0
        for page_num in selected_pages:
            try:
                page_text = reader.pages[page_num - 1].extract_text() or ""
            except Exception:
                page_text = ""

            if not page_text:
                continue

            remaining = resolved_max_chars - consumed
            if remaining <= 0:
                break

            trimmed = page_text[:remaining]
            parts.append(f"[Page {page_num}]\n{trimmed}")
            consumed += len(trimmed)

        extracted_text = "\n\n".join(parts).strip()

        output_path: str | None = None
        if isinstance(resolved_save_as, str) and resolved_save_as.strip():
            text_path = self._resolve_path(resolved_save_as)
            if text_path.suffix.lower() not in {".txt", ".md"}:
                raise ValueError("save_as must be a .txt or .md file")
            text_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                text_path.write_text(extracted_text, encoding="utf-8")
            except Exception as exc:
                raise ValueError(f"Failed to write extracted text file: {exc}") from exc
            output_path = str(text_path)

        return {
            "status": "success",
            "action": "pdf_to_text",
            "file_path": str(pdf_path),
            "total_pages": total_pages,
            "pages_processed": selected_pages,
            "char_count": len(extracted_text),
            "text": extracted_text,
            "save_as": output_path,
        }

    def pdf_to_images(
        self,
        file_path: str | dict[str, Any],
        pages: list[int] | None = None,
        output_dir: str = "pdf_images",
        zoom: float = 1.5,
        as_data_urls: bool = False,
        max_pages: int = 5,
    ) -> dict[str, Any]:
        """Render PDF pages to PNG image files and optionally return data URLs."""
        if fitz is None:
            raise ValueError("pymupdf is not installed. Install requirements.txt dependencies.")

        if isinstance(file_path, dict):
            payload = file_path
            resolved_file_path = payload.get("file_path")
            resolved_pages = payload.get("pages", pages)
            resolved_output_dir = payload.get("output_dir", output_dir)
            resolved_zoom = payload.get("zoom", zoom)
            resolved_as_data_urls = payload.get("as_data_urls", as_data_urls)
            resolved_max_pages = payload.get("max_pages", max_pages)
        else:
            resolved_file_path = file_path
            resolved_pages = pages
            resolved_output_dir = output_dir
            resolved_zoom = zoom
            resolved_as_data_urls = as_data_urls
            resolved_max_pages = max_pages

        if not isinstance(resolved_file_path, str) or not resolved_file_path.strip():
            raise ValueError("file_path must be a non-empty string")
        if not isinstance(resolved_output_dir, str) or not resolved_output_dir.strip():
            raise ValueError("output_dir must be a non-empty string")
        if not isinstance(resolved_zoom, (int, float)) or float(resolved_zoom) <= 0:
            raise ValueError("zoom must be a number > 0")
        if not isinstance(resolved_as_data_urls, bool):
            raise ValueError("as_data_urls must be a boolean")
        if not isinstance(resolved_max_pages, int) or resolved_max_pages < 1:
            raise ValueError("max_pages must be an integer >= 1")

        pdf_path = self._resolve_path(resolved_file_path)
        if not pdf_path.exists() or not pdf_path.is_file():
            raise ValueError("file_path does not exist")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError("file_path must be a .pdf file")

        output_path = self._resolve_path(resolved_output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        try:
            document = fitz.open(str(pdf_path))
        except Exception as exc:
            raise ValueError(f"Failed to open PDF file: {exc}") from exc

        total_pages = len(document)
        selected_pages = self._normalize_page_numbers(resolved_pages, total_pages)
        limited_pages = selected_pages[:resolved_max_pages]

        safe_stem = pdf_path.stem or "pdf"
        image_paths: list[str] = []
        image_data_urls: list[str] = []

        try:
            for page_num in limited_pages:
                page_index = page_num - 1
                try:
                    page = document.load_page(page_index)
                    pix = page.get_pixmap(matrix=fitz.Matrix(float(resolved_zoom), float(resolved_zoom)), alpha=False)
                    png_bytes = pix.tobytes("png")
                except Exception as exc:
                    raise ValueError(f"Failed to render page {page_num}: {exc}") from exc

                image_file_path = output_path / f"{safe_stem}_page_{page_num}.png"
                try:
                    image_file_path.write_bytes(png_bytes)
                except Exception as exc:
                    raise ValueError(f"Failed to write image file for page {page_num}: {exc}") from exc

                image_paths.append(str(image_file_path))

                if resolved_as_data_urls:
                    encoded = base64.b64encode(png_bytes).decode("ascii")
                    image_data_urls.append(f"data:image/png;base64,{encoded}")
        finally:
            document.close()

        return {
            "status": "success",
            "action": "pdf_to_images",
            "file_path": str(pdf_path),
            "total_pages": total_pages,
            "pages_processed": limited_pages,
            "images_created": len(image_paths),
            "output_dir": str(output_path),
            "image_paths": image_paths,
            "image_data_urls": image_data_urls if resolved_as_data_urls else [],
        }
