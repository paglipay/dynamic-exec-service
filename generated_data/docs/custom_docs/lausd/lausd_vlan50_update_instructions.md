# Step-by-step Instructions for VLAN 50 Update on LAUSD Excel Sheet

This document describes the steps taken to update the VLAN 50 range for Henry David Thoreau Continuation High (Loc Code 8883) in the 'r1' sheet of the provided Excel workbook.

## Email Reference

The VLAN 50 range information was obtained from the following email exchange:

- Request made by Paul Aglipay for VLAN 50 range for Henry David Thoreau Continuation High (8883).
- Response from Phil Lei providing the IP range: 172.25.244.192/26.

## Instructions for Updating the Excel Workbook

1. Identify the target file path and sheet name:
   - File path: `C:/Users/Paul/Documents/Projects/paramiko/temp.xlsm`
   - Sheet name: `r1`

2. Locate the row corresponding to the target site using the "Loc Code" column:
   - Filter the "Loc Code" column for the value `8883`.
   - Note the row number where this match occurs (row `751` in this case).

3. Update the "vlan50" column in the identified row:
   - Set the value of the "vlan50" column to `172.25.244.192/26`.

4. Save the changes to the original file to maintain data integrity.

---

These steps ensure the VLAN 50 IP range is accurately reflected in the LAUSD network infrastructure data.
