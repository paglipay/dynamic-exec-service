# API Usage Tips

- Always use allowlisted module/class/method combinations.
- For single actions, use the `/execute` endpoint.
- For multi-step workflows, use the `/workflow` endpoint with references.
- Always check the `status` field in responses and handle errors appropriately.
- Use JSON-serializable arguments and constructor args.
- Use environment variables for integrations (e.g., Slack `SIGNING_SECRET` and `SLACK_BOT_TOKEN`).
- Use the `TextFileCRUDPlugin` to read file contents before passing to plugins that cannot read files directly.
- For Slack integration, prime memory with README context via workflows and environment variables.
- For Excel workflows, `ExcelPlugin.excel_to_json` returns `column_names`, and `ExcelPlugin.list_columns_in_sheet` provides per-sheet schema discovery before extraction.
- Use request examples in `jsons/system_tools/excel/excel_to_json_request.json` and `jsons/system_tools/excel/excel_list_sheets_metadata_request.json` as working payload templates.
