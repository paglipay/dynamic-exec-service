# Excel Plugin

## Module
`plugins.system_tools.excel_plugin`

## Class
`ExcelPlugin`

## Allowed Methods
- `excel_to_json`
- `list_sheets_metadata`

## Features
- Reads `.xlsx`, `.xlsm`, and `.xls` workbooks
- Exports selected/filtered sheet rows to `.json`
- Returns `column_names` in `excel_to_json` results
- Lists workbook sheet metadata via `list_sheets_metadata`

## `excel_to_json` summary
- Inputs include `file_path`, `sheet`, optional `columns`, optional `filter_by`, and `save_as`
- Output includes `row_count`, `column_names`, and saved JSON path

## `list_sheets_metadata` summary
- Input: `file_path`
- Output includes:
  - `sheet_count`
  - `sheets[]` entries with `sheet_index`, `sheet_name`, `row_count`, `column_count`, `column_names`, `first_row_column_names`, `first_data_row`

## Usage Notes
- Use a relative file path under `base_dir` (default `generated_data`) for easiest interoperability with `TextFileCRUDPlugin`
- Validate target method against `config.ALLOWED_MODULES` before making calls


### Example File Source

The Excel file `C:\Users\Paul\Documents\Projects\temp.xlsx` is an example source workbook for network and site data that the Excel plugin can process. It contains multiple sheets with relevant columns for filtering and exporting to JSON.
