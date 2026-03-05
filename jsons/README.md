# JSON Request Layout

This folder is organized by domain so request samples are easier to find and maintain.

## Folders

- `integrations/openai/` — OpenAI HTTP/SDK/function-calling request samples
- `integrations/slack/` — Slack message/upload request samples
- `integrations/pika/` — RabbitMQ/pika publish/subscribe/consume/workflow samples
- `system_tools/excel/` — Excel plugin request samples
- `system_tools/apscheduler/` — APScheduler workflow scheduling request samples
- `system_tools/terminal/` — terminal introspection request samples
- `system_tools/pdf/` — PDF + markdown-to-pdf request samples
- `system_tools/ssh/` — SSH plugin request samples
- `system_tools/subprocess/` — subprocess plugin request samples
- `plugins/generator/` — plugin generator request samples
- `plugins/generated_math/` — generated math plugin request samples
- `plugins/text_file_crud/` — top-level text file CRUD request samples
- `text_file_crud_plugin/` — detailed CRUD test requests (existing set)
- `workflows/openai/` — OpenAI-oriented workflow samples
- `workflows/ssh/` — SSH workflow samples
- `workflows/excel/` — Excel workflow samples
- `workflows/plugins/` — plugin-focused workflow samples
- `workflows/general/` — generic workflow chain samples

## Notes

- Keep filenames unchanged when possible to preserve discoverability.
- For new requests, place files in the matching domain folder.
- Use object-style args when practical: `"args": [{ ... }]`.
