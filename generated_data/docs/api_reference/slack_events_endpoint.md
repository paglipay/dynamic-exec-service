# Slack Events Endpoint

## Description
Handles Slack Events via the SlackEventAdapter if `SIGNING_SECRET` is set.

## Features
- Accepts normal message events and file shares
- Ignores duplicate deliveries
- Replies via function-calling using `OpenAIFunctionCallingPlugin`
- Downloads text-like file attachments using `SLACK_BOT_TOKEN` to include content in AI prompts

## Environment Variables
- `SIGNING_SECRET` (required for Slack event handling)
- `SLACK_BOT_TOKEN` (required for posting replies and downloading files)

## Usage Tips
- Enable by setting environment variables
- Monitor app logs for file download and event processing

## Example Event
```json
{
  "type": "event_callback",
  "event": {
    "type": "message",
    "text": "Hello!",
    "files": []
  }
}
```
