# Execute Endpoint

## Description
Executes one allowlisted plugin method call.

## Request Fields
- `module` (string) - Module name of the plugin
- `class` (string) - Class name inside the module
- `method` (string) - Method name to call
- `constructor_args` (object, optional, defaults to `{}`) - Arguments for the class constructor
- `args` (array, optional, defaults to `[]`) - Arguments for the method

## Response
- Success: JSON with `status: success` and `result`
- Error: JSON with `status: error` and `message`

## Usage Tips
- Always check `status` and handle errors explicitly.
- Only use allowlisted module/class/method combinations.
- Keep all args JSON-serializable.

## Example
```json
{
  "module": "plugins.generated_math_plugin",
  "class": "GeneratedMathPlugin",
  "method": "multiply",
  "args": [6, 7]
}
```  