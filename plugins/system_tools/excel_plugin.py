"""Excel plugin for exporting sheet data to JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


class ExcelPlugin:
    """Read Excel sheets and save selected data as JSON."""

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

    def _apply_filters(self, frame: pd.DataFrame, filter_by: list[dict[str, Any]]) -> pd.DataFrame:
        filtered = frame
        for item in filter_by:
            if not isinstance(item, dict):
                raise ValueError("Each filter_by entry must be an object")

            column = item.get("column")
            operator = item.get("operator", "equals")
            value = item.get("value")

            if not isinstance(column, str) or not column:
                raise ValueError("filter column must be a non-empty string")
            if column not in filtered.columns:
                raise ValueError(f"filter column not found: {column}")
            if not isinstance(operator, str) or not operator:
                raise ValueError("filter operator must be a non-empty string")

            op = operator.lower()
            series = filtered[column]

            if op in {"equals", "eq", "=="}:
                filtered = filtered[series == value]
            elif op in {"not_equals", "neq", "!="}:
                filtered = filtered[series != value]
            elif op in {"contains"}:
                filtered = filtered[series.astype(str).str.contains(str(value), case=False, na=False)]
            elif op in {"in"}:
                if not isinstance(value, list):
                    raise ValueError("'in' filter value must be an array")
                filtered = filtered[series.isin(value)]
            else:
                raise ValueError(f"unsupported filter operator: {operator}")

        return filtered

    def excel_to_json(
        self,
        file_path: str,
        sheet: str | int = 0,
        columns: list[str] | None = None,
        filter_by: list[dict[str, Any]] | None = None,
        save_as: str = "output.json",
    ) -> dict[str, Any]:
        """Read an Excel sheet, optionally select/filter rows, and save to a JSON file."""
        excel_path = self._resolve_path(file_path)
        if not excel_path.exists() or not excel_path.is_file():
            raise ValueError("file_path does not exist")
        if excel_path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
            raise ValueError("file_path must be an Excel file (.xlsx/.xlsm/.xls)")

        if not isinstance(sheet, (str, int)):
            raise ValueError("sheet must be a string or integer")

        if columns is not None:
            if not isinstance(columns, list) or any(not isinstance(item, str) or not item for item in columns):
                raise ValueError("columns must be an array of non-empty strings when provided")

        if filter_by is not None and not isinstance(filter_by, list):
            raise ValueError("filter_by must be an array when provided")

        output_path = self._resolve_path(save_as)
        if output_path.suffix.lower() != ".json":
            raise ValueError("save_as must be a .json file")

        try:
            frame = pd.read_excel(excel_path, sheet_name=sheet)
        except Exception as exc:
            raise ValueError(f"Failed to read Excel file: {exc}") from exc

        if columns:
            missing_columns = [column for column in columns if column not in frame.columns]
            if missing_columns:
                raise ValueError(f"columns not found in sheet: {', '.join(missing_columns)}")
            frame = frame[columns]

        if filter_by:
            frame = self._apply_filters(frame, filter_by)

        records = frame.where(pd.notna(frame), None).to_dict(orient="records")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            output_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            raise ValueError(f"Failed to write JSON output: {exc}") from exc

        return {
            "status": "success",
            "action": "excel_to_json",
            "file_path": str(excel_path),
            "sheet": sheet,
            "selected_columns": columns if columns is not None else "all",
            "filters_applied": len(filter_by) if isinstance(filter_by, list) else 0,
            "row_count": len(records),
            "save_as": str(output_path),
        }
