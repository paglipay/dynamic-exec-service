"""RabbitMQ integration plugin using pika."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import pika

from executor.engine import JSONExecutor
from executor.permissions import validate_request


class PikaPlugin:
    """Publish messages to RabbitMQ queues with basic validation."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5672,
        virtual_host: str = "/",
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        if not isinstance(host, str) or not host.strip():
            raise ValueError("host must be a non-empty string")
        if not isinstance(port, int) or port <= 0:
            raise ValueError("port must be a positive integer")
        if not isinstance(virtual_host, str) or not virtual_host.strip():
            raise ValueError("virtual_host must be a non-empty string")
        if (username is None) ^ (password is None):
            raise ValueError("username and password must be provided together")
        if username is not None and (not username.strip() or not password or not password.strip()):
            raise ValueError("username and password must be non-empty when provided")

        self.host = host.strip()
        self.port = port
        self.virtual_host = virtual_host.strip()
        self.username = username.strip() if isinstance(username, str) else None
        self.password = password.strip() if isinstance(password, str) else None
        self._processed_workflow_ids: set[str] = set()
        self._workflow_ref_pattern = re.compile(r"^\$\{steps\.([^\.]+)\.result(?:\.(.+))?\}$")

    def _validate_execution_fields(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str, str, dict[str, Any], list[Any]]:
        required_fields = ["module", "class", "method"]
        missing_fields = [field for field in required_fields if field not in payload]
        if missing_fields:
            raise ValueError(f"Missing required field(s): {', '.join(missing_fields)}")

        module_name = payload.get("module")
        class_name = payload.get("class")
        method_name = payload.get("method")
        constructor_args = payload.get("constructor_args", {})
        args = payload.get("args", [])

        if not isinstance(module_name, str) or not module_name:
            raise ValueError("module must be a non-empty string")
        if not isinstance(class_name, str) or not class_name:
            raise ValueError("class must be a non-empty string")
        if not isinstance(method_name, str) or not method_name:
            raise ValueError("method must be a non-empty string")
        if not isinstance(constructor_args, dict):
            raise ValueError("constructor_args must be an object")
        if not isinstance(args, list):
            raise ValueError("args must be an array")

        return module_name, class_name, method_name, constructor_args, args

    def _resolve_result_path(self, value: Any, path: str) -> Any:
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise ValueError(f"Reference path '{path}' was not found in step result")
            current = current[part]
        return current

    def _resolve_references(self, value: Any, step_results: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: self._resolve_references(item, step_results) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_references(item, step_results) for item in value]
        if isinstance(value, str):
            match = self._workflow_ref_pattern.fullmatch(value.strip())
            if match is None:
                return value

            step_id = match.group(1)
            result_path = match.group(2)
            if step_id not in step_results:
                raise ValueError(f"Referenced step '{step_id}' has no available result")

            resolved = step_results[step_id]
            if result_path:
                return self._resolve_result_path(resolved, result_path)
            return resolved

        return value

    def _execute_workflow_payload(self, workflow_payload: dict[str, Any]) -> dict[str, Any]:
        steps = workflow_payload.get("steps")
        stop_on_error = workflow_payload.get("stop_on_error", True)
        if not isinstance(steps, list) or not steps:
            raise ValueError("workflow.steps must be a non-empty array")
        if not isinstance(stop_on_error, bool):
            raise ValueError("workflow.stop_on_error must be a boolean")

        executor = JSONExecutor()
        step_results: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        has_errors = False

        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Step {index} must be an object")

            step_id = step.get("id", str(index))
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError(f"Step {index} id must be a non-empty string")
            step_id = step_id.strip()

            if step_id in step_results:
                raise ValueError(f"Duplicate step id '{step_id}'")

            step_on_error = step.get("on_error", "stop" if stop_on_error else "continue")
            if step_on_error not in {"stop", "continue"}:
                raise ValueError(f"Step '{step_id}' on_error must be 'stop' or 'continue'")

            module_name, class_name, method_name, constructor_args, args = self._validate_execution_fields(step)
            constructor_args = self._resolve_references(constructor_args, step_results)
            args = self._resolve_references(args, step_results)

            try:
                validate_request(module_name, class_name, method_name)
                executor.instantiate(module_name, class_name, constructor_args)
                result = executor.call_method(module_name, method_name, args)
                step_results[step_id] = result
                results.append({"id": step_id, "status": "success", "result": result})
            except (ValueError, ImportError, AttributeError, TypeError) as exc:
                has_errors = True
                message = str(exc) if str(exc) else "Invalid execution request"
                results.append({"id": step_id, "status": "error", "message": message})
                if step_on_error == "stop":
                    return {
                        "status": "error",
                        "message": f"Workflow failed at step '{step_id}'",
                        "failed_step": step_id,
                        "results": results,
                    }

        return {"status": "success", "has_errors": has_errors, "results": results}

    def _validate_workflow_payload(self, workflow_payload: dict[str, Any]) -> None:
        steps = workflow_payload.get("steps")
        stop_on_error = workflow_payload.get("stop_on_error", True)
        if not isinstance(steps, list) or not steps:
            raise ValueError("workflow.steps must be a non-empty array")
        if not isinstance(stop_on_error, bool):
            raise ValueError("workflow.stop_on_error must be a boolean")

        seen_ids: set[str] = set()
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Step {index} must be an object")

            step_id = step.get("id", str(index))
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError(f"Step {index} id must be a non-empty string")
            step_id = step_id.strip()
            if step_id in seen_ids:
                raise ValueError(f"Duplicate step id '{step_id}'")
            seen_ids.add(step_id)

            step_on_error = step.get("on_error")
            if step_on_error is not None and step_on_error not in {"stop", "continue"}:
                raise ValueError(f"Step '{step_id}' on_error must be 'stop' or 'continue'")

            module_name, class_name, method_name, _constructor_args, _args = self._validate_execution_fields(step)
            validate_request(module_name, class_name, method_name)

    def _normalize_workflow_envelope(self, payload: dict[str, Any], default_workflow_id: str) -> dict[str, Any]:
        workflow_id = payload.get("workflow_id", default_workflow_id)
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("workflow_id must be a non-empty string")
        workflow_id = workflow_id.strip()

        workflow = payload.get("workflow")
        if workflow is None:
            workflow = payload
        if not isinstance(workflow, dict):
            raise ValueError("workflow must be an object")

        retry_count = payload.get("retry_count", 0)
        if not isinstance(retry_count, int) or retry_count < 0:
            raise ValueError("retry_count must be a non-negative integer")

        max_retries = payload.get("max_retries", 3)
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")

        meta = payload.get("meta", {})
        if not isinstance(meta, dict):
            raise ValueError("meta must be an object")

        return {
            "workflow_id": workflow_id,
            "workflow": workflow,
            "retry_count": retry_count,
            "max_retries": max_retries,
            "meta": meta,
        }

    def _create_connection(self) -> pika.BlockingConnection:
        credentials: pika.PlainCredentials | None = None
        if self.username is not None and self.password is not None:
            credentials = pika.PlainCredentials(self.username, self.password)

        params = pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            virtual_host=self.virtual_host,
            credentials=credentials,
            heartbeat=30,
            blocked_connection_timeout=10,
        )
        return pika.BlockingConnection(params)

    def _connection_troubleshooting_hint(self, exc: Exception) -> str:
        """Return a compact hint for common RabbitMQ connection failures."""
        raw = str(exc)
        lowered = raw.lower()

        if "incompatibleprotocolerror" in lowered or "transport indicated eof" in lowered:
            return (
                "Protocol handshake failed. Verify RabbitMQ is listening on AMQP port 5672 "
                "(not HTTP management port 15672), and check whether TLS/non-TLS settings match."
            )
        if "access refused" in lowered or "authentication" in lowered:
            return "Authentication failed. Verify username/password and broker auth configuration."
        if "connection refused" in lowered:
            return "Connection refused. Verify RabbitMQ service is running and reachable at host/port."
        if "timed out" in lowered or "timeout" in lowered:
            return "Connection timed out. Verify host, port, firewall, and network routing."

        return "Verify host, port, credentials, virtual host, and TLS settings."

    def publish_message(
        self,
        queue_name: str | dict[str, Any],
        message: Any = None,
        durable: bool = True,
        persistent: bool = True,
    ) -> dict[str, Any]:
        """Publish a message to a queue and return publish metadata.

        Supports either positional args or a single options object as the first arg.
        """
        if isinstance(queue_name, dict):
            options = queue_name
            queue_name = options.get("queue_name", "")
            message = options.get("message")
            durable = options.get("durable", durable)
            persistent = options.get("persistent", persistent)

        if not isinstance(queue_name, str) or not queue_name.strip():
            raise ValueError("queue_name must be a non-empty string")
        if message is None:
            raise ValueError("message is required")
        if not isinstance(durable, bool):
            raise ValueError("durable must be a boolean")
        if not isinstance(persistent, bool):
            raise ValueError("persistent must be a boolean")

        content_type = "text/plain"
        if isinstance(message, str):
            body = message
        else:
            try:
                body = json.dumps(message)
            except (TypeError, ValueError) as exc:
                raise ValueError("message must be JSON-serializable or a string") from exc
            content_type = "application/json"

        delivery_mode = 2 if persistent else 1

        connection: pika.BlockingConnection | None = None
        try:
            connection = self._create_connection()
            channel = connection.channel()
            target_queue = queue_name.strip()
            channel.queue_declare(queue=target_queue, durable=durable)
            channel.basic_publish(
                exchange="",
                routing_key=target_queue,
                body=body,
                properties=pika.BasicProperties(
                    content_type=content_type,
                    delivery_mode=delivery_mode,
                ),
            )
        except (pika.exceptions.AMQPError, OSError) as exc:
            hint = self._connection_troubleshooting_hint(exc)
            raise ValueError(f"Failed to publish message to RabbitMQ: {exc}. {hint}") from exc
        finally:
            if connection is not None and connection.is_open:
                connection.close()

        return {
            "status": "success",
            "queue_name": queue_name.strip(),
            "content_type": content_type,
            "delivery_mode": delivery_mode,
            "body_size_bytes": len(body.encode("utf-8")),
            "message": "Message published",
        }

    def publish_workflow(
        self,
        queue_name: str | dict[str, Any],
        workflow: dict[str, Any] | None = None,
        workflow_id: str | None = None,
        meta: dict[str, Any] | None = None,
        durable: bool = True,
        persistent: bool = True,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Publish a workflow envelope message for downstream execution."""
        if isinstance(queue_name, dict):
            options = queue_name
            queue_name = options.get("queue_name", "")
            workflow = options.get("workflow")
            workflow_id = options.get("workflow_id")
            meta = options.get("meta")
            durable = options.get("durable", durable)
            persistent = options.get("persistent", persistent)
            max_retries = options.get("max_retries", max_retries)

        if not isinstance(queue_name, str) or not queue_name.strip():
            raise ValueError("queue_name must be a non-empty string")
        if workflow is None or not isinstance(workflow, dict):
            raise ValueError("workflow must be an object")
        if workflow_id is not None and (not isinstance(workflow_id, str) or not workflow_id.strip()):
            raise ValueError("workflow_id must be a non-empty string when provided")
        if meta is not None and not isinstance(meta, dict):
            raise ValueError("meta must be an object when provided")
        if not isinstance(durable, bool):
            raise ValueError("durable must be a boolean")
        if not isinstance(persistent, bool):
            raise ValueError("persistent must be a boolean")
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")

        normalized_workflow_id = workflow_id.strip() if isinstance(workflow_id, str) else f"wf-{int(time.time() * 1000)}"
        envelope = self._normalize_workflow_envelope(
            {
                "workflow_id": normalized_workflow_id,
                "workflow": workflow,
                "retry_count": 0,
                "max_retries": max_retries,
                "meta": meta or {},
            },
            default_workflow_id=normalized_workflow_id,
        )
        self._validate_workflow_payload(envelope["workflow"])

        publish_result = self.publish_message(
            {
                "queue_name": queue_name.strip(),
                "message": envelope,
                "durable": durable,
                "persistent": persistent,
            }
        )
        publish_result["workflow_id"] = envelope["workflow_id"]
        publish_result["message_type"] = "workflow_envelope"
        return publish_result

    def subscribe(
        self,
        queue_name: str | dict[str, Any],
        timeout_seconds: float = 5.0,
        ack_message: bool = True,
        poll_interval_seconds: float = 0.2,
        declare_queue: bool = False,
        durable: bool = True,
    ) -> dict[str, Any]:
        """Read one message from a queue, waiting up to timeout_seconds.

        Supports either positional args or a single options object as the first arg.
        """
        if isinstance(queue_name, dict):
            options = queue_name
            queue_name = options.get("queue_name", "")
            timeout_seconds = options.get("timeout_seconds", timeout_seconds)
            ack_message = options.get("ack_message", ack_message)
            poll_interval_seconds = options.get("poll_interval_seconds", poll_interval_seconds)
            declare_queue = options.get("declare_queue", declare_queue)
            durable = options.get("durable", durable)

        if not isinstance(queue_name, str) or not queue_name.strip():
            raise ValueError("queue_name must be a non-empty string")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        if not isinstance(ack_message, bool):
            raise ValueError("ack_message must be a boolean")
        if not isinstance(poll_interval_seconds, (int, float)) or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be a positive number")
        if not isinstance(declare_queue, bool):
            raise ValueError("declare_queue must be a boolean")
        if not isinstance(durable, bool):
            raise ValueError("durable must be a boolean")

        target_queue = queue_name.strip()
        deadline = time.monotonic() + float(timeout_seconds)
        connection: pika.BlockingConnection | None = None

        try:
            connection = self._create_connection()
            channel = connection.channel()

            if declare_queue:
                channel.queue_declare(queue=target_queue, durable=durable)

            while True:
                method_frame, header_frame, body = channel.basic_get(
                    queue=target_queue,
                    auto_ack=False,
                )

                if method_frame is not None:
                    if ack_message:
                        channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    else:
                        channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)

                    body_text = body.decode("utf-8", errors="replace")
                    parsed_json: Any = None
                    try:
                        parsed_json = json.loads(body_text)
                    except (TypeError, ValueError):
                        parsed_json = None

                    result: dict[str, Any] = {
                        "status": "success",
                        "queue_name": target_queue,
                        "has_message": True,
                        "ack_message": ack_message,
                        "delivery_tag": method_frame.delivery_tag,
                        "redelivered": method_frame.redelivered,
                        "content_type": header_frame.content_type if header_frame else None,
                        "body_size_bytes": len(body),
                        "body_text": body_text,
                        "message": "Message received",
                    }
                    if parsed_json is not None:
                        result["body_json"] = parsed_json
                    return result

                if time.monotonic() >= deadline:
                    return {
                        "status": "success",
                        "queue_name": target_queue,
                        "has_message": False,
                        "ack_message": ack_message,
                        "message": "No message received before timeout",
                    }

                time.sleep(float(poll_interval_seconds))
        except (pika.exceptions.AMQPError, OSError) as exc:
            hint = self._connection_troubleshooting_hint(exc)
            raise ValueError(f"Failed to subscribe from RabbitMQ queue: {exc}. {hint}") from exc
        finally:
            if connection is not None and connection.is_open:
                connection.close()

    def consume(
        self,
        queue_name: str | dict[str, Any],
        max_messages: int = 10,
        timeout_seconds: float = 5.0,
        ack_message: bool = True,
        poll_interval_seconds: float = 0.2,
        declare_queue: bool = False,
        durable: bool = True,
    ) -> dict[str, Any]:
        """Consume up to max_messages from a queue within timeout_seconds.

        Supports either positional args or a single options object as the first arg.
        """
        if isinstance(queue_name, dict):
            options = queue_name
            queue_name = options.get("queue_name", "")
            max_messages = options.get("max_messages", max_messages)
            timeout_seconds = options.get("timeout_seconds", timeout_seconds)
            ack_message = options.get("ack_message", ack_message)
            poll_interval_seconds = options.get("poll_interval_seconds", poll_interval_seconds)
            declare_queue = options.get("declare_queue", declare_queue)
            durable = options.get("durable", durable)

        if not isinstance(queue_name, str) or not queue_name.strip():
            raise ValueError("queue_name must be a non-empty string")
        if not isinstance(max_messages, int) or max_messages <= 0:
            raise ValueError("max_messages must be a positive integer")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        if not isinstance(ack_message, bool):
            raise ValueError("ack_message must be a boolean")
        if not isinstance(poll_interval_seconds, (int, float)) or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be a positive number")
        if not isinstance(declare_queue, bool):
            raise ValueError("declare_queue must be a boolean")
        if not isinstance(durable, bool):
            raise ValueError("durable must be a boolean")

        target_queue = queue_name.strip()
        deadline = time.monotonic() + float(timeout_seconds)
        messages: list[dict[str, Any]] = []
        connection: pika.BlockingConnection | None = None

        try:
            connection = self._create_connection()
            channel = connection.channel()

            if declare_queue:
                channel.queue_declare(queue=target_queue, durable=durable)

            while len(messages) < max_messages and time.monotonic() < deadline:
                method_frame, header_frame, body = channel.basic_get(
                    queue=target_queue,
                    auto_ack=False,
                )

                if method_frame is None:
                    time.sleep(float(poll_interval_seconds))
                    continue

                if ack_message:
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                else:
                    channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)

                body_text = body.decode("utf-8", errors="replace")
                parsed_json: Any = None
                try:
                    parsed_json = json.loads(body_text)
                except (TypeError, ValueError):
                    parsed_json = None

                item: dict[str, Any] = {
                    "delivery_tag": method_frame.delivery_tag,
                    "redelivered": method_frame.redelivered,
                    "content_type": header_frame.content_type if header_frame else None,
                    "body_size_bytes": len(body),
                    "body_text": body_text,
                }
                if parsed_json is not None:
                    item["body_json"] = parsed_json
                messages.append(item)

            return {
                "status": "success",
                "queue_name": target_queue,
                "ack_message": ack_message,
                "requested_max_messages": max_messages,
                "consumed_count": len(messages),
                "has_messages": len(messages) > 0,
                "messages": messages,
                "message": "Messages consumed" if messages else "No messages consumed before timeout",
            }
        except (pika.exceptions.AMQPError, OSError) as exc:
            hint = self._connection_troubleshooting_hint(exc)
            raise ValueError(f"Failed to consume from RabbitMQ queue: {exc}. {hint}") from exc
        finally:
            if connection is not None and connection.is_open:
                connection.close()

    def consume_and_execute_workflow(
        self,
        queue_name: str | dict[str, Any],
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.2,
        declare_queue: bool = False,
        durable: bool = True,
        max_retries: int = 3,
        dead_letter_queue: str | None = None,
    ) -> dict[str, Any]:
        """Consume one workflow message, execute it, and handle retry/DLQ."""
        if isinstance(queue_name, dict):
            options = queue_name
            queue_name = options.get("queue_name", "")
            timeout_seconds = options.get("timeout_seconds", timeout_seconds)
            poll_interval_seconds = options.get("poll_interval_seconds", poll_interval_seconds)
            declare_queue = options.get("declare_queue", declare_queue)
            durable = options.get("durable", durable)
            max_retries = options.get("max_retries", max_retries)
            dead_letter_queue = options.get("dead_letter_queue", dead_letter_queue)

        if not isinstance(queue_name, str) or not queue_name.strip():
            raise ValueError("queue_name must be a non-empty string")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        if not isinstance(poll_interval_seconds, (int, float)) or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be a positive number")
        if not isinstance(declare_queue, bool):
            raise ValueError("declare_queue must be a boolean")
        if not isinstance(durable, bool):
            raise ValueError("durable must be a boolean")
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if dead_letter_queue is not None and (not isinstance(dead_letter_queue, str) or not dead_letter_queue.strip()):
            raise ValueError("dead_letter_queue must be a non-empty string when provided")

        target_queue = queue_name.strip()
        dlq_name = dead_letter_queue.strip() if isinstance(dead_letter_queue, str) else None
        deadline = time.monotonic() + float(timeout_seconds)
        connection: pika.BlockingConnection | None = None

        try:
            connection = self._create_connection()
            channel = connection.channel()

            if declare_queue:
                channel.queue_declare(queue=target_queue, durable=durable)
                if dlq_name:
                    channel.queue_declare(queue=dlq_name, durable=durable)

            while True:
                method_frame, _header_frame, body = channel.basic_get(
                    queue=target_queue,
                    auto_ack=False,
                )

                if method_frame is None:
                    if time.monotonic() >= deadline:
                        return {
                            "status": "success",
                            "queue_name": target_queue,
                            "has_message": False,
                            "message": "No workflow message received before timeout",
                        }
                    time.sleep(float(poll_interval_seconds))
                    continue

                body_text = body.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(body_text)
                except (TypeError, ValueError):
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    if dlq_name:
                        channel.basic_publish(
                            exchange="",
                            routing_key=dlq_name,
                            body=json.dumps(
                                {
                                    "reason": "invalid_json",
                                    "original_body": body_text,
                                }
                            ),
                            properties=pika.BasicProperties(
                                content_type="application/json",
                                delivery_mode=2,
                            ),
                        )
                    return {
                        "status": "error",
                        "queue_name": target_queue,
                        "has_message": True,
                        "executed": False,
                        "message": "Consumed message was not valid JSON",
                    }

                if not isinstance(payload, dict):
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    return {
                        "status": "error",
                        "queue_name": target_queue,
                        "has_message": True,
                        "executed": False,
                        "message": "Consumed message must be a JSON object",
                    }

                default_workflow_id = f"wf-{method_frame.delivery_tag}-{int(time.time() * 1000)}"
                envelope = self._normalize_workflow_envelope(payload, default_workflow_id)
                workflow_id = envelope["workflow_id"]

                if workflow_id in self._processed_workflow_ids:
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    return {
                        "status": "success",
                        "queue_name": target_queue,
                        "has_message": True,
                        "workflow_id": workflow_id,
                        "executed": False,
                        "duplicate": True,
                        "message": "Workflow already processed",
                    }

                execution_result = self._execute_workflow_payload(envelope["workflow"])
                if execution_result.get("status") == "success":
                    self._processed_workflow_ids.add(workflow_id)
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    return {
                        "status": "success",
                        "queue_name": target_queue,
                        "has_message": True,
                        "workflow_id": workflow_id,
                        "executed": True,
                        "retry_count": envelope["retry_count"],
                        "workflow_result": execution_result,
                    }

                next_retry_count = envelope["retry_count"] + 1
                retry_limit = min(max_retries, envelope["max_retries"])

                if next_retry_count <= retry_limit:
                    retry_envelope = dict(envelope)
                    retry_envelope["retry_count"] = next_retry_count
                    channel.basic_publish(
                        exchange="",
                        routing_key=target_queue,
                        body=json.dumps(retry_envelope),
                        properties=pika.BasicProperties(
                            content_type="application/json",
                            delivery_mode=2,
                        ),
                    )
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    return {
                        "status": "error",
                        "queue_name": target_queue,
                        "has_message": True,
                        "workflow_id": workflow_id,
                        "executed": False,
                        "retried": True,
                        "retry_count": next_retry_count,
                        "max_retries": retry_limit,
                        "workflow_result": execution_result,
                        "message": "Workflow execution failed and was requeued",
                    }

                if dlq_name:
                    channel.basic_publish(
                        exchange="",
                        routing_key=dlq_name,
                        body=json.dumps(
                            {
                                "reason": "workflow_failed_after_retries",
                                "workflow_id": workflow_id,
                                "workflow_envelope": envelope,
                                "workflow_result": execution_result,
                            }
                        ),
                        properties=pika.BasicProperties(
                            content_type="application/json",
                            delivery_mode=2,
                        ),
                    )

                channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                return {
                    "status": "error",
                    "queue_name": target_queue,
                    "has_message": True,
                    "workflow_id": workflow_id,
                    "executed": False,
                    "retried": False,
                    "dead_lettered": bool(dlq_name),
                    "retry_count": envelope["retry_count"],
                    "max_retries": retry_limit,
                    "workflow_result": execution_result,
                    "message": "Workflow execution failed and retry limit reached",
                }
        except ValueError:
            raise
        except (pika.exceptions.AMQPError, OSError) as exc:
            hint = self._connection_troubleshooting_hint(exc)
            raise ValueError(
                f"Failed to consume and execute workflow from RabbitMQ queue: {exc}. {hint}"
            ) from exc
        finally:
            if connection is not None and connection.is_open:
                connection.close()

    def start_consuming_workflows(
        self,
        queue_name: str | dict[str, Any],
        max_messages: int = 0,
        stop_after_seconds: float = 30.0,
        poll_interval_seconds: float = 1.0,
        declare_queue: bool = False,
        durable: bool = True,
        max_retries: int = 3,
        dead_letter_queue: str | None = None,
        prefetch_count: int = 1,
    ) -> dict[str, Any]:
        """Continuously consume and execute workflow messages with bounded stop controls.

        Supports either positional args or a single options object as the first arg.
        """
        if isinstance(queue_name, dict):
            options = queue_name
            queue_name = options.get("queue_name", "")
            max_messages = options.get("max_messages", max_messages)
            stop_after_seconds = options.get("stop_after_seconds", stop_after_seconds)
            poll_interval_seconds = options.get("poll_interval_seconds", poll_interval_seconds)
            declare_queue = options.get("declare_queue", declare_queue)
            durable = options.get("durable", durable)
            max_retries = options.get("max_retries", max_retries)
            dead_letter_queue = options.get("dead_letter_queue", dead_letter_queue)
            prefetch_count = options.get("prefetch_count", prefetch_count)

        if not isinstance(queue_name, str) or not queue_name.strip():
            raise ValueError("queue_name must be a non-empty string")
        if not isinstance(max_messages, int) or max_messages < 0:
            raise ValueError("max_messages must be a non-negative integer")
        if not isinstance(stop_after_seconds, (int, float)) or stop_after_seconds <= 0:
            raise ValueError("stop_after_seconds must be a positive number")
        if not isinstance(poll_interval_seconds, (int, float)) or poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be a positive number")
        if not isinstance(declare_queue, bool):
            raise ValueError("declare_queue must be a boolean")
        if not isinstance(durable, bool):
            raise ValueError("durable must be a boolean")
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if dead_letter_queue is not None and (not isinstance(dead_letter_queue, str) or not dead_letter_queue.strip()):
            raise ValueError("dead_letter_queue must be a non-empty string when provided")
        if not isinstance(prefetch_count, int) or prefetch_count <= 0:
            raise ValueError("prefetch_count must be a positive integer")
        if max_messages == 0 and stop_after_seconds <= 0:
            raise ValueError("At least one stop condition is required")

        target_queue = queue_name.strip()
        dlq_name = dead_letter_queue.strip() if isinstance(dead_letter_queue, str) else None
        started_at = time.monotonic()

        processed_messages = 0
        executed_count = 0
        duplicate_count = 0
        retried_count = 0
        dead_lettered_count = 0
        invalid_count = 0
        errors_count = 0
        results: list[dict[str, Any]] = []

        connection: pika.BlockingConnection | None = None
        channel: Any = None

        try:
            connection = self._create_connection()
            channel = connection.channel()
            channel.basic_qos(prefetch_count=prefetch_count)

            if declare_queue:
                channel.queue_declare(queue=target_queue, durable=durable)
                if dlq_name:
                    channel.queue_declare(queue=dlq_name, durable=durable)

            for method_frame, _header_frame, body in channel.consume(
                queue=target_queue,
                inactivity_timeout=float(poll_interval_seconds),
                auto_ack=False,
            ):
                elapsed = time.monotonic() - started_at
                if elapsed >= float(stop_after_seconds):
                    break

                if method_frame is None:
                    continue

                processed_messages += 1
                item_result: dict[str, Any] = {
                    "delivery_tag": method_frame.delivery_tag,
                    "processed": False,
                }

                body_text = body.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(body_text)
                except (TypeError, ValueError):
                    invalid_count += 1
                    item_result.update({
                        "processed": True,
                        "status": "error",
                        "reason": "invalid_json",
                    })
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    if dlq_name:
                        dead_lettered_count += 1
                        channel.basic_publish(
                            exchange="",
                            routing_key=dlq_name,
                            body=json.dumps({"reason": "invalid_json", "original_body": body_text}),
                            properties=pika.BasicProperties(
                                content_type="application/json",
                                delivery_mode=2,
                            ),
                        )
                    results.append(item_result)
                    if max_messages > 0 and processed_messages >= max_messages:
                        break
                    continue

                if not isinstance(payload, dict):
                    invalid_count += 1
                    item_result.update({
                        "processed": True,
                        "status": "error",
                        "reason": "invalid_payload_type",
                    })
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    results.append(item_result)
                    if max_messages > 0 and processed_messages >= max_messages:
                        break
                    continue

                default_workflow_id = f"wf-{method_frame.delivery_tag}-{int(time.time() * 1000)}"
                envelope = self._normalize_workflow_envelope(payload, default_workflow_id)
                workflow_id = envelope["workflow_id"]
                item_result["workflow_id"] = workflow_id

                if workflow_id in self._processed_workflow_ids:
                    duplicate_count += 1
                    item_result.update({
                        "processed": True,
                        "status": "success",
                        "duplicate": True,
                    })
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    results.append(item_result)
                    if max_messages > 0 and processed_messages >= max_messages:
                        break
                    continue

                execution_result = self._execute_workflow_payload(envelope["workflow"])
                if execution_result.get("status") == "success":
                    executed_count += 1
                    self._processed_workflow_ids.add(workflow_id)
                    item_result.update(
                        {
                            "processed": True,
                            "status": "success",
                            "executed": True,
                            "retry_count": envelope["retry_count"],
                            "workflow_result": execution_result,
                        }
                    )
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    results.append(item_result)
                    if max_messages > 0 and processed_messages >= max_messages:
                        break
                    continue

                errors_count += 1
                next_retry_count = envelope["retry_count"] + 1
                retry_limit = min(max_retries, envelope["max_retries"])

                if next_retry_count <= retry_limit:
                    retried_count += 1
                    retry_envelope = dict(envelope)
                    retry_envelope["retry_count"] = next_retry_count
                    channel.basic_publish(
                        exchange="",
                        routing_key=target_queue,
                        body=json.dumps(retry_envelope),
                        properties=pika.BasicProperties(
                            content_type="application/json",
                            delivery_mode=2,
                        ),
                    )
                    item_result.update(
                        {
                            "processed": True,
                            "status": "error",
                            "executed": False,
                            "retried": True,
                            "retry_count": next_retry_count,
                            "max_retries": retry_limit,
                            "workflow_result": execution_result,
                        }
                    )
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    results.append(item_result)
                    if max_messages > 0 and processed_messages >= max_messages:
                        break
                    continue

                if dlq_name:
                    dead_lettered_count += 1
                    channel.basic_publish(
                        exchange="",
                        routing_key=dlq_name,
                        body=json.dumps(
                            {
                                "reason": "workflow_failed_after_retries",
                                "workflow_id": workflow_id,
                                "workflow_envelope": envelope,
                                "workflow_result": execution_result,
                            }
                        ),
                        properties=pika.BasicProperties(
                            content_type="application/json",
                            delivery_mode=2,
                        ),
                    )

                item_result.update(
                    {
                        "processed": True,
                        "status": "error",
                        "executed": False,
                        "retried": False,
                        "dead_lettered": bool(dlq_name),
                        "retry_count": envelope["retry_count"],
                        "max_retries": retry_limit,
                        "workflow_result": execution_result,
                    }
                )
                channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                results.append(item_result)

                if max_messages > 0 and processed_messages >= max_messages:
                    break

            channel.cancel()

            return {
                "status": "success",
                "queue_name": target_queue,
                "runtime_seconds": round(time.monotonic() - started_at, 3),
                "processed_messages": processed_messages,
                "executed_count": executed_count,
                "duplicate_count": duplicate_count,
                "retried_count": retried_count,
                "dead_lettered_count": dead_lettered_count,
                "invalid_count": invalid_count,
                "errors_count": errors_count,
                "results": results,
                "message": "Consumer loop finished",
            }
        except ValueError:
            raise
        except (pika.exceptions.AMQPError, OSError) as exc:
            hint = self._connection_troubleshooting_hint(exc)
            raise ValueError(
                f"Failed to start consuming workflows from RabbitMQ queue: {exc}. {hint}"
            ) from exc
        finally:
            if channel is not None:
                try:
                    channel.cancel()
                except Exception:
                    pass
            if connection is not None and connection.is_open:
                connection.close()
