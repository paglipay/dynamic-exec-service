# Excel Lookup for `sh_lldp_entry_out`

This document explains how to query an Excel file for a MAC address and export matching data (including `sh_lldp_entry_out`) to JSON.

## Recommended Parameter Style (Industry-Standard)

Use explicit defaults and optional fields instead of `"*"` placeholders.

- **Required value**: `<required_value>`
- **Optional value with default**: omit it or pass `null` where supported

## `excel_to_json` Argument Contract

Method signature:

`excel_to_json(file_path, sheet=0, columns=None, filter_by=None, save_as="output.json")`

Meaning:
- `file_path` (**required**): path to `.xlsx/.xls/.xlsm`
- `sheet` (**optional**, default `0`): sheet name or sheet index
- `columns` (**optional**, default `all columns`): array of column names, or `null`
- `filter_by` (**optional**, default `no filters`): array of filter objects, or `[]`
- `save_as` (**optional**, default `output.json`): output filename/path ending in `.json`

## Mapping from `*` Preference to Clear Defaults

If your shorthand intent is:
- `sheet: "*"` → use default `0` (or provide explicit sheet name)
- `columns: ["*"]` → use `null` (all columns)
- `filter_by: []` → keep as `[]` (no filter)
- `save_as: "*.json"` → use a concrete name, e.g. `lookup_sh_lldp_entry_out.json`

## Practical Request (Default-Friendly)

```json
{
  "module": "plugins.system_tools.excel_plugin",
  "class": "ExcelPlugin",
  "method": "excel_to_json",
  "constructor_args": {
    "base_dir": "generated_data",
    "allow_outside_base_dir": true
  },
  "args": [
    "C:/Users/Paul/Documents/Projects/paramiko/temp.xlsx",
    0,
    null,
    [],
    "lookup_sh_lldp_entry_out.json"
  ]
}
```

## MAC Lookup Example (Targeted)

```json
{
  "module": "plugins.system_tools.excel_plugin",
  "class": "ExcelPlugin",
  "method": "excel_to_json",
  "constructor_args": {
    "base_dir": "generated_data",
    "allow_outside_base_dir": true
  },
  "args": [
    "C:/Users/Paul/Documents/Projects/paramiko/temp.xlsx",
    "ad-venice-s1",
    ["mac", "sh_lldp_entry_out"],
    [
      {
        "column": "mac",
        "operator": "equals",
        "value": "b8a4.4fb1.e3cf"
      }
    ],
    "lookup_sh_lldp_entry_out.json"
  ]
}
```

## Notes

- Prefer explicit output filenames over wildcard-like patterns.
- `sh_lldp_entry_out` is usually multiline text; parse downstream for IP/device details.
- If multiple rows match, iterate all JSON records instead of assuming one row.
