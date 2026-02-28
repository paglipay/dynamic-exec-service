"""Paramiko-based SSH plugin module."""

from __future__ import annotations

import shlex
from typing import Any

import paramiko


class SSHModule:
    """Plugin that executes SSH commands against a remote host."""

    def __init__(
        self,
        hostname: str,
        username: str,
        password: str | None = None,
        key_filename: str | None = None,
    ) -> None:
        if not hostname or not username:
            raise ValueError("hostname and username are required")
        if password is None and key_filename is None:
            raise ValueError("Either password or key_filename must be provided")

        self.hostname = hostname
        self.username = username
        self.password = password
        self.key_filename = key_filename

    def _connect(self) -> paramiko.SSHClient:
        """Create and return a connected Paramiko SSH client."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.hostname,
            username=self.username,
            password=self.password,
            key_filename=self.key_filename,
            timeout=15,
        )
        return client

    def run_command(self, command: str) -> dict[str, Any]:
        """Run a command on the remote host and return stdout/stderr and exit code."""
        if not command or not isinstance(command, str):
            raise ValueError("command must be a non-empty string")

        client = self._connect()
        try:
            _, stdout, stderr = client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            return {
                "stdout": stdout.read().decode("utf-8", errors="replace"),
                "stderr": stderr.read().decode("utf-8", errors="replace"),
                "exit_status": exit_status,
            }
        finally:
            client.close()

    def list_directory(self, path: str) -> dict[str, Any]:
        """List directory contents via a safe shell-quoted command."""
        if not path or not isinstance(path, str):
            raise ValueError("path must be a non-empty string")
        safe_path = shlex.quote(path)
        return self.run_command(f"ls -la {safe_path}")
