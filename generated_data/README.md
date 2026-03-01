# Dynamic Exec Service — AI Usage Notes

This README is intended to help an AI agent use the current service safely and correctly.

## What this application does
- Exposes a Flask API for controlled dynamic plugin execution.
- Enforces module/class/method allowlisting from `config.ALLOWED_MODULES`.
- Returns standardized JSON responses for success and errors.
- Supports Slack Events at `/slack/events` when `SIGNING_SECRET` is available (from environment or `.env`).

## Agent quick-start (recommended)
1. Use `POST /execute` for a single action.
2. Use `POST /workflow` for multi-step tasks with `${steps.<id>.result...}` references.
3. Check `status` in every response and handle `error` messages explicitly.
4. Only call allowlisted module/class/method combinations listed below.

## Priority: Slack image reference convention
- Slack image attachments are saved under:
  - `target_dir = (base_dir / "slack_downloads" / "images" / channel_segment / timestamp).resolve()`
- With default `base_dir=generated_data`, images land in paths like:
  - `generated_data/slack_downloads/images/C01FMQVG5RU/2026/02/28/...`
- For markdown/image links in files created under `generated_data`, prioritize **base_dir-relative** references:
  - `slack_downloads/images/...`
- Alternative supported form:
  - `generated_data/slack_downloads/images/...`
- This convention is documented in:
  - `generated_data/docs/integration_guides/slack_image_upload_and_markdown_references.md`

## API routes in app.py

### POST /execute
Executes one allowlisted plugin method call.

Required fields:
- `module` (string)
- `class` (string)
- `method` (string)

Optional fields:
- `constructor_args` (object, default `{}`)
- `args` (array, default `[]`)

Success response:
```json
{"status":"success","result": ...}
```

Error response:
```json
{"status":"error","message":"..."}
```

### POST /workflow
Executes a sequence of allowlisted steps.

Workflow features:
- `steps`: non-empty array of step objects.
- `stop_on_error`: boolean (default `true`).
- Optional per-step `id` (auto-indexed if omitted).
- Optional per-step `on_error`: `stop` or `continue`.
- Supports references in step inputs:
  - `${steps.<id>.result}`
  - `${steps.<id>.result.<path>}`

Typical success response:
```json
{"status":"success","has_errors":false,"results":[...]}
```

### POST /slack/events
Handled by `SlackEventAdapter` when `SIGNING_SECRET` is set.

- If `SIGNING_SECRET` is missing, Slack event subscriptions are disabled and the app logs a warning.
- Current handler listens for `message` events, accepts normal messages plus `file_share`, ignores duplicate deliveries, and replies via function-calling using `plugins.integrations.openai_plugin`.
- For file attachments, it extracts metadata from `event["files"]` and attempts to download text-like files (`.txt`, `.md`, `text/*`) using `SLACK_BOT_TOKEN` so content can be included in the AI prompt.

## Allowlisted plugins (current)
Only these module/class/method combinations are executable via API:

- `plugins.sample_module` → `SampleModule`
  - methods: `add`, `process`

- `plugins.local_http_module` → `LocalHTTPModule`
  - methods: `post_execute`

- `plugins.generated_math_plugin` → `GeneratedMathPlugin`
  - methods: `multiply`, `greet`

- `plugins.text_file_crud_plugin` → `TextFileCRUDPlugin`
  - methods: `create_text`, `read_text`, `update_text`, `delete_text`, `list_text_files`
  - allowed file extensions: `.txt`, `.md`, `.json`
  - supports nested relative paths under `base_dir` (for example `documents/notes/today.txt`)
  - blocks absolute paths and traversal outside `base_dir`

- `plugins.ssh_module` → `SSHModule`
  - methods: `run_command`, `list_directory`

- `plugins.plugin_generator` → `PluginGenerator`
  - methods: `create_plugin`

- `plugins.system_tools.terminal_introspection_plugin` → `TerminalIntrospectionPlugin`
  - methods: `get_environment_summary`, `list_directory`, `discover_folder_structure`, `pip_freeze`
  - purpose: cross-platform read-only environment introspection

- `plugins.system_tools.subprocess_plugin` → `SubprocessPlugin`
  - methods: `run_python_script`
  - purpose: run Python scripts via subprocess (can allow script paths outside base directory)

- `plugins.system_tools.excel_plugin` → `ExcelPlugin`
  - methods: `excel_to_json`, `list_columns_in_sheet`, `list_sheet_names`
  - purpose: export sheet rows to `.json`, inspect columns for a specific sheet, and list workbook sheet names

- `plugins.integrations.slack_plugin` → `SlackPlugin`
  - methods: `post_message`, `upload_text_file`, `upload_local_file`
  - purpose: post messages and upload text/local files using Slack's external file upload flow

- `plugins.integrations.openai_http_plugin` → `OpenAIHTTPPlugin`
  - methods: `generate_text`
  - style: raw HTTP request to OpenAI Responses API

