# Dynamic Exec Service — Agent Skill

## Project intent
This project exposes a Flask API that executes **only allowlisted plugin methods** from JSON requests.
It is a controlled dynamic execution service, not a general-purpose remote code runner.

## Primary runtime flow
1. `POST /execute` receives JSON.
2. Request fields are validated for shape and type.
3. Module/class/method are validated against `config.ALLOWED_MODULES`.
4. `JSONExecutor.instantiate(...)` imports and creates the plugin instance.
5. `JSONExecutor.call_method(...)` executes the requested allowlisted method.
6. API returns standardized success/error JSON.

## Core files and responsibilities
- `app.py`: Flask entrypoint, request validation, error handling, endpoint wiring.
- `config.py`: execution allowlist (`ALLOWED_MODULES`) for modules/classes/methods.
- `executor/permissions.py`: allowlist checks (`validate_module/class/method/request`).
- `executor/engine.py`: dynamic import, instance cache, method invocation.
- `plugins/sample_module.py`: simple demo plugin.
- `plugins/ssh_module.py`: SSH-backed plugin using Paramiko.
- `plugins/plugin_generator.py`: safe plugin file generation with strict validation.

## API contract
`POST /execute` body:
```json
{
  "module": "plugins.sample_module",
  "class": "SampleModule",
  "method": "add",
  "constructor_args": {"name": "demo", "data": "x"},
  "args": [1, 2]
}
```

Required fields: `module`, `class`, `method`.
Optional fields: `constructor_args` (object, default `{}`), `args` (array, default `[]`).

Success response:
```json
{"status":"success","result": ...}
```

Error response:
```json
{"status":"error","message":"..."}
```

## Security model (important)
- Execution is constrained by a static allowlist in `config.py`.
- Reject non-allowlisted modules/classes/methods.
- `PluginGenerator` blocks dangerous tokens and only allows one-line `return` bodies.
- `SSHModule` requires explicit credentials and validates user inputs.

## Agent guidance for code changes
When modifying this project:
1. Preserve allowlist-first security checks (do not bypass `validate_*`).
2. Keep API error format stable: `{"status":"error","message":...}`.
3. If adding a plugin:
   - implement plugin module under `plugins/`.
   - add module/class/method allowlist entry in `config.py`.
   - keep constructor args and method args JSON-serializable.
4. Avoid adding unrestricted eval/exec/import behavior.
5. Prefer explicit, minimal exceptions and user-safe error messages.

## Local run
- Install deps: `pip install -r requirements.txt`
- Start service: `python app.py`
- Default bind: `0.0.0.0:5000`

## Known behavior notes
- `JSONExecutor` stores instances by `module_name` in-memory for process lifetime.
- A method call fails if the module instance was not initialized first in the current process.
- The current `/execute` flow initializes and then calls in one request, so normal usage works.
