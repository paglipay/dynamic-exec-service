"""RabbitMQ integration plugin using pika."""

from __future__ import annotations

import json
import time
from typing import Any

import pika


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
        queue_name: str,
        message: Any,
        durable: bool = True,
        persistent: bool = True,
    ) -> dict[str, Any]:
        """Publish a message to a queue and return publish metadata."""
        if not isinstance(queue_name, str) or not queue_name.strip():
            raise ValueError("queue_name must be a non-empty string")
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

    def subscribe(
        self,
        queue_name: str,
        timeout_seconds: float = 5.0,
        ack_message: bool = True,
        poll_interval_seconds: float = 0.2,
        declare_queue: bool = False,
        durable: bool = True,
    ) -> dict[str, Any]:
        """Read one message from a queue, waiting up to timeout_seconds."""
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
