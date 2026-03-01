"""Gmail integration plugin for listing and sending email via Gmail API."""

from __future__ import annotations

import base64
from email.message import EmailMessage
import mimetypes
import os
from pathlib import Path
from typing import Any


class GmailPlugin:
    """Access Gmail messages and send email using OAuth credentials."""

    DEFAULT_SCOPES: tuple[str, ...] = (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    )
    MAX_ATTACHMENT_COUNT = 10
    MAX_ATTACHMENT_TOTAL_BYTES = 20 * 1024 * 1024

    def __init__(
        self,
        credentials_path: str | None = None,
        token_path: str | None = None,
        user_id: str = "me",
        scopes: list[str] | None = None,
        service: Any | None = None,
    ) -> None:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")

        self.user_id = user_id.strip()
        self.scopes = self._resolve_scopes(scopes)

        if service is not None:
            self.service = service
            self.credentials_path = credentials_path
            self.token_path = token_path
            return

        self.credentials_path = self._resolve_credentials_path(credentials_path)
        self.token_path = self._resolve_token_path(token_path)
        self.service = self._build_service()

    def _resolve_scopes(self, scopes: list[str] | None) -> list[str]:
        if scopes is None:
            return list(self.DEFAULT_SCOPES)
        if not isinstance(scopes, list) or not scopes:
            raise ValueError("scopes must be a non-empty list of strings")

        resolved_scopes: list[str] = []
        for scope in scopes:
            if not isinstance(scope, str) or not scope.strip():
                raise ValueError("scopes must contain only non-empty strings")
            resolved_scopes.append(scope.strip())
        return resolved_scopes

    def _resolve_credentials_path(self, credentials_path: str | None) -> str:
        resolved = credentials_path or os.getenv("GMAIL_CREDENTIALS_PATH") or "credentials.json"
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError("credentials_path must be a non-empty string")
        return resolved.strip()

    def _resolve_token_path(self, token_path: str | None) -> str:
        resolved = token_path or os.getenv("GMAIL_TOKEN_PATH") or "gmail_token.json"
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError("token_path must be a non-empty string")
        return resolved.strip()

    def _import_google_dependencies(self) -> tuple[Any, Any, Any, Any, Any]:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            return Request, Credentials, InstalledAppFlow, build, HttpError
        except Exception as exc:
            raise ValueError(
                "Gmail dependencies are not installed. Install google-api-python-client, "
                "google-auth, and google-auth-oauthlib."
            ) from exc

    def _load_credentials(self) -> Any:
        Request, Credentials, InstalledAppFlow, _, _ = self._import_google_dependencies()

        creds = None
        token_file = Path(self.token_path)
        if token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_file), self.scopes)
            except Exception as exc:
                raise ValueError("token_path exists but is not a valid Gmail OAuth token file") from exc

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise ValueError("Failed to refresh Gmail OAuth token") from exc
        else:
            credentials_file = Path(self.credentials_path)
            if not credentials_file.exists() or not credentials_file.is_file():
                raise ValueError(
                    "No valid Gmail token found. Provide credentials_path (or GMAIL_CREDENTIALS_PATH) "
                    "to run OAuth consent and generate a token."
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), self.scopes)
                creds = flow.run_local_server(port=0)
            except Exception as exc:
                raise ValueError("Failed to complete Gmail OAuth flow") from exc

        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def _build_service(self) -> Any:
        _, _, _, build, _ = self._import_google_dependencies()
        credentials = self._load_credentials()

        try:
            return build("gmail", "v1", credentials=credentials, cache_discovery=False)
        except Exception as exc:
            raise ValueError(f"Failed to initialize Gmail service: {exc}") from exc

    def get_profile(self) -> dict[str, Any]:
        """Get profile metadata for the authenticated Gmail account."""
        _, _, _, _, HttpError = self._import_google_dependencies()
        try:
            result = self.service.users().getProfile(userId=self.user_id).execute()
        except HttpError as exc:
            raise ValueError(f"Gmail API profile request failed: {exc}") from exc

        return {
            "status": "success",
            "email_address": result.get("emailAddress"),
            "messages_total": result.get("messagesTotal"),
            "threads_total": result.get("threadsTotal"),
            "history_id": result.get("historyId"),
        }

    def list_messages(
        self,
        query: str = "",
        max_results: int = 20,
        label_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """List recent Gmail messages and return concise metadata."""
        _, _, _, _, HttpError = self._import_google_dependencies()

        if not isinstance(query, str):
            raise ValueError("query must be a string")
        if not isinstance(max_results, int) or max_results < 1 or max_results > 100:
            raise ValueError("max_results must be an integer between 1 and 100")
        if label_ids is not None:
            if not isinstance(label_ids, list):
                raise ValueError("label_ids must be a list of strings when provided")
            for label in label_ids:
                if not isinstance(label, str) or not label.strip():
                    raise ValueError("label_ids must contain only non-empty strings")

        payload: dict[str, Any] = {
            "userId": self.user_id,
            "q": query.strip(),
            "maxResults": max_results,
        }
        if label_ids:
            payload["labelIds"] = [value.strip() for value in label_ids]

        try:
            listed = self.service.users().messages().list(**payload).execute()
        except HttpError as exc:
            raise ValueError(f"Gmail API list request failed: {exc}") from exc

        messages = listed.get("messages", [])
        if not isinstance(messages, list):
            messages = []

        summaries: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            message_id = item.get("id")
            if not isinstance(message_id, str) or not message_id.strip():
                continue

            try:
                details = self.service.users().messages().get(
                    userId=self.user_id,
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                ).execute()
            except HttpError:
                continue

            payload_data = details.get("payload")
            headers: dict[str, str] = {}
            if isinstance(payload_data, dict):
                raw_headers = payload_data.get("headers")
                if isinstance(raw_headers, list):
                    for header in raw_headers:
                        if not isinstance(header, dict):
                            continue
                        name = header.get("name")
                        value = header.get("value")
                        if isinstance(name, str) and isinstance(value, str):
                            headers[name.lower()] = value

            summaries.append(
                {
                    "id": details.get("id", message_id),
                    "thread_id": details.get("threadId"),
                    "snippet": details.get("snippet"),
                    "from": headers.get("from"),
                    "to": headers.get("to"),
                    "subject": headers.get("subject"),
                    "date": headers.get("date"),
                    "internal_date": details.get("internalDate"),
                }
            )

        return {
            "status": "success",
            "query": query.strip(),
            "count": len(summaries),
            "messages": summaries,
            "next_page_token": listed.get("nextPageToken"),
            "result_size_estimate": listed.get("resultSizeEstimate"),
        }

    def get_message(
        self,
        message_id: str,
        format: str = "metadata",
        metadata_headers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one Gmail message in metadata, minimal, or full format."""
        _, _, _, _, HttpError = self._import_google_dependencies()

        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("message_id must be a non-empty string")
        if not isinstance(format, str) or not format.strip():
            raise ValueError("format must be a non-empty string")

        allowed_formats = {"metadata", "minimal", "full"}
        requested_format = format.strip().lower()
        if requested_format not in allowed_formats:
            raise ValueError("format must be one of: metadata, minimal, full")

        headers = metadata_headers or ["From", "To", "Subject", "Date"]
        if not isinstance(headers, list) or not headers:
            raise ValueError("metadata_headers must be a non-empty list of strings")
        for header in headers:
            if not isinstance(header, str) or not header.strip():
                raise ValueError("metadata_headers must contain only non-empty strings")

        request_kwargs: dict[str, Any] = {
            "userId": self.user_id,
            "id": message_id.strip(),
            "format": requested_format,
        }
        if requested_format == "metadata":
            request_kwargs["metadataHeaders"] = [header.strip() for header in headers]

        try:
            message = self.service.users().messages().get(**request_kwargs).execute()
        except HttpError as exc:
            raise ValueError(f"Gmail API get message request failed: {exc}") from exc

        result: dict[str, Any] = {
            "status": "success",
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            "label_ids": message.get("labelIds"),
            "snippet": message.get("snippet"),
            "internal_date": message.get("internalDate"),
            "size_estimate": message.get("sizeEstimate"),
            "format": requested_format,
        }

        if requested_format in {"metadata", "full"}:
            payload_data = message.get("payload")
            if isinstance(payload_data, dict):
                result["payload"] = payload_data

        return result

    def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        cc: str | list[str] | None = None,
        bcc: str | list[str] | None = None,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send an email from the authenticated Gmail account."""
        _, _, _, _, HttpError = self._import_google_dependencies()

        if not isinstance(to, str) or not to.strip():
            raise ValueError("to must be a non-empty string")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("subject must be a non-empty string")
        if not isinstance(body_text, str) or not body_text.strip():
            raise ValueError("body_text must be a non-empty string")

        message = EmailMessage()
        message["To"] = to.strip()
        message["Subject"] = subject.strip()

        cc_value = self._normalize_email_field(cc, field_name="cc")
        bcc_value = self._normalize_email_field(bcc, field_name="bcc")
        if cc_value:
            message["Cc"] = cc_value
        if bcc_value:
            message["Bcc"] = bcc_value

        message.set_content(body_text)
        attached_files = self._prepare_attachments(attachments)
        for attached_file in attached_files:
            message.add_attachment(
                attached_file["content"],
                maintype=attached_file["maintype"],
                subtype=attached_file["subtype"],
                filename=attached_file["filename"],
            )

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        try:
            result = self.service.users().messages().send(
                userId=self.user_id,
                body={"raw": encoded_message},
            ).execute()
        except HttpError as exc:
            raise ValueError(f"Gmail API send request failed: {exc}") from exc

        return {
            "status": "success",
            "message": "Email sent via Gmail",
            "id": result.get("id"),
            "thread_id": result.get("threadId"),
            "label_ids": result.get("labelIds"),
            "attachment_count": len(attached_files),
        }

    def _prepare_attachments(self, attachments: list[str] | None) -> list[dict[str, Any]]:
        if attachments is None:
            return []
        if not isinstance(attachments, list):
            raise ValueError("attachments must be a list of file paths when provided")
        if len(attachments) > self.MAX_ATTACHMENT_COUNT:
            raise ValueError(
                f"attachments may include at most {self.MAX_ATTACHMENT_COUNT} files"
            )

        loaded_files: list[dict[str, Any]] = []
        total_bytes = 0

        for item in attachments:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("attachments must contain only non-empty file paths")

            file_path = Path(item.strip())
            if not file_path.exists() or not file_path.is_file():
                raise ValueError(f"attachment file not found: {item}")

            file_bytes = file_path.read_bytes()
            if not file_bytes:
                raise ValueError(f"attachment file is empty: {file_path.name}")

            total_bytes += len(file_bytes)
            if total_bytes > self.MAX_ATTACHMENT_TOTAL_BYTES:
                raise ValueError(
                    "total attachment size exceeds 20MB limit"
                )

            guessed_mime, _ = mimetypes.guess_type(file_path.name)
            if not isinstance(guessed_mime, str) or "/" not in guessed_mime:
                guessed_mime = "application/octet-stream"
            maintype, subtype = guessed_mime.split("/", 1)

            loaded_files.append(
                {
                    "filename": file_path.name,
                    "maintype": maintype,
                    "subtype": subtype,
                    "content": file_bytes,
                }
            )

        return loaded_files

    def _normalize_email_field(
        self,
        value: str | list[str] | None,
        field_name: str,
    ) -> str | None:
        if value is None:
            return None

        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{field_name} must be a non-empty string when provided")
            return normalized

        if isinstance(value, list):
            recipients: list[str] = []
            for recipient in value:
                if not isinstance(recipient, str) or not recipient.strip():
                    raise ValueError(f"{field_name} must contain only non-empty strings")
                recipients.append(recipient.strip())
            if not recipients:
                raise ValueError(f"{field_name} must contain at least one recipient")
            return ", ".join(recipients)

        raise ValueError(f"{field_name} must be a string or list of strings when provided")
