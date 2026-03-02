# Network Device and Site Data Reference

This Excel file contains comprehensive data about network devices and associated sites such as schools or facilities. It can be used as a reference point for locating site information, device details, and related metadata.

## Key Data Available

- **Device Information:** IP addresses (`switch_ip_address`), device names (`Device Name`), and device types (`Type`).
- **Site Information:** Site names (`Site`), location codes (`Loc Code`), school names (`School Name`), addresses (`Address`), and city details (`City`).
- **Vendor and Warranty:** Vendor information (`Vendor`), warranty status (`Warranty`), and contractors (`Contractor`).
- **Network Commands:** Various fields containing network commands and outputs (e.g., `show_command`, `show_dell_command`, and their outputs).
- **Additional Metadata:** Status, priority, activation flags, notes, VLAN info, camera details, and URLs.

## Usage

When needing information about network devices, their locations, or site metadata, this Excel file is the centralized source.

To find specific info such as the location code for a site, one can:

1. Identify the relevant sheet, often `r1`.
2. Locate the site by name.
3. Extract the matching details such as `Loc Code` and address.

## Important Columns

- `Site`
- `Loc Code`
- `School Name`
- `Address`
- `City`
- `Vendor`
- `Device Name`
- `switch_ip_address`

## Notes

- Sheet names may vary but generally contain related data.
- Standardize search inputs for consistent querying.
- This file supports tracking and managing network devices by location.


## Example Source File

The source of this type of network device and site data can be the Excel file located at `C:\Users\Paul\Documents\Projects\temp.xlsx`. This file contains detailed columns such as "School Name", "Address", "City", "Loc Code", and device related data which can be queried for location and device info.
