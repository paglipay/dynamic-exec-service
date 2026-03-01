---
Request ID: REQ-20260228-EXCEL-001
Gap Type: Type B
Current Limitation: ExcelPlugin can update existing workbooks, but does not provide a dedicated method to create a new workbook from scratch.
Workaround Available: partial - an existing workbook can be copied/templated and updated using update methods; true first-time workbook creation still requires external tooling.
Proposed Update: Add new workbook-creation and optional sheet-creation methods to ExcelPlugin while preserving existing update/read behavior.
Risk Level: medium
Dependencies: openpyxl and pandas are already present; no new library is required for baseline create/update methods.
ETA: TBD
Owner: dev-team
Verification Plan:
  - Unit tests for workbook creation, sheet creation, and validation failures.
  - Integration tests through /execute with allowlist validation.
  - Sample JSON payloads under jsons/ for API contract verification.

## Current State (Accurate Baseline)

### Already Supported by ExcelPlugin

- Read/inspect:
  - `excel_to_json`
  - `list_columns_in_sheet`
  - `list_sheet_names`
- Update existing workbook content:
  - `append_mapped_output_change` (builds change objects for workflows)
  - `update_sheet_row_values` (writes values to existing workbook/sheet cells)

### Not Yet Supported (Primary Gap)

- Create a new Excel workbook file from scratch via a dedicated allowlisted method.
- Optionally create/add worksheet(s) when a requested sheet does not exist.

## Background

The current plugin supports reading Excel data and updating existing files. The missing capability is explicit first-time workbook creation for flows that start without a template file.

## Proposed Capability Addition

- Implement new methods in ExcelPlugin:
  - `create_workbook(file_path: str, sheet_name: str = "Sheet1", headers: list[str] | None = None, rows: list[list[Any]] | None = None, overwrite: bool = False) -> dict[str, Any]`
  - `add_sheet(file_path: str, sheet_name: str, headers: list[str] | None = None, overwrite_if_exists: bool = False) -> dict[str, Any]` (optional but recommended)

### Why these methods

- `create_workbook` closes the true capability gap.
- `add_sheet` avoids overloading `update_sheet_row_values` with implicit sheet creation logic and keeps behavior explicit/safe.

## Non-Goals

- Do not replace existing methods.
- Do not add unrestricted file operations outside the current path resolution safeguards.
- Do not change API error response shape.

## Security and Compliance

- Input validation to prevent injection or invalid file paths.
- Keep path handling within existing `_resolve_path` behavior.
- Validate extension allowlist (`.xlsx`, `.xlsm`, `.xls`) consistently.
- Enforce explicit overwrite flags to avoid accidental data loss.
- Permission enforcement remains allowlist-driven via `config.py` and `executor/permissions.py`.

## Allowlist and Permission Impact

- `executor/permissions.py` likely requires no code changes (generic allowlist checks already in place).
- `config.py` must be updated to include any newly added method names under `plugins.system_tools.excel_plugin`.

## Development Steps

1. Implement `create_workbook` in ExcelPlugin with strict argument checks.
2. Optionally implement `add_sheet` for explicit sheet lifecycle control.
3. Update allowlist entries in `config.py` for new methods.
4. Add JSON request examples under `jsons/` (single and workflow-ready variants).
5. Add/refresh documentation under `generated_data/docs/usage_tips` and/or integration docs.
6. Execute targeted tests (unit + /execute integration) for success and failure cases.

## Suggested Request Examples to Add

- `jsons/excel_create_workbook_request.json`
- `jsons/excel_add_sheet_request.json` (if method added)

## Definition of Done

- New method(s) implemented and allowlisted.
- New workbook can be created with expected sheet/header/row behavior.
- Validation errors are user-safe and deterministic.
- Example JSON payloads execute successfully via `/execute`.
- Documentation reflects both current capabilities and new methods.
