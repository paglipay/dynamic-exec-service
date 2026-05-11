"""PDF conversion plugin for extracting text and rendering pages as images."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader, PdfWriter  # type: ignore[import-not-found]
    from pypdf.generic import BooleanObject, NameObject, TextStringObject  # type: ignore[import-not-found]
except Exception:
    PdfReader = None  # type: ignore[assignment]
    PdfWriter = None  # type: ignore[assignment]
    BooleanObject = None  # type: ignore[assignment]
    NameObject = None  # type: ignore[assignment]
    TextStringObject = None  # type: ignore[assignment]

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

    # ------------------------------------------------------------------
    # AcroForm helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _acroform_root(pdf: Any) -> Any:
        """Return the /AcroForm dictionary for either a PdfReader or PdfWriter."""
        root = getattr(pdf, "_root_object", None)
        if root is None:
            try:
                root = pdf.trailer["/Root"]
            except Exception as exc:  # pragma: no cover - defensive
                raise ValueError(f"Unable to locate PDF root: {exc}") from exc
        af = root.get("/AcroForm") if root is not None else None
        if af is None:
            raise ValueError("PDF has no /AcroForm — it is not a fillable form")
        return af.get_object()

    @classmethod
    def _index_form_fields(cls, pdf: Any) -> dict[str, Any]:
        """Return a {field_name: field_object} mapping for every AcroForm field."""
        af = cls._acroform_root(pdf)
        fields_array = af.get("/Fields")
        if fields_array is None:
            return {}
        out: dict[str, Any] = {}
        for ref in fields_array:
            field = ref.get_object()
            name = field.get("/T")
            if name is not None:
                out[str(name)] = field
        return out

    @staticmethod
    def _checkbox_states(field: Any) -> tuple[Any, list[str]]:
        """Return (on_state_NameObject, list_of_all_state_names) for a /Btn field."""
        ap = field.get("/AP")
        if ap is None:
            return NameObject("/Yes"), ["/Off", "/Yes"]
        n = ap.get_object().get("/N")
        if n is None:
            return NameObject("/Yes"), ["/Off", "/Yes"]
        states = [str(s) for s in n.get_object().keys()]
        on_state = next((s for s in states if s != "/Off"), "/Yes")
        return NameObject(on_state), states

    @staticmethod
    def _classify_field(field: Any) -> str:
        """Map a raw PDF field type to a human-friendly category."""
        ftype = field.get("/FT")
        if ftype == "/Tx":
            return "text"
            # text input
        if ftype == "/Btn":
            flags = int(field.get("/Ff", 0) or 0)
            # bit 16 (0x10000) = Pushbutton, bit 15 (0x8000) = Radio
            if flags & 0x10000:
                return "pushbutton"
            if flags & 0x8000:
                return "radio"
            return "checkbox"
        if ftype == "/Ch":
            return "choice"
        if ftype == "/Sig":
            return "signature"
        return "unknown"

    @staticmethod
    def _resolve_payload(
        first_arg: Any,
        defaults: dict[str, Any],
    ) -> dict[str, Any]:
        """Allow the first positional arg to be either a dict payload or a string file_path."""
        if isinstance(first_arg, dict):
            merged = dict(defaults)
            merged.update(first_arg)
            return merged
        merged = dict(defaults)
        merged["file_path"] = first_arg
        return merged

    def list_pdf_form_fields(
        self,
        file_path: str | dict[str, Any],
        include_values: bool = True,
    ) -> dict[str, Any]:
        """Enumerate every AcroForm field in a PDF.

        Returns each field's name, category (text/checkbox/radio/choice/signature),
        current value, and—for buttons—the discovered "on" state plus all known states.
        """
        if PdfReader is None:
            raise ValueError("pypdf is not installed. Install requirements.txt dependencies.")

        payload = self._resolve_payload(
            file_path,
            {"file_path": None, "include_values": include_values},
        )
        resolved_file_path = payload.get("file_path")
        resolved_include_values = payload.get("include_values", include_values)

        if not isinstance(resolved_file_path, str) or not resolved_file_path.strip():
            raise ValueError("file_path must be a non-empty string")
        if not isinstance(resolved_include_values, bool):
            raise ValueError("include_values must be a boolean")

        pdf_path = self._resolve_path(resolved_file_path)
        if not pdf_path.exists() or not pdf_path.is_file():
            raise ValueError("file_path does not exist")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError("file_path must be a .pdf file")

        try:
            reader = PdfReader(str(pdf_path))
        except Exception as exc:
            raise ValueError(f"Failed to open PDF file: {exc}") from exc

        try:
            fields = self._index_form_fields(reader)
        except ValueError as exc:
            return {
                "status": "success",
                "action": "list_pdf_form_fields",
                "file_path": str(pdf_path),
                "field_count": 0,
                "fields": [],
                "note": str(exc),
            }

        descriptors: list[dict[str, Any]] = []
        for name, field in fields.items():
            category = self._classify_field(field)
            descriptor: dict[str, Any] = {
                "name": name,
                "type": category,
                "raw_type": str(field.get("/FT")) if field.get("/FT") else None,
            }

            if resolved_include_values:
                current = field.get("/V")
                descriptor["current_value"] = str(current) if current is not None else None

            if category in {"checkbox", "radio"}:
                on_state, all_states = self._checkbox_states(field)
                descriptor["on_state"] = str(on_state)
                descriptor["available_states"] = all_states

            if category == "choice":
                opts = field.get("/Opt")
                if opts is not None:
                    try:
                        descriptor["options"] = [
                            (str(o[0]) if isinstance(o, list) and o else str(o))
                            for o in opts.get_object()
                        ]
                    except Exception:
                        descriptor["options"] = None

            descriptors.append(descriptor)

        return {
            "status": "success",
            "action": "list_pdf_form_fields",
            "file_path": str(pdf_path),
            "field_count": len(descriptors),
            "fields": descriptors,
        }

    def fill_pdf_form(
        self,
        file_path: str | dict[str, Any],
        field_values: dict[str, Any] | None = None,
        output_path: str | None = None,
        flatten: bool = False,
        ignore_unknown: bool = True,
    ) -> dict[str, Any]:
        """Fill an AcroForm PDF with the provided field values and write a new PDF.

        Text fields take strings; checkboxes take booleans (the "on" state name is
        auto-discovered per field); radio buttons take the option name (e.g. "/Choice1");
        choice/dropdown fields take the selected value. Returns a structured report of
        which fields were filled, skipped, or unknown.
        """
        if PdfReader is None or PdfWriter is None:
            raise ValueError("pypdf is not installed. Install requirements.txt dependencies.")

        payload = self._resolve_payload(
            file_path,
            {
                "file_path": None,
                "field_values": field_values,
                "output_path": output_path,
                "flatten": flatten,
                "ignore_unknown": ignore_unknown,
            },
        )
        resolved_file_path = payload.get("file_path")
        resolved_field_values = payload.get("field_values")
        resolved_output_path = payload.get("output_path")
        resolved_flatten = payload.get("flatten", flatten)
        resolved_ignore_unknown = payload.get("ignore_unknown", ignore_unknown)

        if not isinstance(resolved_file_path, str) or not resolved_file_path.strip():
            raise ValueError("file_path must be a non-empty string")
        if not isinstance(resolved_field_values, dict) or not resolved_field_values:
            raise ValueError("field_values must be a non-empty object of {field_name: value}")
        if resolved_output_path is not None and (
            not isinstance(resolved_output_path, str) or not resolved_output_path.strip()
        ):
            raise ValueError("output_path must be a non-empty string when provided")
        if not isinstance(resolved_flatten, bool):
            raise ValueError("flatten must be a boolean")
        if not isinstance(resolved_ignore_unknown, bool):
            raise ValueError("ignore_unknown must be a boolean")

        pdf_path = self._resolve_path(resolved_file_path)
        if not pdf_path.exists() or not pdf_path.is_file():
            raise ValueError("file_path does not exist")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError("file_path must be a .pdf file")

        if resolved_output_path:
            out_path = self._resolve_path(resolved_output_path)
        else:
            out_path = pdf_path.with_name(f"{pdf_path.stem}_filled.pdf")
        if out_path.suffix.lower() != ".pdf":
            raise ValueError("output_path must end with .pdf")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            reader = PdfReader(str(pdf_path))
            writer = PdfWriter(clone_from=reader)
        except Exception as exc:
            raise ValueError(f"Failed to open PDF file: {exc}") from exc

        try:
            fields = self._index_form_fields(writer)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        filled: list[dict[str, Any]] = []
        unknown: list[str] = []
        unsupported: list[dict[str, Any]] = []
        text_updates: dict[str, str] = {}

        for name, value in resolved_field_values.items():
            if name not in fields:
                unknown.append(name)
                continue

            field = fields[name]
            category = self._classify_field(field)

            if category == "text":
                text_updates[name] = "" if value is None else str(value)
                filled.append({"name": name, "type": "text"})
            elif category == "checkbox":
                on_state, _ = self._checkbox_states(field)
                new_state = on_state if bool(value) else NameObject("/Off")
                field[NameObject("/V")] = new_state
                field[NameObject("/AS")] = new_state
                filled.append({"name": name, "type": "checkbox", "value": str(new_state)})
            elif category == "radio":
                # For radio groups, value is the option name (e.g. "/Choice1") or bool
                _, states = self._checkbox_states(field)
                if isinstance(value, bool):
                    target = NameObject(states[1]) if value and len(states) > 1 else NameObject("/Off")
                else:
                    raw = str(value)
                    if not raw.startswith("/"):
                        raw = f"/{raw}"
                    target = NameObject(raw if raw in states else "/Off")
                field[NameObject("/V")] = target
                field[NameObject("/AS")] = target
                filled.append({"name": name, "type": "radio", "value": str(target)})
            elif category == "choice":
                field[NameObject("/V")] = TextStringObject("" if value is None else str(value))
                filled.append({"name": name, "type": "choice", "value": str(value)})
            else:
                unsupported.append({"name": name, "type": category})

        if unknown and not resolved_ignore_unknown:
            raise ValueError(f"Unknown field name(s): {', '.join(unknown)}")

        # Apply text updates per page (pypdf groups updates by page).
        for page in writer.pages:
            per_page: dict[str, str] = {}
            annots = page.get("/Annots")
            if not annots:
                continue
            for ref in annots.get_object():
                f = ref.get_object()
                t = f.get("/T")
                if t and str(t) in text_updates:
                    per_page[str(t)] = text_updates[str(t)]
            if per_page:
                writer.update_page_form_field_values(page, per_page)

        # Tell viewers to regenerate field appearances.
        try:
            af = writer._root_object["/AcroForm"].get_object()
            af[NameObject("/NeedAppearances")] = BooleanObject(True)
        except Exception:
            pass

        if resolved_flatten:
            try:
                # pypdf >= 3.x exposes flatten() on Writer / individual pages
                if hasattr(writer, "flatten"):
                    writer.flatten()
                else:
                    for page in writer.pages:
                        if hasattr(page, "flatten"):
                            page.flatten()
            except Exception as exc:
                # Flatten is best-effort; surface a note rather than failing the call.
                unsupported.append({"name": "<flatten>", "type": "flatten_error", "error": str(exc)})

        try:
            with open(out_path, "wb") as fh:
                writer.write(fh)
        except Exception as exc:
            raise ValueError(f"Failed to write filled PDF: {exc}") from exc

        return {
            "status": "success",
            "action": "fill_pdf_form",
            "file_path": str(pdf_path),
            "output_path": str(out_path),
            "fields_total_in_pdf": len(fields),
            "filled_count": len(filled),
            "filled": filled,
            "unknown": unknown,
            "unsupported": unsupported,
            "flattened": bool(resolved_flatten),
        }
