# Slack Image Detection and OpenAI Analysis Handoff

This document explains how the service detects Slack image attachments and hands them to OpenAI for analysis.

## Where this happens

- Slack event handling is implemented in [app.py](app.py).
- Image extraction is handled by `_extract_slack_file_context(...)`.
- The OpenAI analysis call is executed via `OpenAIFunctionCallingPlugin.generate_with_function_calls_and_history`.

## End-to-end flow

1. Slack sends a message event to `/slack/events`.
2. The handler accepts event subtype `None` or `file_share` and ignores bot-originated messages.
3. `_extract_slack_file_context(...)` reads `event["files"]`.
4. For each file, image candidacy is determined.
5. Candidate images are downloaded from Slack private URLs using the bot token.
6. Downloaded image bytes are:
   - saved locally under `generated_data/slack_downloads/...` (flat folder)
   - converted to Base64 data URLs (`data:<mime>;base64,...`)
7. The handler calls OpenAI with:
   - user text + file metadata suffix
   - `image_data_urls` as multimodal image inputs
8. OpenAI response text is posted back into Slack.

## Image detection rules

A Slack file is considered an image when either condition is true:

- `mimetype` starts with `image/`
- filename ends with one of:
  - `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`

Additional requirements before download/handoff:

- file has `url_private_download` or `url_private`
- `SLACK_BOT_TOKEN` is available
- image count has not exceeded `SLACK_MAX_IMAGE_COUNT` (default: 3)

## Download and safety limits

Binary downloads use authenticated requests to Slack private file URLs.

- Max bytes per image is controlled by `SLACK_MAX_IMAGE_BYTES` (default: 5 MB).
- If content type cannot be confirmed as an image, the fallback mime used for data URL is `image/png`.

## Local persistence behavior

When an image download succeeds, a local copy is written under:

- `generated_data/slack_downloads/`

The filename is sanitized, and a millisecond timestamp suffix is appended to keep filenames unique.
The filename is sanitized and saved as the original name (no timestamp suffix); repeated uploads with the same filename overwrite the existing local file.

## OpenAI handoff details

After extraction, the message handler calls:

- module: `plugins.integrations.openai_plugin`
- class: `OpenAIFunctionCallingPlugin`
- method: `generate_with_function_calls_and_history`

Arguments include:

- conversation id
- message text with Slack file metadata appended
- model name (from `SLACK_OPENAI_MODEL`, default `gpt-4.1-mini`)
- max tool rounds (from `SLACK_OPENAI_MAX_TOOL_ROUNDS`, default `5`)
- `image_data_urls` list for multimodal image analysis

## What the prompt includes

For transparency and traceability, prompt suffix content can include:

- file metadata (name/type/title/private URL)
- text file contents (for text-like attachments)
- image count included for analysis
- saved local image paths

## Operational notes

- If no text and no file context are present, no OpenAI request is made.
- If OpenAI generation fails, the service returns a safe fallback reply to Slack.
- If Slack token is missing at reply time, the service logs warning and skips posting.
