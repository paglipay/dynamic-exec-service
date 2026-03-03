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

## Step-by-step instructions for data retrieval and updates

1. Identify the Excel source file containing the relevant data (for example, `temp.xlsm`).
2. Use `list_sheet_names` to confirm the target worksheet.
3. Use `list_columns_in_sheet` for that worksheet to see available column names.
4. Use `excel_to_json` with a minimal `columns` list and a `filter_by` partial string (`operator: "contains"`) to find the target record.
5. Read the returned `row` value from the filtered result and treat it as the worksheet row for updates.
6. Call `update_sheet_row_values` with the discovered `sheet`, `row`, target `columns`, and `values`.

## Generic update workflow (template)

### 1) List columns available in a sheet

Use `list_columns_in_sheet` first so update requests always use valid column names.

### 2) Find target row using partial-string search

Use `excel_to_json` with:
- `columns`: only what you need (for example, `Site`, `Loc Code`, `Network Engineer`)
- `filter_by`: a partial string on a stable lookup field (for example, `Site contains "woodland"`)

The result includes `row` for each matching record. Use that row number in the update request.

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
