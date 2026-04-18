# How to Use the Slack Plugin

The Slack plugin allows you to interact with Slack workspace by posting messages, opening modals, uploading files, and more.

## Common Methods

### 1. request_modal_with_button
- **Purpose:** Post a message with a button that opens a modal in Slack.
- **Key Arguments:**
  - `channel` (string): The Slack channel ID where the message will be posted.
  - `button_text` (string): The text shown on the button.
  - `message_text` (string): The text message above the button.
  - `callback_id` (string): A unique identifier for the button interaction.
  - `modal_view` (JSON object): The modal view structure following Slack Block Kit format.

### 2. post_message
- **Purpose:** Post a plain message or message with blocks to a Slack channel.
- **Key Arguments:**
  - `channel` (string): Target Slack channel ID.
  - `text` (string): Text content of the message.
  - `blocks` (optional, JSON array): Structured blocks for rich message formatting.

### 3. upload_local_file
- **Purpose:** Upload a local file to a Slack channel.
- **Key Arguments:**
  - `file_path` (string): Local path to the file to upload.
  - `channel` (string): Slack channel ID to upload the file to.

## Example Usage

To post a modal form with a button:

```json
{
  "channel": "C01FMQWSWQN",
  "button_text": "Open Form",
  "message_text": "Click to open the form.",
  "callback_id": "form_button",
  "modal_view": { /* Slack Block Kit modal JSON */ }
}
```

To upload a file:

```json
{
  "file_path": "path/to/file.md",
  "channel": "C01FMQWSWQN"
}
```

Let me know if you want me to create example code snippets or help with other methods!