- `plugins.integrations.openai_sdk_plugin` → `OpenAISDKPlugin`
  - methods: `generate_text`, `generate_text_with_history`, `reply_with_plugins`
  - style: official OpenAI SDK
  - memory: `generate_text_with_history` keeps conversation history in process memory by `conversation_id`

- `plugins.integrations.openai_plugin` → `OpenAIFunctionCallingPlugin`
  - methods: `generate_with_function_calls`, `generate_with_function_calls_and_history`
  - style: OpenAI function-calling (`tool_choice=auto`) that maps allowlisted plugin methods into callable tools

## Useful request JSON files in jsons/

### File/plugin examples
- `create_text_file_crud_plugin_request.json`
- `md_file_crud_create_request.json`
- `excel_to_json_request.json`
- `excel_list_sheets_metadata_request.json`
- `excel_list_sheet_names_request.json`
- `test_generated_math_plugin_request.json`
- `pika_publish_message_request.json`
- `pika_subscribe_request.json`
- `pika_consume_request.json`
- `pika_publish_workflow_request.json`
- `pika_consume_and_execute_workflow_request.json`

### Excel response notes
- `excel_to_json` now includes `column_names` in its success payload.
- `excel_list_sheets_metadata_request.json` now calls `list_columns_in_sheet` and returns:
  - `sheet_index`, `sheet_name`
  - `row_count`, `column_count`
  - `column_names`
  - `first_row_column_names` (mirrors parsed header names)
  - `first_data_row` (first row of data values, if present)
- `list_sheet_names` returns workbook-level `sheet_count` and `sheet_names`.

### SSH examples
- `ssh_list_directory_request.json`
- `workflows/ssh_sample_commands_workflow.json`

### Terminal introspection examples
- `terminal_list_directory_request.json`
- `terminal_discover_structure_request.json`
- `terminal_pip_freeze_request.json`
- `subprocess_run_python_script_request.json`

### Slack example
- `slack_send_joke_of_day_general_request.json`
- `slack_upload_text_file_request.json`
- `slack_upload_local_file_request.json`

### OpenAI examples
- `openai_http_generate_text_request.json`
- `openai_sdk_generate_text_request.json`
- `openai_sdk_generate_text_with_history_request.json`
- `openai_function_calling_generate_request.json`

### Workflow examples
- `workflows/workflow_read_readme_openai_sdk_reply.json`
- `workflows/workflow_read_notes_openai_sdk_reply.json`

## Reading files like notes.txt with OpenAI SDK
`OpenAISDKPlugin` does not directly read files from disk. Use workflow chaining:

1. Read file content with `TextFileCRUDPlugin.read_text`.
2. Pass `${steps.<step_id>.result.content}` into `OpenAISDKPlugin.generate_text` or `generate_text_with_history`.

Example: `workflows/workflow_read_notes_openai_sdk_reply.json` reads `generated_data/notes.txt` and sends content to `OpenAISDKPlugin`.

## TextFile CRUD nested paths
`TextFileCRUDPlugin` now supports nested relative file paths within `base_dir`.

Examples:
- `notes.txt`
- `documents/project-a/summary.md`
- `exports/data/result.json`

Use `list_text_files` to get recursive relative file paths.

## Priming memory for Slack with README context
To make Slack continue a seeded memory thread:

1. POST `jsons/workflows/openai/workflow_read_readme_openai_sdk_reply.json` to `/workflow`.
2. Set `SLACK_CONVERSATION_ID=readme-reply-thread` in environment/.env.
3. Start/restart app and send Slack messages.

Slack replies will continue the same conversation memory while the app process remains running.

### Slack AI environment variables
- `SLACK_OPENAI_MODEL` (default `gpt-4.1-mini`)
- `SLACK_OPENAI_MAX_TOOL_ROUNDS` (default `5`)
- `SLACK_CONVERSATION_ID` (optional fixed thread id)
- `SLACK_BOT_TOKEN` (required for posting replies and downloading private file attachments)

### Slack attachment behavior
- If an attached file is downloadable text, file content is included in prompt context.
- If download fails or content is non-text/HTML fallback, only metadata is included.
- Debug logs in `app.py` show file detection, download attempts, content-type checks, and duplicate-event suppression.

## Present but not allowlisted
- `plugins.generated_data_plugin` is present but not listed in `config.ALLOWED_MODULES`.

## Practical AI-agent usage tips
- Always use allowlisted module/class/method triples from `config.py`.
- Prefer `/execute` for single actions and `/workflow` for chained tasks.
- Treat API as strict about input types (`constructor_args` object, `args` array).
- Prefer object-style `args` payloads (single options object in `args[0]`) for readability; see `generated_data/docs/usage_tips/request_args_object_style.md`.
- Parse `status` on every response and handle `error` cases explicitly.
- Keep all constructor and method arguments JSON-serializable.
