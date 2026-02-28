# Dynamic Exec Service — AI Usage Notes

This README is generated through the allowlisted `TextFileCRUDPlugin` and is intended to help an AI agent understand how to use the current service safely.

## What this application does
- Exposes a Flask API for controlled dynamic plugin execution.
- Enforces module/class/method allowlisting from `config.ALLOWED_MODULES`.
- Returns standardized JSON responses for success and errors.

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

Flow:
1. Validate payload shape and field types.
2. Validate request against allowlist (`validate_request`).
3. Instantiate plugin class (`executor.instantiate`).
4. Call method (`executor.call_method`).

### POST /workflow
Executes a sequence of allowlisted steps.

Workflow features:
- `steps`: non-empty array of step objects.
- `stop_on_error`: boolean (default behavior is stop on error).
- Optional `id` per step (auto-indexed if omitted).
- Optional per-step `on_error`: `stop` or `continue`.
- Supports step result references in inputs: `${steps.<id>.result}` or `${steps.<id>.result.<path>}`.

Typical success response:
```json
{"status":"success","has_errors":false,"results":[...]}
```

## Plugins currently present in plugins/
Note: only plugins listed in `config.ALLOWED_MODULES` are invokable through the API.

### Allowlisted in config.py
- `plugins.sample_module` → class `SampleModule`
  - methods: `add(x, y)`, `process()`
  - purpose: arithmetic and simple string processing.

- `plugins.local_http_module` → class `LocalHTTPModule`
  - methods: `post_execute(payload)`
  - purpose: sends a JSON payload to local `http://localhost:5000/execute`.

- `plugins.generated_math_plugin` → class `GeneratedMathPlugin`
  - allowlisted methods: `multiply(x, y)`, `greet(name)`
  - file also contains extra methods (`add`, `subtract`, `divide`) but they are NOT allowlisted.

- `plugins.text_file_crud_plugin` → class `TextFileCRUDPlugin`
  - methods: `create_text`, `read_text`, `update_text`, `delete_text`, `list_text_files`
  - purpose: CRUD for files in `base_dir` (`generated_data` by default).
  - current extension policy: `.txt` and `.md` filenames only.

- `plugins.ssh_module` → class `SSHModule`
  - methods: `run_command(command)`, `list_directory(path)`
  - purpose: execute SSH commands with explicit credentials.

- `plugins.plugin_generator` → class `PluginGenerator`
  - methods: `create_plugin(plugin_definition)`
  - purpose: generate safe plugin source files from validated definitions/templates.

### Present but currently not allowlisted
- `plugins.generated_data_plugin` (not listed in `config.ALLOWED_MODULES`, so not executable via API).

## Practical AI-agent usage tips
- Always build requests that match allowlisted module/class/method triples.
- Prefer `/execute` for single calls and `/workflow` for chained tasks.
- Parse and branch on `status` in API responses.
- Keep constructor and method args JSON-serializable.
- Treat non-allowlisted methods as unavailable, even if they exist in source files.
