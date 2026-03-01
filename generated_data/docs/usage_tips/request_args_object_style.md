# Request JSON Style Guide: Object-Style `args`

This guide defines the preferred request payload style for AI agents and automation workflows.

## Goal

Keep `jsons/*.json` easy to read and edit by using **one structured object** in `args`, instead of many positional values.

Preferred pattern:

```json
"args": [
  {
    "field_a": "...",
    "field_b": true,
    "field_c": 123
  }
]
```

## Why this style

- More intuitive than `args[0]`, `args[1]`, `args[2]` mapping.
- Safer for future edits (field names are explicit).
- Easier for AI agents to generate and maintain.
- Aligns with existing intuitive examples such as Excel request payloads.

## Service contract reminder

`/execute` still requires:

- `constructor_args` as an object
- `args` as an array

So object-style inputs are represented as a **single object inside the `args` array**.

## Pika examples (current project)

### Publish message (preferred)

```json
{
  "module": "plugins.integrations.pika_plugin",
  "class": "PikaPlugin",
  "method": "publish_message",
  "constructor_args": {
    "host": "localhost",
    "port": 5672,
    "virtual_host": "/",
    "username": "guest",
    "password": "guest"
  },
  "args": [
    {
      "queue_name": "demo_queue",
      "message": {
        "event": "ping",
        "source": "dynamic-exec-service",
        "timestamp": "2026-03-01T00:00:00Z"
      },
      "durable": true,
      "persistent": true
    }
  ]
}
```

### Subscribe message (preferred)

```json
{
  "module": "plugins.integrations.pika_plugin",
  "class": "PikaPlugin",
  "method": "subscribe",
  "constructor_args": {
    "host": "localhost",
    "port": 5672,
    "virtual_host": "/",
    "username": "guest",
    "password": "guest"
  },
  "args": [
    {
      "queue_name": "demo_queue",
      "timeout_seconds": 5.0,
      "ack_message": true,
      "poll_interval_seconds": 0.2,
      "declare_queue": true,
      "durable": true
    }
  ]
}
```

### Publish workflow envelope (preferred)

```json
{
  "module": "plugins.integrations.pika_plugin",
  "class": "PikaPlugin",
  "method": "publish_workflow",
  "constructor_args": {
    "host": "localhost",
    "port": 5672,
    "virtual_host": "/",
    "username": "guest",
    "password": "guest"
  },
  "args": [
    {
      "queue_name": "workflow_queue",
      "workflow_id": "wf-sample-001",
      "max_retries": 2,
      "meta": {
        "source": "manual-test"
      },
      "workflow": {
        "stop_on_error": true,
        "steps": [
          {
            "id": "multiply_step",
            "module": "plugins.generated_math_plugin",
            "class": "GeneratedMathPlugin",
            "method": "multiply",
            "constructor_args": {},
            "args": [6, 7]
          }
        ]
      },
      "durable": true,
      "persistent": true
    }
  ]
}
```

### Consume and execute one workflow (preferred)

```json
{
  "module": "plugins.integrations.pika_plugin",
  "class": "PikaPlugin",
  "method": "consume_and_execute_workflow",
  "constructor_args": {
    "host": "localhost",
    "port": 5672,
    "virtual_host": "/",
    "username": "guest",
    "password": "guest"
  },
  "args": [
    {
      "queue_name": "workflow_queue",
      "timeout_seconds": 8.0,
      "poll_interval_seconds": 0.2,
      "declare_queue": true,
      "durable": true,
      "max_retries": 2,
      "dead_letter_queue": "workflow_queue_dlq"
    }
  ]
}
```

## Backward compatibility guidance

When changing plugin methods, prefer supporting both:

1. Legacy positional args (for existing callers)
2. New object-style args (for readability)

For new request files, default to object-style args.

## Agent checklist

When generating new request JSON files:

1. Use allowlisted `module` / `class` / `method` from `config.py`.
2. Put connection/setup values in `constructor_args`.
3. Put operation-specific values in a single object under `args[0]`.
4. Keep all values JSON-serializable.
5. Use descriptive field names and realistic sample values.
