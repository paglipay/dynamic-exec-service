# AI Agent Reference Index

Purpose: quick navigation and decision support for AI agents working with documentation in `generated_data/docs`.

## How to Use This Index

- Start with **API Reference** to understand request/response contracts.
- Use **Usage Tips** for request-shape and operational best practices.
- Use **Integration Guides** when handling Slack files/images and markdown references.
- Use **Plugin Research** for module/class/method-level capability details.

## Suggested Read Order

1. [API Usage Tips](./usage_tips/api_usage_tips.md)
2. [Execute Endpoint](./api_reference/execute_endpoint.md)
3. [Workflow Endpoint](./api_reference/workflow_endpoint.md)
4. [Capability Gap Process](./usage_tips/capability_gap_process.md)
5. Integration and plugin-specific docs as needed.

## API Reference

- [Execute Endpoint](./api_reference/execute_endpoint.md)  
  Single allowlisted plugin call via `/execute`; includes required fields (`module`, `class`, `method`), optional fields, response format, and example payload.

- [Workflow Endpoint](./api_reference/workflow_endpoint.md)  
  Multi-step execution via `/workflow`; includes step references (`${steps.<id>.result}`), partial error controls, and response structure.

- [Slack Events Endpoint](./api_reference/slack_events_endpoint.md)  
  Slack event handling behavior, duplicate suppression, text/file processing, image detection, and required environment variables.

## Integration Guides

- [Slack Image Detection and OpenAI Analysis Handoff](./integration_guides/slack_image_detection_and_openai_handoff.md)  
  End-to-end Slack image flow: event parsing, image candidacy checks, secure download, local persistence, base64 data URL conversion, and OpenAI multimodal handoff.

- [Slack Image Upload and Markdown Reference Guide](./integration_guides/slack_image_upload_and_markdown_references.md)  
  Canonical path conventions for saved Slack images and preferred markdown linking style (`slack_downloads/images/...`) for files under `generated_data`.

## Plugin Research

- [Excel Plugin](./plugin_research/excel_plugin.md)  
  `plugins.system_tools.excel_plugin.ExcelPlugin`; highlights `excel_to_json` and `list_sheets_metadata` capabilities and output schema focus.

- [Generated Math Plugin](./plugin_research/generated_math_plugin.md)  
  `plugins.generated_math_plugin.GeneratedMathPlugin`; simple `multiply` and `greet` methods used in examples and quick validation.

- [SSH Module](./plugin_research/ssh_module.md)  
  `plugins.ssh_module.SSHModule`; remote command execution and directory listing usage summary.

- [Text File CRUD Plugin](./plugin_research/text_file_crud_plugin.md)  
  `plugins.text_file_crud_plugin.TextFileCRUDPlugin`; create/read/update/delete/list behavior with extension and path-safety constraints.

## Usage Tips

- [API Usage Tips](./usage_tips/api_usage_tips.md)  
  Practical guidance on endpoint selection, allowlist discipline, response handling, env var usage, and common request templates.

- [Capability Gap Process](./usage_tips/capability_gap_process.md)  
  Escalation workflow when requested functionality is missing or partial; includes gap classification, risk checks, rollout plan, and handoff templates.

- [Request JSON Style Guide: Object-Style args](./usage_tips/request_args_object_style.md)  
  Preferred request-authoring pattern: keep `args` as an array containing one named object (`args[0]`) for readability and maintainability.

## Agent Routing Heuristics

- If task is **single plugin call** → read Execute docs first.
- If task is **multi-step/chained data flow** → read Workflow docs first.
- If task involves **Slack files or images** → read both Slack integration guides.
- If task asks for **plugin details/method names** → read Plugin Research.
- If task asks for **new capability not present** → follow Capability Gap Process.

## Contract and Safety Reminders

- Respect allowlisted module/class/method constraints from `config.py`.
- Preserve stable error shape: `{ "status": "error", "message": "..." }`.
- Keep `constructor_args` object-shaped and `args` array-shaped (prefer one named object in `args[0]` for new JSON).
- Keep values JSON-serializable.
