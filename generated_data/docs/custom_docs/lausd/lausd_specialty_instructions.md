# LAUSD Specialty Instructions

This document is the LAUSD-specific specialty instructions entrypoint.

## Reference Documents

Use these as the primary references for LAUSD network/site lookups and Excel processing behavior:

- [Network Device and Site Data Reference](../../network_device_site_data_reference.md)
- [Excel Plugin Research](../../plugin_research/excel_plugin.md)

## Suggested Usage

1. Use the network device/site reference for `Site`, `Loc Code`, `School Name`, `Address`, `City`, and device fields.
2. Use the Excel plugin reference to determine method capabilities and expected inputs/outputs when exporting or inspecting workbook data.
3. Keep requests aligned with current allowlisted plugin methods in `config.py`.

## Notes

- Prefer consistent site-name formatting when searching/filtering.
- Validate method names against current implementation when preparing JSON requests.

## Step-by-step instructions for data retrieval and updates for temp.xlsm file

1. Start by listing all sheet names in the workbook located at `C:/Users/Paul/Documents/Projects/paramiko/temp.xlsm`.
2. For the target sheet (e.g., `r1`), list all columns to understand the available data structure.
3. Use partial string filtering on relevant columns to find target rows. For instance, to search for "Woodland Hills Academy", filtering the "School Name" column with the partial string "woodland" is recommended for broader matching.
4. Each retrieved record includes a "row" value that corresponds to the row number in the worksheet. This row number is critical for update operations.
5. To update records, follow the examples and guidance in the [Excel Plugin Research](../../plugin_research/excel_plugin.md) documentation, particularly focusing on the use of the `update_sheet_row_values` method which utilizes the sheet name, row number, column names, and new values.

## Generic update workflow (template)

### 1) List columns available in a sheet

Use `list_columns_in_sheet` first so update requests always use valid column names.

### 2) Find target row using partial-string search

... (existing text omitted for brevity) ...

### 3) Update one or more columns on the discovered row

```json
{
	"module": "plugins.system_tools.excel_plugin",
	"class": "ExcelPlugin",
	"method": "update_sheet_row_values",
	"constructor_args": {
		"base_dir": "generated_data",
		"allow_outside_base_dir": true
	},
	"args": [
		{
			"file_path": "<absolute-or-base_dir-relative-excel-path>",
			"header_row": 1,
			"save_as": "<output-excel-path>",
			"updates": [
				{
					"sheet": "<sheet-name>",
					"row": <row-from-excel_to_json-result>,
					"columns": ["<column-name-1>", "<column-name-2>"],
					"values": ["<new-value-1>", "<new-value-2>"]
				}
			]
		}
	]
}
```

Example pattern: if a filtered result shows `Site` containing `woodland` on row `846` in sheet `r1`, update any allowed target column(s) using that exact sheet/row pair.
