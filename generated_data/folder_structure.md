# Best-Practice Documentation Folder Structure

This document describes the intended markdown documentation structure for this project and reflects the **current actual location** used by generated docs.

## Current Actual Location

Documentation currently lives under:

```
generated_data/docs/
  api_reference/
  plugin_research/
  usage_tips/
```

Current known files include:
- `generated_data/docs/api_reference/execute_endpoint.md`
- `generated_data/docs/api_reference/workflow_endpoint.md`
- `generated_data/docs/api_reference/slack_events_endpoint.md`
- `generated_data/docs/plugin_research/generated_math_plugin.md`
- `generated_data/docs/plugin_research/text_file_crud_plugin.md`
- `generated_data/docs/plugin_research/ssh_module.md`
- `generated_data/docs/usage_tips/api_usage_tips.md`

## Recommended Target Structure

```
generated_data/docs/
  api_reference/
  plugin_research/
  workflow_examples/
  integration_guides/
  usage_tips/
  README.md
```

## Intent by Folder

### generated_data/docs/api_reference
- Endpoint-level behavior and examples for:
  - `/execute`
  - `/workflow`
  - `/slack/events`

### generated_data/docs/plugin_research
- Capability notes per allowlisted plugin module/class/method.
- Include constraints (path limits, allowed file types, auth requirements).

### generated_data/docs/workflow_examples
- Markdown explanations of workflow JSON examples from `jsons/workflows/`.
- Explain step IDs, references like `${steps.<id>.result}`, and error handling choices.

### generated_data/docs/integration_guides
- Integration playbooks for Slack, OpenAI, SSH, Excel, and subprocess usage.
- Include required environment variables and OAuth/API scopes where relevant.

### generated_data/docs/usage_tips
- Safe-operation guidance for AI agents.
- Request-shape reminders (`constructor_args` object, `args` array).

## Naming Conventions
- Use lowercase file names with underscores.
- Use `.md` extension.
- Prefer descriptive names tied to route/plugin intent.

## Notes for AI Agents
- Treat this structure as documentation intent; source of truth remains code/config.
- Always validate runnable methods against `config.ALLOWED_MODULES`.
- Link docs to concrete JSON examples under `jsons/` and `jsons/workflows/`.
