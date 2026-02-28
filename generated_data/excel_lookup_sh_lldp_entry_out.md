# Excel Lookup for sh_lldp_entry_out

This document explains how to perform a lookup in an Excel file for a specific MAC address and retrieve the corresponding `sh_lldp_entry_out` output.

## Purpose

The task is to find a row in an Excel sheet where the `mac` address matches a given value and extract the associated `sh_lldp_entry_out` information, which may contain IP address and device details.

## Steps

1. Use the `excel_to_json` method from the Excel plugin to query the Excel file.
2. Provide the file path and sheet name.
3. Select the columns to return, typically `mac` and `sh_lldp_entry_out`.
4. Apply a filter on the `mac` column to match the target MAC address.
5. The output will be a JSON representation of the matching row(s).
6. Parse the JSON to extract the `sh_lldp_entry_out` details.
7. From the details, read the camera IP address or any other relevant information needed.

## Example

```json
{
  "module": "plugins.system_tools.excel_plugin",
  "class": "ExcelPlugin",
  "method": "excel_to_json",
  "constructor_args": {
    "base_dir": ".",
    "allow_outside_base_dir": true
  },
  "args": [
    "C:/path/to/file.xlsx",
    "sheet_name",
    ["mac", "sh_lldp_entry_out"],
    [
      {
        "column": "mac",
        "operator": "equals",
        "value": "target_mac_address"
      }
    ],
    "output.json"
  ]
}
```

Replace `target_mac_address` with the actual MAC.

## Notes

- Ensure the path and sheet name match your file.
- The `sh_lldp_entry_out` column contains multiline string data which may need parsing for IP extraction.
- This method assumes there's only one matching row; if multiple, iterate accordingly.
