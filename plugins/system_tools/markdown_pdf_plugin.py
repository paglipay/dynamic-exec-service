"""Markdown to PDF conversion plugin."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image as RLImage
from reportlab.platypus import ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle


class MarkdownPDFPlugin:
    """Convert markdown files to simple PDF output."""

    _image_line_pattern = re.compile(r"^\s*!\[[^\]]*\]\(([^\)]+)\)\s*$")

    def _is_markdown_table_separator(self, line: str) -> bool:
        stripped = line.strip()
        if "|" not in stripped:
            return False

        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            return False

        for cell in cells:
            if not cell or not re.fullmatch(r":?-{3,}:?", cell):
                return False
        return True

    def _parse_table_row(self, line: str) -> list[str]:
        stripped = line.strip()
        if not stripped:
            return []

        parts = [part.strip() for part in stripped.strip("|").split("|")]
        return parts

    def _build_table_flowable(self, rows: list[list[str]], content_width: float, body_style: ParagraphStyle) -> Table:
        col_count = max((len(row) for row in rows), default=0)
        if col_count <= 0:
            raise ValueError("table has no columns")

        normalized_rows: list[list[Any]] = []
        for row in rows:
            padded = [*row, *([""] * (col_count - len(row)))]
            normalized_rows.append([Paragraph(self._inline_markdown_to_rml(cell), body_style) for cell in padded])

        col_width = content_width / col_count
        table = Table(normalized_rows, colWidths=[col_width] * col_count, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
                ]
            )
        )
        return table

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

    def _inline_markdown_to_rml(self, text: str) -> str:
        escaped = html.escape(text)

        code_tokens: dict[str, str] = {}

        def _extract_code(match: re.Match[str]) -> str:
            token = f"__CODE_TOKEN_{len(code_tokens)}__"
            code_tokens[token] = f"<font name='Courier'>{match.group(1)}</font>"
            return token

        escaped = re.sub(r"`([^`]+)`", _extract_code, escaped)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"__(.+?)__", r"<b>\1</b>", escaped)
        escaped = re.sub(r"\*(.+?)\*", r"<i>\1</i>", escaped)
        escaped = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", escaped)

        for token, replacement in code_tokens.items():
            escaped = escaped.replace(token, replacement)

        return escaped

    def _build_styles(self) -> dict[str, ParagraphStyle]:
        base_styles = getSampleStyleSheet()

        body_style = ParagraphStyle(
            "Body",
            parent=base_styles["BodyText"],
            fontName="Helvetica",
            fontSize=11,
            leading=15,
            spaceAfter=8,
        )
        code_style = ParagraphStyle(
            "Code",
            parent=base_styles["Code"],
            fontName="Courier",
            fontSize=9.5,
            leading=12,
            leftIndent=8,
            rightIndent=8,
            backColor=colors.whitesmoke,
            borderColor=colors.lightgrey,
            borderPadding=6,
            borderWidth=0.5,
            borderRadius=2,
            spaceAfter=10,
        )

        heading_styles: dict[int, ParagraphStyle] = {
            1: ParagraphStyle("H1", parent=base_styles["Heading1"], fontName="Helvetica-Bold", fontSize=22, leading=26, spaceBefore=10, spaceAfter=10),
            2: ParagraphStyle("H2", parent=base_styles["Heading2"], fontName="Helvetica-Bold", fontSize=18, leading=22, spaceBefore=8, spaceAfter=8),
            3: ParagraphStyle("H3", parent=base_styles["Heading3"], fontName="Helvetica-Bold", fontSize=15, leading=18, spaceBefore=6, spaceAfter=6),
            4: ParagraphStyle("H4", parent=base_styles["Heading4"], fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=5, spaceAfter=5),
            5: ParagraphStyle("H5", parent=base_styles["Heading5"], fontName="Helvetica-Bold", fontSize=12, leading=15, spaceBefore=4, spaceAfter=4),
            6: ParagraphStyle("H6", parent=base_styles["Heading6"], fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=4, spaceAfter=4),
        }

        return {
            "body": body_style,
            "code": code_style,
            "title": ParagraphStyle(
                "DocTitle",
                parent=base_styles["Title"],
                fontName="Helvetica-Bold",
                fontSize=24,
                leading=28,
                alignment=0,
                spaceAfter=14,
            ),
            **{f"h{level}": style for level, style in heading_styles.items()},
        }

    def _resolve_markdown_image_path(self, image_ref: str, markdown_path: Path) -> Path | None:
        raw_ref = image_ref.strip().strip("<>").strip()
        if not raw_ref:
            return None

        lowered = raw_ref.lower()
        if lowered.startswith(("http://", "https://", "data:")):
            return None

        candidate = Path(raw_ref)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            return resolved if resolved.exists() and resolved.is_file() else None

        relative_to_md = (markdown_path.parent / candidate).resolve()
        if relative_to_md.exists() and relative_to_md.is_file():
            return relative_to_md

        relative_to_base = (self.base_dir / candidate).resolve()
        if relative_to_base.exists() and relative_to_base.is_file():
            return relative_to_base

        return None

    def _build_image_flowable(self, image_path: Path, content_width: float, max_height: float) -> RLImage:
        reader = ImageReader(str(image_path))
        img_width, img_height = reader.getSize()
        width = float(img_width)
        height = float(img_height)
        if width <= 0 or height <= 0:
            raise ValueError("image dimensions are invalid")

        width_scale = content_width / width
        height_scale = max_height / height
        scale = min(width_scale, height_scale, 1.0)

        return RLImage(str(image_path), width=width * scale, height=height * scale)

    def _parse_markdown_to_flowables(
        self,
        markdown_text: str,
        title: str | None,
        markdown_path: Path,
        content_width: float,
        content_max_height: float,
    ) -> list[Any]:
        styles = self._build_styles()
        flowables: list[Any] = []

        if isinstance(title, str) and title.strip():
            flowables.append(Paragraph(self._inline_markdown_to_rml(title.strip()), styles["title"]))
            flowables.append(Spacer(1, 0.08 * inch))

        lines = markdown_text.splitlines()
        index = 0
        line_count = len(lines)

        while index < line_count:
            line = lines[index]

            if not line.strip():
                flowables.append(Spacer(1, 0.08 * inch))
                index += 1
                continue

            if line.strip().startswith("```"):
                index += 1
                code_lines: list[str] = []
                while index < line_count and not lines[index].strip().startswith("```"):
                    code_lines.append(lines[index].rstrip("\n"))
                    index += 1
                if index < line_count and lines[index].strip().startswith("```"):
                    index += 1
                code_text = "\n".join(code_lines) if code_lines else ""
                flowables.append(Preformatted(code_text, styles["code"]))
                continue

            image_match = self._image_line_pattern.match(line)
            if image_match:
                image_ref = image_match.group(1).strip()
                image_path = self._resolve_markdown_image_path(image_ref, markdown_path)
                if isinstance(image_path, Path):
                    try:
                        flowables.append(self._build_image_flowable(image_path, content_width, content_max_height))
                        flowables.append(Spacer(1, 0.12 * inch))
                    except Exception:
                        flowables.append(Paragraph(self._inline_markdown_to_rml(f"[Image could not be rendered: {image_ref}]"), styles["body"]))
                else:
                    flowables.append(Paragraph(self._inline_markdown_to_rml(f"[Image not found: {image_ref}]"), styles["body"]))
                index += 1
                continue

            heading_match = re.match(r"^\s{0,3}(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip()
                flowables.append(Paragraph(self._inline_markdown_to_rml(heading_text), styles[f"h{level}"]))
                index += 1
                continue

            if "|" in line and (index + 1) < line_count and self._is_markdown_table_separator(lines[index + 1]):
                header_row = self._parse_table_row(line)
                index += 2
                table_rows: list[list[str]] = [header_row]

                while index < line_count:
                    row_line = lines[index]
                    if not row_line.strip() or "|" not in row_line:
                        break
                    if self._is_markdown_table_separator(row_line):
                        index += 1
                        continue
                    row = self._parse_table_row(row_line)
                    if not row:
                        break
                    table_rows.append(row)
                    index += 1

                if len(table_rows) >= 1:
                    try:
                        flowables.append(self._build_table_flowable(table_rows, content_width, styles["body"]))
                        flowables.append(Spacer(1, 0.1 * inch))
                    except Exception:
                        fallback_text = "\n".join(" | ".join(row) for row in table_rows)
                        flowables.append(Paragraph(self._inline_markdown_to_rml(fallback_text), styles["body"]))
                continue

            unordered_match = re.match(r"^\s*[-*+]\s+(.+)$", line)
            if unordered_match:
                items: list[ListItem] = []
                while index < line_count:
                    item_match = re.match(r"^\s*[-*+]\s+(.+)$", lines[index])
                    if not item_match:
                        break
                    item_text = item_match.group(1).strip()
                    item_paragraph = Paragraph(self._inline_markdown_to_rml(item_text), styles["body"])
                    items.append(ListItem(item_paragraph))
                    index += 1
                flowables.append(ListFlowable(items, bulletType="bullet", leftIndent=18, bulletFontName="Helvetica", bulletFontSize=10))
                flowables.append(Spacer(1, 0.05 * inch))
                continue

            ordered_match = re.match(r"^\s*\d+\.\s+(.+)$", line)
            if ordered_match:
                items = []
                while index < line_count:
                    item_match = re.match(r"^\s*\d+\.\s+(.+)$", lines[index])
                    if not item_match:
                        break
                    item_text = item_match.group(1).strip()
                    item_paragraph = Paragraph(self._inline_markdown_to_rml(item_text), styles["body"])
                    items.append(ListItem(item_paragraph))
                    index += 1
                flowables.append(ListFlowable(items, bulletType="1", leftIndent=18, bulletFontName="Helvetica", bulletFontSize=10))
                flowables.append(Spacer(1, 0.05 * inch))
                continue

            paragraph_parts: list[str] = [line.strip()]
            index += 1
            while index < line_count:
                next_line = lines[index]
                if not next_line.strip():
                    break
                if re.match(r"^\s{0,3}(#{1,6})\s+", next_line):
                    break
                if "|" in next_line and (index + 1) < line_count and self._is_markdown_table_separator(lines[index + 1]):
                    break
                if re.match(r"^\s*[-*+]\s+", next_line):
                    break
                if re.match(r"^\s*\d+\.\s+", next_line):
                    break
                if next_line.strip().startswith("```"):
                    break
                paragraph_parts.append(next_line.strip())
                index += 1

            paragraph_text = " ".join(paragraph_parts)
            flowables.append(Paragraph(self._inline_markdown_to_rml(paragraph_text), styles["body"]))

        return flowables

    def markdown_to_pdf(
        self,
        file_path: str,
        output_path: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Convert a markdown file to a PDF file."""
        markdown_path = self._resolve_path(file_path)
        if not markdown_path.exists() or not markdown_path.is_file():
            raise ValueError("file_path does not exist")
        if markdown_path.suffix.lower() != ".md":
            raise ValueError("file_path must be a .md file")

        resolved_output = output_path.strip() if isinstance(output_path, str) and output_path.strip() else f"{markdown_path.stem}.pdf"
        pdf_path = self._resolve_path(resolved_output)
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError("output_path must be a .pdf file")

        try:
            markdown_text = markdown_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Failed to read markdown file: {exc}") from exc

        pdf_path.parent.mkdir(parents=True, exist_ok=True)

        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=letter,
            leftMargin=0.75 * inch,
            rightMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
            title=title.strip() if isinstance(title, str) and title.strip() else markdown_path.stem,
        )

        flowables = self._parse_markdown_to_flowables(
            markdown_text,
            title,
            markdown_path,
            content_width=float(doc.width),
            content_max_height=float(doc.height * 0.65),
        )

        try:
            doc.build(flowables)
        except Exception as exc:
            raise ValueError(f"Failed to write PDF file: {exc}") from exc

        return {
            "status": "success",
            "action": "markdown_to_pdf",
            "file_path": str(markdown_path),
            "output_path": str(pdf_path),
            "line_count": len(markdown_text.splitlines()),
        }
