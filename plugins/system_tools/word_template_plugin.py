"""Word template plugin for token replacement and optional PDF export."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


class WordTemplatePlugin:
    """Generate per-school DOCX outputs from templates and optional PDF export."""

    def __init__(self, base_dir: str = "generated_data", allow_outside_base_dir: bool = True) -> None:
        if not isinstance(base_dir, str) or not base_dir.strip():
            raise ValueError("base_dir must be a non-empty string")
        if not isinstance(allow_outside_base_dir, bool):
            raise ValueError("allow_outside_base_dir must be a boolean")

        self.base_dir = Path(base_dir).resolve()
        self.allow_outside_base_dir = allow_outside_base_dir

        if not self.base_dir.exists() or not self.base_dir.is_dir():
            raise ValueError("base_dir must point to an existing directory")

    def _resolve_path(self, path_value: str, require_exists: bool = False) -> Path:
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError("path must be a non-empty string")

        raw = Path(path_value.strip())
        resolved = raw.resolve() if raw.is_absolute() else (self.base_dir / raw).resolve()

        if not self.allow_outside_base_dir:
            try:
                resolved.relative_to(self.base_dir)
            except ValueError as exc:
                raise ValueError("path must be inside base_dir") from exc

        if require_exists and not resolved.exists():
            raise ValueError(f"path does not exist: {resolved}")

        return resolved

    def _load_json_array(self, path: Path) -> list[Any]:
        if not path.exists() or not path.is_file():
            raise ValueError(f"JSON file not found: {path}")

        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            raise ValueError(f"Failed to read JSON file: {path}") from exc

        if not isinstance(data, list):
            raise ValueError(f"Expected top-level JSON array in file: {path}")

        return data

    def _load_document(self, path: Path) -> Any:
        try:
            from docx import Document  # type: ignore[import-not-found]
        except Exception as exc:
            raise ValueError("python-docx is not installed. Install requirements.txt dependencies.") from exc

        return Document(path)

    def _replace_in_paragraph_preserve_format(self, paragraph: Any, replacements: list[tuple[str, str]]) -> bool:
        changed = False

        for run in paragraph.runs:
            original = run.text
            updated = original
            for old, new in replacements:
                updated = updated.replace(old, new)
            if updated != original:
                run.text = updated
                changed = True

        for old, new in replacements:
            if not old:
                continue

            while True:
                full_text = "".join(run.text for run in paragraph.runs)
                start = full_text.find(old)
                if start == -1:
                    break

                end = start + len(old)
                pos = 0
                start_run = -1
                start_offset = -1
                end_run = -1
                end_offset = -1

                for idx, run in enumerate(paragraph.runs):
                    run_len = len(run.text)
                    next_pos = pos + run_len

                    if start_run == -1 and start < next_pos:
                        start_run = idx
                        start_offset = start - pos

                    if end_run == -1 and end <= next_pos:
                        end_run = idx
                        end_offset = end - pos
                        break

                    pos = next_pos

                if start_run == -1 or end_run == -1:
                    break

                if start_run == end_run:
                    run = paragraph.runs[start_run]
                    text = run.text
                    run.text = text[:start_offset] + new + text[end_offset:]
                else:
                    first_run = paragraph.runs[start_run]
                    last_run = paragraph.runs[end_run]

                    prefix = first_run.text[:start_offset]
                    suffix = last_run.text[end_offset:]

                    first_run.text = prefix + new
                    for idx in range(start_run + 1, end_run):
                        paragraph.runs[idx].text = ""
                    last_run.text = suffix

                changed = True

        return changed

    def _replace_in_document(self, document: Any, replacements: list[tuple[str, str]]) -> int:
        changed_count = 0

        for paragraph in document.paragraphs:
            if self._replace_in_paragraph_preserve_format(paragraph, replacements):
                changed_count += 1

        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        if self._replace_in_paragraph_preserve_format(paragraph, replacements):
                            changed_count += 1

        return changed_count

    def _append_paragraphs(self, document: Any, lines: list[str]) -> None:
        for line in lines:
            document.add_paragraph(line)

    def _sanitize_filename(self, name: str) -> str:
        invalid = '<>:"/\\|?*'
        sanitized = "".join("_" if char in invalid else char for char in name).strip()
        return sanitized or "Unknown_School"

    def _is_active_school(self, item: Any, active_flag_field: str, active_flag_value: str) -> bool:
        if not isinstance(item, dict):
            return False
        return str(item.get(active_flag_field) or "").strip().lower() == active_flag_value.lower()

    def _get_school_name(self, item: dict[str, Any]) -> str:
        return str(
            item.get("School Name")
            or item.get("SCHOOL_NAME")
            or item.get("school_name")
            or item.get("Site")
            or item.get("Device Name")
            or "Unknown School"
        ).strip()

    def _get_address(self, item: dict[str, Any]) -> str:
        return str(item.get("Address") or item.get("ADDRESS") or item.get("address") or "").strip()

    def _get_location_code(self, item: dict[str, Any]) -> str:
        value = item.get("Loc Code")
        if value is None:
            value = item.get("LOCATION_CODE")
        if value is None:
            value = item.get("location_code")
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _get_contractor(self, item: dict[str, Any]) -> str:
        value = item.get("Contractor")
        if value is None:
            value = item.get("VENDOR")
        if value is None:
            value = item.get("vendor")
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _ensure_array(self, value: Any, field_name: str) -> list[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, str) and value.strip():
            path = self._resolve_path(value, require_exists=True)
            if path.suffix.lower() != ".json":
                raise ValueError(f"{field_name} path must be a .json file")
            return self._load_json_array(path)
        raise ValueError(f"{field_name} must be a JSON file path or an array")

    def _has_active_flag(self, item: Any, active_flag_field: str) -> bool:
        if not isinstance(item, dict):
            return False
        return active_flag_field in item

    def _export_pdf(self, docx_path: Path, pdf_path: Path) -> None:
        errors: list[str] = []

        try:
            from docx2pdf import convert  # type: ignore[import-not-found]

            convert(str(docx_path), str(pdf_path))
            return
        except Exception as exc:
            errors.append(f"docx2pdf failed: {exc}")

        try:
            import win32com.client  # type: ignore[import-not-found]

            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            source = word.Documents.Open(str(docx_path.resolve()))
            source.SaveAs(str(pdf_path.resolve()), FileFormat=17)
            source.Close()
            word.Quit()
            return
        except Exception as exc:
            errors.append(f"win32com failed: {exc}")

        try:
            soffice_cmd = shutil.which("soffice")
            if not soffice_cmd:
                raise FileNotFoundError("'soffice' not found on PATH")

            output_dir = pdf_path.resolve().parent
            output_dir.mkdir(parents=True, exist_ok=True)

            subprocess.run(
                [
                    soffice_cmd,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_dir),
                    str(docx_path.resolve()),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            generated_pdf = output_dir / f"{docx_path.stem}.pdf"
            if not generated_pdf.exists():
                raise RuntimeError("LibreOffice conversion completed but PDF file was not created.")

            if generated_pdf.resolve() != pdf_path.resolve():
                shutil.move(str(generated_pdf), str(pdf_path.resolve()))
            return
        except Exception as exc:
            errors.append(f"LibreOffice/soffice failed: {exc}")

        details = "\n".join(f"- {item}" for item in errors)
        raise ValueError(
            "PDF export failed. Use one of these options:\n"
            "1) Install Microsoft Word + docx2pdf/pywin32, or\n"
            "2) Install LibreOffice and ensure 'soffice' is on PATH.\n\n"
            "Attempt details:\n"
            f"{details}"
        )

    def _parse_replacements_json(self, json_path: Path) -> list[tuple[str, str]]:
        data = self._load_json_array(json_path)

        pairs: list[tuple[str, str]] = []
        for index, item in enumerate(data):
            if isinstance(item, dict):
                if "find" not in item or "replace" not in item:
                    raise ValueError(f"Invalid object at index {index}. Expected keys: 'find' and 'replace'.")
                pairs.append((str(item["find"]), str(item["replace"])))
                continue

            if isinstance(item, list) and len(item) == 2:
                pairs.append((str(item[0]), str(item[1])))
                continue

            raise ValueError(
                f"Invalid replacement at index {index}. Use {{\"find\":\"...\",\"replace\":\"...\"}} or [\"old\", \"new\"]."
            )

        return pairs

    def _parse_template_jobs(self, json_path: Path) -> list[dict[str, str]]:
        items = self._load_json_array(json_path)

        jobs: list[dict[str, str]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"Invalid template config at index {index}. Expected object.")

            input_docx = str(item.get("INPUT_DOCX") or "").strip()
            output_dir = str(item.get("OUTPUT_DIR") or "").strip()
            filename = str(item.get("filename") or "").strip()

            if not input_docx or not output_dir or not filename:
                raise ValueError(
                    f"Invalid template config at index {index}. Required keys: INPUT_DOCX, OUTPUT_DIR, filename."
                )

            jobs.append({"INPUT_DOCX": input_docx, "OUTPUT_DIR": output_dir, "filename": filename})

        if not jobs:
            raise ValueError(f"No template jobs found in: {json_path}")

        return jobs

    def _render_filename_template(self, filename_template: str, school_name: str, location_code: str) -> str:
        sanitized_school_name = self._sanitize_filename(school_name)

        stripped_template = filename_template.strip()
        if stripped_template.startswith('f"') and stripped_template.endswith('"'):
            stripped_template = stripped_template[2:-1]
        elif stripped_template.startswith("f'") and stripped_template.endswith("'"):
            stripped_template = stripped_template[2:-1]

        rendered = stripped_template.replace("{sanitize_filename(school_name)}", sanitized_school_name)

        try:
            rendered = rendered.format(
                school_name=school_name,
                school_name_sanitized=sanitized_school_name,
                location_code=location_code,
            )
        except KeyError as exc:
            raise ValueError(f"Unknown placeholder in filename template: {exc}") from exc

        rendered = rendered.strip()
        if not rendered:
            raise ValueError("Rendered filename is empty.")

        return rendered

    def _build_school_replacements(
        self,
        base_replacements: list[tuple[str, str]],
        school_name: str,
        location_code: str,
        address: str,
        contractor: str,
    ) -> list[tuple[str, str]]:
        formatted_date = datetime.now().strftime("%m/%d/%y")

        replacements = list(base_replacements)
        keys = {find for find, _ in replacements}

        if "<SCHOOL_NAME>" not in keys:
            replacements.append(("<SCHOOL_NAME>", school_name))
        if "<LOCATION_CODE>" not in keys:
            replacements.append(("<LOCATION_CODE>", location_code))
        if "<ADDRESS>" not in keys:
            replacements.append(("<ADDRESS>", address))
        if "<VENDOR>" not in keys:
            replacements.append(("<VENDOR>", contractor))
        if "<DATE>" not in keys:
            replacements.append(("<DATE>", formatted_date))

        updated: list[tuple[str, str]] = []
        for find, replace in replacements:
            if find == "<SCHOOL_NAME>":
                updated.append((find, school_name))
            elif find == "<LOCATION_CODE>":
                updated.append((find, location_code))
            elif find == "<ADDRESS>":
                updated.append((find, address))
            elif find == "<VENDOR>":
                updated.append((find, contractor))
            elif find == "<DATE>":
                updated.append((find, formatted_date))
            else:
                updated.append((find, replace))

        return updated

    def generate_documents(
        self,
        schools_json: str | dict[str, Any],
        templates_json: str | None = None,
        input_docx: str | None = None,
        output_dir: str | None = None,
        filename_template: str | None = None,
        replacements_json: str | None = None,
        append_lines: list[str] | None = None,
        export_pdf: bool = True,
        active_flag_field: str = "activate",
        active_flag_value: str = "x",
    ) -> dict[str, Any]:
        """Generate school-specific DOCX/PDF files using token replacements."""
        if isinstance(schools_json, dict):
            payload = schools_json
            resolved_schools_json = payload.get("schools_json")
            resolved_templates_json = payload.get("templates_json", templates_json)
            resolved_input_docx = payload.get("input_docx", input_docx)
            resolved_output_dir = payload.get("output_dir", output_dir)
            resolved_filename_template = payload.get("filename_template", filename_template)
            resolved_replacements_json = payload.get("replacements_json", replacements_json)
            resolved_append_lines = payload.get("append_lines", append_lines)
            resolved_export_pdf = payload.get("export_pdf", export_pdf)
            resolved_active_flag_field = payload.get("active_flag_field", active_flag_field)
            resolved_active_flag_value = payload.get("active_flag_value", active_flag_value)
        else:
            resolved_schools_json = schools_json
            resolved_templates_json = templates_json
            resolved_input_docx = input_docx
            resolved_output_dir = output_dir
            resolved_filename_template = filename_template
            resolved_replacements_json = replacements_json
            resolved_append_lines = append_lines
            resolved_export_pdf = export_pdf
            resolved_active_flag_field = active_flag_field
            resolved_active_flag_value = active_flag_value

        if isinstance(resolved_schools_json, str):
            if not resolved_schools_json.strip():
                raise ValueError("schools_json must be a non-empty string when provided as a path")
        elif not isinstance(resolved_schools_json, list):
            raise ValueError("schools_json must be a JSON file path or an array")
        if not isinstance(resolved_export_pdf, bool):
            raise ValueError("export_pdf must be a boolean")
        if not isinstance(resolved_active_flag_field, str) or not resolved_active_flag_field.strip():
            raise ValueError("active_flag_field must be a non-empty string")
        if not isinstance(resolved_active_flag_value, str) or not resolved_active_flag_value.strip():
            raise ValueError("active_flag_value must be a non-empty string")
        if resolved_append_lines is not None and not isinstance(resolved_append_lines, list):
            raise ValueError("append_lines must be an array of strings when provided")

        schools_data = self._ensure_array(resolved_schools_json, "schools_json")

        normalized_active_field = resolved_active_flag_field.strip()
        normalized_active_value = resolved_active_flag_value.strip()
        active_flag_present = any(self._has_active_flag(item, normalized_active_field) for item in schools_data)

        if active_flag_present:
            active_schools = [
                item
                for item in schools_data
                if self._is_active_school(item, normalized_active_field, normalized_active_value)
            ]
        else:
            active_schools = [item for item in schools_data if isinstance(item, dict)]

        if resolved_templates_json is not None:
            template_items = self._ensure_array(resolved_templates_json, "templates_json")
            template_jobs = self._parse_template_jobs_from_items(template_items)
        else:
            if not isinstance(resolved_input_docx, str) or not resolved_input_docx.strip():
                raise ValueError("input_docx is required when templates_json is not provided")
            if not isinstance(resolved_output_dir, str) or not resolved_output_dir.strip():
                raise ValueError("output_dir is required when templates_json is not provided")
            if not isinstance(resolved_filename_template, str) or not resolved_filename_template.strip():
                raise ValueError("filename_template is required when templates_json is not provided")

            template_jobs = [
                {
                    "INPUT_DOCX": resolved_input_docx.strip(),
                    "OUTPUT_DIR": resolved_output_dir.strip(),
                    "filename": resolved_filename_template.strip(),
                }
            ]

        base_replacements: list[tuple[str, str]] = []
        if resolved_replacements_json is not None:
            replacement_items = self._ensure_array(resolved_replacements_json, "replacements_json")
            base_replacements = self._parse_replacements_items(replacement_items)

        safe_append_lines: list[str] = []
        if isinstance(resolved_append_lines, list):
            for line in resolved_append_lines:
                if isinstance(line, str) and line.strip():
                    safe_append_lines.append(line)

        generated: list[dict[str, Any]] = []

        for job in template_jobs:
            job_input = self._resolve_path(job["INPUT_DOCX"], require_exists=True)
            job_output_dir = self._resolve_path(job["OUTPUT_DIR"], require_exists=False)
            job_filename_template = str(job["filename"])

            if job_input.suffix.lower() != ".docx":
                raise ValueError("Only .docx files are supported")

            job_output_dir.mkdir(parents=True, exist_ok=True)

            for item in active_schools:
                school = dict(item)
                school_name = self._get_school_name(school)
                location_code = self._get_location_code(school)
                address = self._get_address(school)
                contractor = self._get_contractor(school)

                filename = self._render_filename_template(job_filename_template, school_name, location_code)
                output_docx_path = job_output_dir / filename
                output_docx_path.parent.mkdir(parents=True, exist_ok=True)

                shutil.copy2(job_input, output_docx_path)

                document = self._load_document(output_docx_path)
                replacements = self._build_school_replacements(
                    base_replacements=base_replacements,
                    school_name=school_name,
                    location_code=location_code,
                    address=address,
                    contractor=contractor,
                )
                changed_count = self._replace_in_document(document, replacements)
                self._append_paragraphs(document, safe_append_lines)
                document.save(output_docx_path)

                output_pdf_path: str | None = None
                if resolved_export_pdf:
                    pdf_path = output_docx_path.with_suffix(".pdf")
                    self._export_pdf(output_docx_path, pdf_path)
                    output_pdf_path = str(pdf_path)

                generated.append(
                    {
                        "school_name": school_name,
                        "location_code": location_code,
                        "output_docx": str(output_docx_path),
                        "output_pdf": output_pdf_path,
                        "changed_blocks": changed_count,
                    }
                )

        return {
            "status": "success",
            "action": "generate_documents",
            "schools_total": len(schools_data),
            "schools_active": len(active_schools),
            "generated_count": len(generated),
            "generated": generated,
        }

    def _parse_replacements_items(self, data: list[Any]) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for index, item in enumerate(data):
            if isinstance(item, dict):
                if "find" not in item or "replace" not in item:
                    raise ValueError(f"Invalid object at index {index}. Expected keys: 'find' and 'replace'.")
                pairs.append((str(item["find"]), str(item["replace"])))
                continue

            if isinstance(item, list) and len(item) == 2:
                pairs.append((str(item[0]), str(item[1])))
                continue

            raise ValueError(
                f"Invalid replacement at index {index}. Use {{\"find\":\"...\",\"replace\":\"...\"}} or [\"old\", \"new\"]."
            )

        return pairs

    def _parse_template_jobs_from_items(self, items: list[Any]) -> list[dict[str, str]]:
        jobs: list[dict[str, str]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"Invalid template config at index {index}. Expected object.")

            input_docx = str(item.get("INPUT_DOCX") or item.get("input_docx") or "").strip()
            output_dir = str(item.get("OUTPUT_DIR") or item.get("output_dir") or "").strip()
            filename = str(item.get("filename") or item.get("FILENAME") or "").strip()

            if not input_docx or not output_dir or not filename:
                raise ValueError(
                    f"Invalid template config at index {index}. Required keys: INPUT_DOCX, OUTPUT_DIR, filename."
                )

            jobs.append({"INPUT_DOCX": input_docx, "OUTPUT_DIR": output_dir, "filename": filename})

        if not jobs:
            raise ValueError("No template jobs found")

        return jobs
