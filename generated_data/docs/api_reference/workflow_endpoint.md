# Workflow Endpoint

## Description
Executes a sequence of allowlisted steps with references between steps.

## Request Fields
- `steps`: Non-empty array of step objects
- `stop_on_error` (boolean, default `true`)
- Step `id` (optional, auto-indexed if omitted)
- Step `on_error`: `stop` or `continue` (optional)
- Supports referencing previous step results using `${steps.<id>.result}` syntax

## Response
- Success: JSON with `status: success`, `has_errors`, and `results` array
- Error: JSON with `status: error` and `message`

## Usage Tips
- Use workflows for multi-step tasks and chaining plugin calls.
- Use references to pass data between steps.
- Handle partial errors with `stop_on_error` and `on_error`.

## Example
```json
{
  "steps": [
    {"id": "step1", "module": "plugins.generated_math_plugin", "class": "GeneratedMathPlugin", "method": "multiply", "args": [3, 5]},
    {"id": "step2", "module": "plugins.generated_math_plugin", "class": "GeneratedMathPlugin", "method": "greet", "args": ["Result is ${steps.step1.result}"]}
  ]
}
```
