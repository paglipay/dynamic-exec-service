# API Usage Tips

- Always use allowlisted module/class/method combinations.
- For single actions, use the `/execute` endpoint.
- For multi-step workflows, use the `/workflow` endpoint with references.
- Always check the `status` field in responses and handle errors appropriately.
- Use JSON-serializable arguments and constructor args.
- Use environment variables for integrations (e.g., Slack `SIGNING_SECRET` and `SLACK_BOT_TOKEN`).
- Use the `TextFileCRUDPlugin` to read file contents before passing to plugins that cannot read files directly.
- For Slack integration, prime memory with README context via workflows and environment variables.
