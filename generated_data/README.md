# Dynamic Exec Service — AI Usage Notes

This README is intended to help an AI agent use the current service safely and correctly.

## What this application does
- Exposes a Flask API for controlled dynamic plugin execution.
- Enforces module/class/method allowlisting from `config.ALLOWED_MODULES`.
- Returns standardized JSON responses for success and errors.
- Supports Slack Events at `/slack/events` when `SIGNING_SECRET` is available (from environment or `.env`).

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
- Current handler listens for `message` events and logs channel/user/text for non-bot messages.

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
  - allowed file extensions: `.txt`, `.md`

- `plugins.ssh_module` → `SSHModule`
  - methods: `run_command`, `list_directory`

- `plugins.plugin_generator` → `PluginGenerator`
  - methods: `create_plugin`

- `plugins.system_tools.terminal_introspection_plugin` → `TerminalIntrospectionPlugin`
  - methods: `get_environment_summary`, `list_directory`, `discover_folder_structure`, `pip_freeze`
  - purpose: cross-platform read-only environment introspection

- `plugins.integrations.slack_plugin` → `SlackPlugin`
  - methods: `post_message`

- `plugins.integrations.openai_http_plugin` → `OpenAIHTTPPlugin`
  - methods: `generate_text`
  - style: raw HTTP request to OpenAI Responses API

- `plugins.integrations.openai_sdk_plugin` → `OpenAISDKPlugin`
  - methods: `generate_text`, `generate_text_with_history`
  - style: official OpenAI SDK
  - memory: `generate_text_with_history` keeps conversation history in process memory by `conversation_id`

## Useful request JSON files in jsons/

### File/plugin examples
- `create_text_file_crud_plugin_request.json`
- `md_file_crud_create_request.json`
- `test_generated_math_plugin_request.json`

### Terminal introspection examples
- `terminal_list_directory_request.json`
- `terminal_discover_structure_request.json`
- `terminal_pip_freeze_request.json`

### Slack example
- `slack_send_joke_of_day_general_request.json`

### OpenAI examples
- `openai_http_generate_text_request.json`
- `openai_sdk_generate_text_request.json`
- `openai_sdk_generate_text_with_history_request.json`

## Present but not allowlisted
- `plugins.generated_data_plugin` is present but not listed in `config.ALLOWED_MODULES`.

## Practical AI-agent usage tips
- Always use allowlisted module/class/method triples from `config.py`.
- Prefer `/execute` for single actions and `/workflow` for chained tasks.
- Treat API as strict about input types (`constructor_args` object, `args` array).
- Parse `status` on every response and handle `error` cases explicitly.
- Keep all constructor and method arguments JSON-serializable.
