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

## Step-by-step instructions for data retrieval

1. Identify the Excel source file containing the relevant data (e.g., `temp.xlsm`).
2. Use the Excel plugin's `list_sheets_metadata` method to list the available sheets and confirm the target sheet name.
3. Use the Excel plugin's `excel_to_json` method to list columns in the target sheet by not specifying any column filter.
4. Choose necessary columns for your task to reduce data load.
5. Apply filtering using the most effective minimum partial string on the filter column to obtain a reasonable result set.
6. Extract the required fields from the filtered row(s) for further use.

## Generic filter guidance for AI agents

- When filtering, use partial string matches rather than full strings for better coverage.
- Always verify the column names and sheet structure dynamically before querying.
- Select only the columns of interest to optimize performance and output size.
- Confirm that the applied filters produce the expected filtered row count to ensure data accuracy.
- Handle cases where no rows or multiple rows match the filter criteria appropriately.