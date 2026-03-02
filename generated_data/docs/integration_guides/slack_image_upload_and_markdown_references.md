# Slack Image Upload and Markdown Reference Guide

This guide explains how Slack image attachments are saved locally and how Markdown files should reference them.

## Local save location (current behavior)

Slack image files are persisted under `generated_data` using this directory pattern:

`target_dir = (base_dir / "slack_downloads").resolve()`

Where:
- `base_dir` defaults to `generated_data`
- files are stored directly in this folder (flat structure)

Example output path:

`generated_data/slack_downloads/sample_1740780000000.png`

## Important reference rule

Any file that needs to reference these saved images should reference that location.

When creating Markdown (`.md`) in your workflows or tools, **prefer base_dir-relative paths**:

`slack_downloads/...`

This is the preferred option because files you create are typically also under `generated_data` (`base_dir`), so the reference stays consistent.

Alternative (also supported):

`generated_data/slack_downloads/...`

The OpenAI integration prompt now includes this directory convention so agent responses can reference the right location.

## Markdown authoring examples

### 1) Preferred: relative root under base_dir

`![Switch Photo](slack_downloads/sample_1740780000000.png)`

### 2) Direct path under generated_data

`![Switch Photo](generated_data/slack_downloads/sample_1740780000000.png)`

### 3) Example for files created in generated_data

If your markdown file is created in `generated_data` (for example `generated_data/notes.md`), reference images like:

`![Switch Photo](slack_downloads/sample_1740780000000.png)`

## Notes

- Files are stored in a flat `slack_downloads` directory (no nested channel/date folders).
- Prefer `slack_downloads/...` for portability across files created under `generated_data`.
- Any markdown/image-link normalization should be handled by the caller workflow or prompt logic.
- Remote URLs (`http://`, `https://`, `data:`) are left unchanged.
