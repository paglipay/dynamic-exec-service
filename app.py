"""Flask entrypoint for the dynamic execution service."""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror, request as urlrequest
from urllib.parse import urljoin

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename
from slackeventsapi import SlackEventAdapter
try:
    import redis  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    redis = None

try:
    from pypdf import PdfReader  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None  # type: ignore[assignment]

try:
    from docx import Document as DocxDocument  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    DocxDocument = None  # type: ignore[assignment]

try:
    import fitz  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore[assignment]

try:
    from PIL import Image as _PILImage  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    _PILImage = None  # type: ignore[assignment]

from executor.engine import JSONExecutor
from executor.permissions import validate_request
from flask_cors import CORS




app = Flask(__name__)
env_path = Path(".") / ".env"
load_dotenv(dotenv_path=env_path)
signing_secret = os.getenv("SIGNING_SECRET")

# Allow cross-origin requests. Configure CORS_ALLOWED_ORIGINS in .env to
# restrict to specific origins (comma-separated). Defaults to "*" so the
# app works without extra config, but you can lock it down in production.
_cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "*").strip()
_cors_origins: list[str] | str = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env != "*"
    else "*"
)
CORS(app, origins=_cors_origins, supports_credentials=True)

# --- DEBUG WRAPPER FOR /slack/events ---
from flask import Response
@app.before_request
def log_slack_events():
    if request.path == "/slack/events":
        if not request.data or not request.get_data(as_text=True).strip():
            return Response("Missing or empty request body", status=400)
        content_type = request.headers.get("Content-Type", "")
        if "application/x-www-form-urlencoded" in content_type:
            from urllib.parse import parse_qs, unquote_plus
            form = parse_qs(request.get_data(as_text=True))
            payload_raw = form.get("payload", [None])[0]
            if not payload_raw:
                return Response("Missing payload field", status=400)
            payload_json = unquote_plus(payload_raw)
            try:
                parsed_payload = json.loads(payload_json)
            except Exception:
                return Response("Invalid payload JSON", status=400)
            return Response("OK", status=200)
        else:
            try:
                body = json.loads(request.get_data(as_text=True))
            except Exception:
                return Response("Invalid JSON", status=400)

        if isinstance(body, dict) and body.get("type") == "url_verification":
            return jsonify({"challenge": body.get("challenge", "")})

slack_event_adapter: SlackEventAdapter | None = None
if signing_secret and signing_secret.strip():
    slack_event_adapter = SlackEventAdapter(
        signing_secret.strip(), "/slack/events", app
    )
else:
    app.logger.error(
        "SIGNING_SECRET is not set or empty — Slack event subscriptions are DISABLED. "
        "Set SIGNING_SECRET in your .env to enable Slack event handling."
    )

_slack_bot_token_startup = os.getenv("SLACK_BOT_TOKEN", "").strip()
if not _slack_bot_token_startup:
    app.logger.error(
        "SLACK_BOT_TOKEN is not set or empty — Slack file downloads and message posting will "
        "be DISABLED. Set SLACK_BOT_TOKEN in your .env."
    )
executor = JSONExecutor()
WORKFLOW_REF_PATTERN = re.compile(r"^\$\{steps\.([^\.]+)\.result(?:\.(.+))?\}$")
SLACK_EVENT_TTL_SECONDS = 300
SLACK_MAX_IMAGE_BYTES = 5 * 1024 * 1024
SLACK_MAX_IMAGE_COUNT = 3
try:
    SLACK_IMAGE_MAX_LONG_EDGE = int(os.getenv("SLACK_IMAGE_MAX_LONG_EDGE", "1024"))
except ValueError:
    SLACK_IMAGE_MAX_LONG_EDGE = 1024
SLACK_MAX_PDF_BYTES = 15 * 1024 * 1024
SLACK_MAX_PDF_TEXT_CHARS = 20000
SLACK_MAX_PDF_IMAGE_PAGES = 3
SLACK_MAX_EXCEL_BYTES = 15 * 1024 * 1024
SLACK_MAX_EXCEL_PREVIEW_ROWS = 5
SLACK_MAX_DOCX_BYTES = 15 * 1024 * 1024
SLACK_MAX_DOCX_TEXT_CHARS = 20000
BASE_DATA_DIR = os.getenv("BASE_DATA_DIR", "generated_data")
SLACK_IMAGE_SAVE_BASE_DIR = os.getenv("SLACK_IMAGE_SAVE_BASE_DIR", BASE_DATA_DIR)
SLACK_UNREADABLE_PREVIEW_TEXTS = {
    "[no preview available]",
    "no preview available",
}
_processed_slack_events: dict[str, float] = {}
_processed_slack_events_lock = threading.Lock()
SLACK_EVENT_REDIS_PREFIX = "slack:event:dedupe"
SLACK_INTERACTIVITY_TTL_SECONDS = 86400
SLACK_MAX_STORED_FORM_SUBMISSIONS = 200
_slack_form_submissions: list[dict[str, Any]] = []
_slack_form_submissions_lock = threading.Lock()

# --- File Storage Configuration ---
# Set BASE_DATA_DIR to set the shared root for all file storage (default: "generated_data").
# Set FILE_UPLOAD_API_KEY in .env to require authentication for upload/download/list/delete.
# Set MEDIA_STORAGE_DIR to override where uploads are saved (defaults to BASE_DATA_DIR / generated_data).
# Set FILE_MAX_UPLOAD_MB to change the per-request size cap (default 500 MB).
# Set SLACK_NETWORK_CHANNEL to the channel name/ID where upload notifications are posted.
MEDIA_STORAGE_DIR = os.getenv("MEDIA_STORAGE_DIR", BASE_DATA_DIR)
SLACK_NETWORK_CHANNEL = os.getenv("SLACK_NETWORK_CHANNEL", "#general")
FILE_UPLOAD_API_KEY = os.getenv("FILE_UPLOAD_API_KEY", "").strip()
try:
    FILE_MAX_UPLOAD_MB = max(1, int(os.getenv("FILE_MAX_UPLOAD_MB", "500")))
except ValueError:
    FILE_MAX_UPLOAD_MB = 500
FILE_MAX_UPLOAD_BYTES = FILE_MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_FILE_EXTENSIONS: frozenset[str] = frozenset({
    # Images
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff",
    # Videos
    "mp4", "mov", "avi", "mkv", "webm", "m4v", "wmv", "flv",
    # Documents / data
    "pdf", "docx", "xlsx", "csv", "txt",
})
_MEDIA_STORAGE_PATH = Path(MEDIA_STORAGE_DIR).resolve()
_MEDIA_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
app.config.setdefault("MAX_CONTENT_LENGTH", FILE_MAX_UPLOAD_BYTES)

from plugins.system_tools.media_storage_plugin import MediaStoragePlugin  # noqa: E402
_media_storage_plugin = MediaStoragePlugin(base_dir=MEDIA_STORAGE_DIR)

from plugins.system_tools.file_reader_plugin import FileReaderPlugin  # noqa: E402
_file_reader_plugin = FileReaderPlugin(base_dir=SLACK_IMAGE_SAVE_BASE_DIR)



def _build_redis_client() -> Any | None:
    """Build Redis client from REDIS_URL when available."""
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url or redis is None:
        return None

    try:
        return redis.from_url(redis_url, decode_responses=True)
    except Exception:
        return None


_slack_dedupe_redis_client = _build_redis_client()


def _verify_slack_signed_request(req: Any, signing_secret_value: str | None) -> bool:
    """Verify Slack request signature for events and interactive payloads."""
    if not isinstance(signing_secret_value, str) or not signing_secret_value.strip():
        return False

    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    slack_signature = req.headers.get("X-Slack-Signature", "")
    if not isinstance(timestamp, str) or not timestamp.strip():
        return False
    if not isinstance(slack_signature, str) or not slack_signature.strip():
        return False

    try:
        request_ts = int(timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - request_ts) > 60 * 5:
        return False

    request_body = req.get_data(cache=True, as_text=False)
    base_string = b"v0:" + timestamp.encode("utf-8") + b":" + request_body
    expected_signature = "v0=" + hmac.new(
        signing_secret_value.strip().encode("utf-8"),
        base_string,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, slack_signature)


def _store_slack_form_submission(submission: dict[str, Any]) -> None:
    """Store a bounded history of Slack form submissions in memory."""
    recorded_at = time.time()
    with _slack_form_submissions_lock:
        retained: list[dict[str, Any]] = []
        for item in _slack_form_submissions:
            created_at = item.get("received_at_epoch")
            if isinstance(created_at, (int, float)) and (recorded_at - float(created_at)) <= SLACK_INTERACTIVITY_TTL_SECONDS:
                retained.append(item)

        submission["received_at_epoch"] = recorded_at
        retained.append(submission)
        if len(retained) > SLACK_MAX_STORED_FORM_SUBMISSIONS:
            retained = retained[-SLACK_MAX_STORED_FORM_SUBMISSIONS:]

        _slack_form_submissions.clear()
        _slack_form_submissions.extend(retained)


def _get_recent_slack_form_submissions(limit: int = 25) -> list[dict[str, Any]]:
    """Return recent Slack form submissions, newest first."""
    normalized_limit = limit if isinstance(limit, int) and limit > 0 else 25
    with _slack_form_submissions_lock:
        items = list(_slack_form_submissions)
    items.sort(key=lambda item: float(item.get("received_at_epoch", 0.0)), reverse=True)
    return items[:normalized_limit]


def _is_duplicate_slack_event(event_data: dict[str, Any], event: dict[str, Any]) -> bool:
    """Return True when a Slack event appears to be a duplicate delivery."""
    event_id = event_data.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        event_id = "|".join(
            [
                str(event.get("channel", "")),
                str(event.get("user", "")),
                str(event.get("ts", "")),
                str(event.get("text", "")),
            ]
        )

    now = time.time()

    redis_client = _slack_dedupe_redis_client
    if redis_client is not None:
        redis_key = f"{SLACK_EVENT_REDIS_PREFIX}:{event_id}"
        try:
            was_recorded = redis_client.set(
                redis_key,
                str(now),
                ex=SLACK_EVENT_TTL_SECONDS,
                nx=True,
            )
            return not bool(was_recorded)
        except Exception as exc:
            app.logger.warning("Slack dedupe Redis check failed; falling back to memory: %s", exc)

    with _processed_slack_events_lock:
        expired = [
            key for key, seen_at in _processed_slack_events.items()
            if (now - seen_at) > SLACK_EVENT_TTL_SECONDS
        ]
        for key in expired:
            del _processed_slack_events[key]

        if event_id in _processed_slack_events:
            return True

        _processed_slack_events[event_id] = now
        return False


_SLACK_ALLOWED_REDIRECT_HOSTS = {
    "slack.com",
    "files.slack.com",
    "cdn.slack-edge.com",
    "a.slack-edge.com",
}


def _is_allowed_slack_redirect(url: str) -> bool:
    """Return True if the URL's host is a known Slack domain."""
    try:
        from urllib.parse import urlparse as _urlparse
        host = _urlparse(url).hostname or ""
        return host == "slack.com" or host.endswith(".slack.com") or host.endswith(".slack-edge.com")
    except Exception:
        return False


def _download_slack_text_file(url: str, bot_token: str, max_chars: int = 12000) -> str | None:
    """Download a Slack private text file and return a bounded UTF-8 string."""
    if not isinstance(url, str) or not url.strip():
        return None
    if not isinstance(bot_token, str) or not bot_token.strip():
        return None

    current_url = url.strip()
    auth_header = f"Bearer {bot_token.strip()}"
    for redirect_count in range(6):
        req = urlrequest.Request(
            current_url,
            headers={"Authorization": auth_header},
            method="GET",
        )
        try:
            app.logger.info("Attempting Slack file download from private URL")
            with urlrequest.urlopen(req, timeout=20) as response:
                status = getattr(response, "status", 200)
                content_type = str(response.headers.get("Content-Type", "")).lower()

                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        app.logger.warning("Slack file redirect missing Location header")
                        return None
                    resolved = urljoin(current_url, location)
                    if not _is_allowed_slack_redirect(resolved):
                        app.logger.warning(
                            "Slack file redirect to non-Slack domain blocked: %s", resolved
                        )
                        return None
                    current_url = resolved
                    app.logger.info("Following Slack file redirect to %s", current_url)
                    continue

                content = response.read().decode("utf-8", errors="replace")
                app.logger.info("Slack file download succeeded; bytes=%s content_type=%s", len(content), content_type)

                if "text/html" in content_type or "<!doctype html" in content.lower()[:500]:
                    app.logger.warning("Slack file download returned HTML instead of raw text file")
                    return None

                return content[:max_chars]
        except urlerror.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location") if exc.headers else None
                if location:
                    resolved = urljoin(current_url, location)
                    if not _is_allowed_slack_redirect(resolved):
                        app.logger.warning(
                            "Slack file redirect (via HTTPError) to non-Slack domain blocked: %s",
                            resolved,
                        )
                        return None
                    current_url = resolved
                    app.logger.info("Following Slack file redirect via HTTPError to %s", current_url)
                    continue
            app.logger.warning("Slack file download failed with HTTP %s: %s", exc.code, exc)
            return None
        except urlerror.URLError as exc:
            app.logger.warning("Slack file download failed: %s", exc)
            return None

        if redirect_count >= 5:
            break

    app.logger.warning("Slack file download exceeded redirect limit")
    return None


def _download_slack_binary_file(url: str, bot_token: str, max_bytes: int = SLACK_MAX_IMAGE_BYTES) -> tuple[bytes | None, str]:
    """Download a Slack private file as bytes with content type.

    Reads in 64 KB chunks so that oversized files are detected and discarded
    early without loading the entire payload into memory first.
    """
    if not isinstance(url, str) or not url.strip():
        return None, ""
    if not isinstance(bot_token, str) or not bot_token.strip():
        return None, ""
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        return None, ""

    _CHUNK = 65536  # 64 KB per read
    current_url = url.strip()
    auth_header = f"Bearer {bot_token.strip()}"
    for _ in range(6):
        req = urlrequest.Request(
            current_url,
            headers={"Authorization": auth_header},
            method="GET",
        )
        try:
            with urlrequest.urlopen(req, timeout=25) as response:
                status = getattr(response, "status", 200)
                content_type = str(response.headers.get("Content-Type", "")).lower().split(";")[0].strip()

                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return None, ""
                    resolved = urljoin(current_url, location)
                    if not _is_allowed_slack_redirect(resolved):
                        app.logger.warning(
                            "Slack binary file redirect to non-Slack domain blocked: %s", resolved
                        )
                        return None, ""
                    current_url = resolved
                    continue

                # Read in chunks to avoid spiking memory on large files.
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        app.logger.warning(
                            "Slack binary file exceeded max allowed size (%d bytes); aborting download",
                            max_bytes,
                        )
                        return None, ""
                    chunks.append(chunk)

                return b"".join(chunks), content_type
        except urlerror.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location") if exc.headers else None
                if location:
                    resolved = urljoin(current_url, location)
                    if not _is_allowed_slack_redirect(resolved):
                        app.logger.warning(
                            "Slack binary file redirect (via HTTPError) to non-Slack domain blocked: %s",
                            resolved,
                        )
                        return None, ""
                    current_url = resolved
                    continue
            app.logger.warning("Slack binary file download failed with HTTP %s: %s", exc.code, exc)
            return None, ""
        except urlerror.URLError as exc:
            app.logger.warning("Slack binary file download failed: %s", exc)
            return None, ""

    return None, ""


def _sanitize_slack_filename(name: str) -> str:
    """Return a filesystem-safe filename while preserving extension when possible."""
    candidate = Path(name).name.strip() if isinstance(name, str) else ""
    if not candidate:
        candidate = "image"

    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", candidate)
    sanitized = sanitized.strip("._")
    if not sanitized:
        sanitized = "image"
    return sanitized


def _guess_image_extension(content_type: str, fallback_name: str) -> str:
    """Choose a file extension based on content type or existing filename."""
    suffix = Path(fallback_name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return suffix

    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }
    return mapping.get(content_type.lower().strip(), ".png")


def _resize_image_for_vision(image_bytes: bytes, mime: str, max_long_edge: int = 1024) -> tuple[bytes, str]:
    """Resize image bytes so the long edge is at most max_long_edge pixels and encode as JPEG."""
    if _PILImage is None:
        return image_bytes, mime
    if not isinstance(image_bytes, bytes) or not image_bytes:
        return image_bytes, mime
    try:
        import io as _io
        img = _PILImage.open(_io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) > max_long_edge:
            scale = max_long_edge / max(w, h)
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            img = img.resize(new_size, _PILImage.LANCZOS)
        else:
            new_size = (w, h)
        img = img.convert("RGB")
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        resized = buf.getvalue()
        app.logger.info(
            "Image prepared for vision: %dx%d -> %dx%d, %d -> %d bytes (JPEG q75)",
            w, h, new_size[0], new_size[1], len(image_bytes), len(resized),
        )
        return resized, "image/jpeg"
    except Exception as exc:
        app.logger.warning("Image resize/convert failed, using original: %s", exc)
        return image_bytes, mime


def _save_slack_image_copy(
    binary_data: bytes,
    original_name: str,
    content_type: str,
    channel: str | None,
) -> str | None:
    """Save downloaded Slack image bytes under a flat slack_downloads directory."""
    if not isinstance(binary_data, bytes) or not binary_data:
        return None

    base_dir = Path(SLACK_IMAGE_SAVE_BASE_DIR).resolve()
    target_dir = (base_dir / "slack_downloads").resolve()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        app.logger.error("_save_slack_image_copy: cannot create directory %s: %s", target_dir, exc)
        return None

    safe_name = _sanitize_slack_filename(original_name)
    stem = Path(safe_name).stem or "image"
    extension = _guess_image_extension(content_type, safe_name)
    target_path = target_dir / f"{stem}{extension}"
    try:
        target_path.write_bytes(binary_data)
    except OSError as exc:
        app.logger.error("_save_slack_image_copy: failed to write %s: %s", target_path, exc)
        return None
    return str(target_path)


def _save_slack_pdf_copy(
    binary_data: bytes,
    original_name: str,
    channel: str | None,
) -> str | None:
    """Save downloaded Slack PDF bytes under a flat slack_downloads directory."""
    if not isinstance(binary_data, bytes) or not binary_data:
        return None

    base_dir = Path(SLACK_IMAGE_SAVE_BASE_DIR).resolve()
    target_dir = (base_dir / "slack_downloads").resolve()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        app.logger.error("_save_slack_pdf_copy: cannot create directory %s: %s", target_dir, exc)
        return None

    safe_name = _sanitize_slack_filename(original_name)
    stem = Path(safe_name).stem or "document"
    target_path = target_dir / f"{stem}.pdf"
    try:
        target_path.write_bytes(binary_data)
    except OSError as exc:
        app.logger.error("_save_slack_pdf_copy: failed to write %s: %s", target_path, exc)
        return None
    return str(target_path)


def _save_slack_docx_copy(
    binary_data: bytes,
    original_name: str,
    channel: str | None,
) -> str | None:
    """Save downloaded Slack DOCX bytes under a flat slack_downloads directory."""
    if not isinstance(binary_data, bytes) or not binary_data:
        return None

    base_dir = Path(SLACK_IMAGE_SAVE_BASE_DIR).resolve()
    target_dir = (base_dir / "slack_downloads").resolve()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        app.logger.error("_save_slack_docx_copy: cannot create directory %s: %s", target_dir, exc)
        return None

    safe_name = _sanitize_slack_filename(original_name)
    stem = Path(safe_name).stem or "document"
    target_path = target_dir / f"{stem}.docx"
    try:
        target_path.write_bytes(binary_data)
    except OSError as exc:
        app.logger.error("_save_slack_docx_copy: failed to write %s: %s", target_path, exc)
        return None
    return str(target_path)


def _save_slack_excel_copy(
    binary_data: bytes,
    original_name: str,
    channel: str | None,
) -> str | None:
    """Save downloaded Slack Excel bytes under a flat slack_downloads directory."""
    if not isinstance(binary_data, bytes) or not binary_data:
        return None

    base_dir = Path(SLACK_IMAGE_SAVE_BASE_DIR).resolve()
    target_dir = (base_dir / "slack_downloads").resolve()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        app.logger.error("_save_slack_excel_copy: cannot create directory %s: %s", target_dir, exc)
        return None

    safe_name = _sanitize_slack_filename(original_name)
    stem = Path(safe_name).stem or "workbook"
    extension = Path(safe_name).suffix.lower()
    if extension not in {".xlsx", ".xlsm", ".xls"}:
        extension = ".xlsx"

    target_path = target_dir / f"{stem}{extension}"
    try:
        target_path.write_bytes(binary_data)
    except OSError as exc:
        app.logger.error("_save_slack_excel_copy: failed to write %s: %s", target_path, exc)
        return None
    return str(target_path)


def _collect_slack_block_text(node: Any, chunks: list[str]) -> None:
    """Recursively collect textual snippets from Slack block structures."""
    if isinstance(node, str):
        text = node.strip()
        if text:
            chunks.append(text)
        return

    if isinstance(node, dict):
        for key in ("text", "fallback", "pretext", "title", "value"):
            text_value = node.get(key)
            if isinstance(text_value, str):
                text = text_value.strip()
                if text:
                    chunks.append(text)

        for key in ("elements", "fields", "blocks", "attachments"):
            child = node.get(key)
            if isinstance(child, list):
                _collect_slack_block_text(child, chunks)
        return

    if isinstance(node, list):
        for item in node:
            _collect_slack_block_text(item, chunks)


def _extract_slack_message_text(event: dict[str, Any], max_chars: int = 12000) -> str:
    """Extract message text from a Slack event, with rich-block fallback when text is empty."""
    message_obj = event.get("message")
    message_payload = message_obj if isinstance(message_obj, dict) else {}

    raw_text = event.get("text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()[:max_chars]

    nested_text = message_payload.get("text")
    if isinstance(nested_text, str) and nested_text.strip():
        return nested_text.strip()[:max_chars]

    chunks: list[str] = []
    _collect_slack_block_text(event.get("blocks", []), chunks)
    _collect_slack_block_text(message_payload.get("blocks", []), chunks)
    _collect_slack_block_text(event.get("attachments", []), chunks)
    _collect_slack_block_text(message_payload.get("attachments", []), chunks)
    if not chunks:
        return ""

    merged_text = "\n".join(chunks).strip()
    if not merged_text:
        return ""
    return merged_text[:max_chars]


def _is_unreadable_slack_preview_text(text: str) -> bool:
    """Return True when Slack only provides placeholder preview text."""
    normalized = text.strip().lower()
    return normalized in SLACK_UNREADABLE_PREVIEW_TEXTS


def _parse_tsv_rows(tsv_text: str, max_rows: int = 25) -> list[dict[str, str]]:
    """Parse TSV text into row dicts using the first row as headers."""
    if not isinstance(tsv_text, str) or not tsv_text.strip():
        return []

    lines = [line for line in tsv_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []

    reader = csv.reader(lines, delimiter="\t")
    try:
        raw_headers = next(reader)
    except StopIteration:
        return []

    headers = [str(item).strip() for item in raw_headers]
    if not headers or not any(headers):
        return []

    rows: list[dict[str, str]] = []
    for row in reader:
        if len(rows) >= max_rows:
            break

        values = [str(item).strip() for item in row]
        if not any(values):
            continue

        normalized_values = values[: len(headers)]
        if len(normalized_values) < len(headers):
            normalized_values.extend([""] * (len(headers) - len(normalized_values)))

        row_data: dict[str, str] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            row_data[header] = normalized_values[index]

        if row_data:
            rows.append(row_data)

    return rows


def _render_pdf_pages_to_image_data_urls(
    pdf_bytes: bytes,
    pdf_name: str,
    channel: str | None,
    max_images: int,
) -> tuple[list[str], list[str], int]:
    """Render selected PDF pages to PNG data URLs and save local copies."""
    if fitz is None:
        return [], [], 0
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        return [], [], 0
    if not isinstance(max_images, int) or max_images <= 0:
        return [], [], 0

    data_urls: list[str] = []
    saved_paths: list[str] = []
    rendered_count = 0
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        app.logger.warning("Failed to open PDF for page rendering: %s", exc)
        return [], [], 0

    try:
        pages_with_images: list[int] = []
        for page_index in range(len(document)):
            try:
                page = document.load_page(page_index)
                if page.get_images(full=True):
                    pages_with_images.append(page_index)
            except Exception:
                continue

        if pages_with_images:
            target_pages = pages_with_images[: min(max_images, SLACK_MAX_PDF_IMAGE_PAGES)]
        else:
            target_pages = list(range(min(len(document), min(max_images, SLACK_MAX_PDF_IMAGE_PAGES))))

        safe_stem = Path(_sanitize_slack_filename(pdf_name)).stem or "pdf"
        for page_index in target_pages:
            try:
                page = document.load_page(page_index)
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                png_bytes = pix.tobytes("png")
            except Exception as exc:
                app.logger.warning("Failed to render PDF page %s: %s", page_index + 1, exc)
                continue

            if len(png_bytes) > SLACK_MAX_IMAGE_BYTES:
                app.logger.warning("Rendered PDF page exceeded image size limit: page=%s", page_index + 1)
                continue

            encoded = base64.b64encode(png_bytes).decode("ascii")
            data_urls.append(f"data:image/png;base64,{encoded}")
            rendered_count += 1

            page_name = f"{safe_stem}_page_{page_index + 1}.png"
            try:
                saved_path = _save_slack_image_copy(png_bytes, page_name, "image/png", channel)
            except Exception as exc:
                app.logger.warning("Failed to save rendered PDF page image locally: %s", exc)
                saved_path = None
            if isinstance(saved_path, str) and saved_path:
                saved_paths.append(saved_path)
    finally:
        document.close()

    return data_urls, saved_paths, rendered_count


def _extract_exif_full(image_bytes: bytes, name: str) -> dict | None:
    """Extract full EXIF data from JPEG bytes into a JSON-serializable dict.

    Returns a dict keyed by IFD name (e.g. '0th', 'Exif', 'GPS', '1st')
    where each value is a dict of {str(tag_id): repr(value)}.
    Returns None if piexif is unavailable or parsing fails.
    """
    try:
        import piexif
        _exif = piexif.load(image_bytes)
        exif_full: dict = {}
        for _ifd, _tags in _exif.items():
            if isinstance(_tags, dict):
                exif_full[_ifd] = {str(k): repr(v) for k, v in _tags.items()}
        # Debug: print full EXIF to stdout immediately
        _exif_str = json.dumps(exif_full, indent=2, ensure_ascii=False)
        app.logger.debug("[EXIF ON RECEIVE] %s (%d bytes):\n%s", name, len(image_bytes), _exif_str)
        return exif_full
    except Exception as exc:
        app.logger.debug("[EXIF ON RECEIVE] Could not read EXIF for %s: %s", name, exc)
        return None


def _extract_gps_from_exif(image_bytes: bytes, name: str) -> dict | None:
    """Extract GPS lat/lon from JPEG bytes. Returns {'lat': float, 'lon': float} or None."""
    try:
        import piexif
        _exif = piexif.load(image_bytes)
        _gps = _exif.get("GPS", {})
        if not _gps:
            return None

        def _dms_to_dec(dms: tuple, ref: bytes) -> float | None:
            try:
                d = dms[0][0] / dms[0][1]
                m = dms[1][0] / dms[1][1]
                s = dms[2][0] / dms[2][1]
                val = d + m / 60 + s / 3600
                return -val if ref in (b"S", b"W") else val
            except Exception:
                return None

        _lat = _dms_to_dec(
            _gps.get(piexif.GPSIFD.GPSLatitude, ()),
            _gps.get(piexif.GPSIFD.GPSLatitudeRef, b"N"),
        )
        _lon = _dms_to_dec(
            _gps.get(piexif.GPSIFD.GPSLongitude, ()),
            _gps.get(piexif.GPSIFD.GPSLongitudeRef, b"E"),
        )
        if _lat is not None and _lon is not None:
            return {"lat": round(_lat, 7), "lon": round(_lon, 7)}
        return None
    except Exception as exc:
        app.logger.debug("GPS extraction failed for %s: %s", name, exc)
        return None


def _extract_slack_file_context(event: dict[str, Any], slack_bot_token: str | None) -> tuple[str, str, list[str], list[dict]]:
    """Build prompt/reply snippets and image data URLs from Slack files when available."""
    files = event.get("files")
    if not isinstance(files, list) or not files:
        nested_message = event.get("message")
        if isinstance(nested_message, dict):
            files = nested_message.get("files")
    if not isinstance(files, list) or not files:
        app.logger.info("No Slack files attached on this event")
        return "", "", [], []

    app.logger.info("Slack files detected: count=%s", len(files))

    file_lines: list[str] = []
    file_content_lines: list[str] = []
    image_data_urls: list[str] = []
    image_metadata: list[dict] = []
    saved_image_paths: list[str] = []
    saved_pdf_paths: list[str] = []
    saved_docx_paths: list[str] = []
    saved_excel_paths: list[str] = []
    reply_items: list[str] = []
    channel = event.get("channel")
    for file_item in files:
        if not isinstance(file_item, dict):
            app.logger.info("Skipping non-dict file item in Slack event")
            continue

        name = str(file_item.get("name") or "(unnamed)")
        filetype = str(file_item.get("filetype") or file_item.get("mimetype") or "unknown")
        mimetype = str(file_item.get("mimetype") or "")
        title = str(file_item.get("title") or "")
        url_private = str(file_item.get("url_private_download") or file_item.get("url_private") or "")

        details = f"name={name}; type={filetype}"
        if title:
            details += f"; title={title}"
        if url_private:
            details += f"; url_private={url_private}"
        file_lines.append(details)
        reply_items.append(name)

        is_text_candidate = (
            name.lower().endswith((".txt", ".md"))
            or filetype.lower() in {"text", "txt", "markdown", "md"}
            or mimetype.startswith("text/")
        )
        is_tsv_candidate = (
            name.lower().endswith(".tsv")
            or filetype.lower() in {"tsv"}
            or mimetype.lower() in {
                "text/tab-separated-values",
                "application/tab-separated-values",
                "text/tsv",
            }
        )
        if is_text_candidate and url_private and isinstance(slack_bot_token, str) and slack_bot_token.strip():
            file_text = _download_slack_text_file(url_private, slack_bot_token)
            if isinstance(file_text, str) and file_text.strip():
                app.logger.info("Attached text file content captured: %s", name)
                if is_tsv_candidate:
                    parsed_rows = _parse_tsv_rows(file_text)
                    if parsed_rows:
                        parsed_json = json.dumps(parsed_rows, ensure_ascii=True)
                        file_content_lines.append(
                            f"TSV '{name}' parsed rows (up to 25):\n{parsed_json}"
                        )
                        file_lines[-1] = f"{file_lines[-1]}; tsv_rows={len(parsed_rows)}"
                    else:
                        file_content_lines.append(f"TSV '{name}' raw content:\n{file_text.strip()}")
                else:
                    file_content_lines.append(f"File '{name}' content:\n{file_text.strip()}")
            else:
                app.logger.info("Attached text file could not be read: %s", name)
        else:
            app.logger.info(
                "Skipping file content fetch for %s (text_candidate=%s, has_url=%s, has_token=%s)",
                name,
                is_text_candidate,
                bool(url_private),
                bool(isinstance(slack_bot_token, str) and slack_bot_token.strip()),
            )

        is_pdf_candidate = (
            mimetype.lower() == "application/pdf"
            or filetype.lower() == "pdf"
            or name.lower().endswith(".pdf")
        )
        if is_pdf_candidate and url_private and isinstance(slack_bot_token, str) and slack_bot_token.strip():
            pdf_data, pdf_content_type = _download_slack_binary_file(
                url_private,
                slack_bot_token,
                max_bytes=SLACK_MAX_PDF_BYTES,
            )
            if isinstance(pdf_data, bytes) and pdf_data:
                try:
                    saved_pdf_path = _save_slack_pdf_copy(
                        pdf_data,
                        name,
                        channel if isinstance(channel, str) else None,
                    )
                except Exception as exc:
                    app.logger.warning("Failed to save Slack PDF locally (%s): %s", name, exc)
                    saved_pdf_path = None
                if isinstance(saved_pdf_path, str) and saved_pdf_path:
                    saved_pdf_paths.append(saved_pdf_path)
                    file_lines[-1] = f"{file_lines[-1]}; saved_pdf_as={saved_pdf_path}"

                extracted_pdf_text = ""
                if isinstance(saved_pdf_path, str) and saved_pdf_path:
                    try:
                        pdf_read_result = _file_reader_plugin.read_pdf_text(
                            saved_pdf_path, max_chars=SLACK_MAX_PDF_TEXT_CHARS
                        )
                        extracted_pdf_text = pdf_read_result.get("text", "")
                    except Exception as exc:
                        app.logger.warning("Failed to read PDF text via plugin (%s): %s", name, exc)
                rendered_pages = 0
                if extracted_pdf_text:
                    file_content_lines.append(f"PDF '{name}' extracted text:\n{extracted_pdf_text}")
                    app.logger.info("Attached PDF text extracted: %s chars=%s", name, len(extracted_pdf_text))
                    file_lines[-1] = f"{file_lines[-1]}; pdf_text_chars={len(extracted_pdf_text)}"

                remaining_image_slots = max(SLACK_MAX_IMAGE_COUNT - len(image_data_urls), 0)
                if remaining_image_slots > 0:
                    pdf_image_urls, pdf_saved_paths, rendered_pages = _render_pdf_pages_to_image_data_urls(
                        pdf_data,
                        name,
                        channel if isinstance(channel, str) else None,
                        remaining_image_slots,
                    )
                    if pdf_image_urls:
                        image_data_urls.extend(pdf_image_urls)
                        app.logger.info("Attached PDF rendered for OpenAI vision: %s pages=%s", name, rendered_pages)
                    if pdf_saved_paths:
                        saved_image_paths.extend(pdf_saved_paths)
                    if rendered_pages > 0:
                        file_lines[-1] = f"{file_lines[-1]}; pdf_rendered_pages={rendered_pages}"

                if not extracted_pdf_text and rendered_pages == 0:
                    file_lines[-1] = f"{file_lines[-1]}; pdf_processed=true"

                if pdf_content_type and pdf_content_type != "application/pdf":
                    file_lines[-1] = f"{file_lines[-1]}; detected_content_type={pdf_content_type}"
            else:
                app.logger.info("Attached PDF could not be downloaded: %s", name)

        is_docx_candidate = (
            mimetype.lower() == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or filetype.lower() in {"docx", "word"}
            or name.lower().endswith(".docx")
        )
        if is_docx_candidate and url_private and isinstance(slack_bot_token, str) and slack_bot_token.strip():
            docx_data, docx_content_type = _download_slack_binary_file(
                url_private,
                slack_bot_token,
                max_bytes=SLACK_MAX_DOCX_BYTES,
            )
            if isinstance(docx_data, bytes) and docx_data:
                try:
                    saved_docx_path = _save_slack_docx_copy(
                        docx_data,
                        name,
                        channel if isinstance(channel, str) else None,
                    )
                except Exception as exc:
                    app.logger.warning("Failed to save Slack DOCX locally (%s): %s", name, exc)
                    saved_docx_path = None
                if isinstance(saved_docx_path, str) and saved_docx_path:
                    saved_docx_paths.append(saved_docx_path)
                    file_lines[-1] = f"{file_lines[-1]}; saved_docx_as={saved_docx_path}"

                extracted_docx_text = ""
                if isinstance(saved_docx_path, str) and saved_docx_path:
                    try:
                        docx_read_result = _file_reader_plugin.read_docx_text(
                            saved_docx_path, max_chars=SLACK_MAX_DOCX_TEXT_CHARS
                        )
                        extracted_docx_text = docx_read_result.get("text", "")
                    except Exception as exc:
                        app.logger.warning("Failed to read DOCX text via plugin (%s): %s", name, exc)
                if extracted_docx_text:
                    file_content_lines.append(f"DOCX '{name}' extracted text:\n{extracted_docx_text}")
                    app.logger.info("Attached DOCX text extracted: %s chars=%s", name, len(extracted_docx_text))
                    file_lines[-1] = f"{file_lines[-1]}; docx_text_chars={len(extracted_docx_text)}"
                else:
                    file_lines[-1] = f"{file_lines[-1]}; docx_processed=true"

                if docx_content_type and docx_content_type != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                    file_lines[-1] = f"{file_lines[-1]}; detected_content_type={docx_content_type}"
            else:
                app.logger.info("Attached DOCX could not be downloaded: %s", name)

        is_excel_candidate = (
            name.lower().endswith((".xlsx", ".xlsm", ".xls"))
            or filetype.lower() in {"xlsx", "xlsm", "xls", "excel"}
            or mimetype.lower() in {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-excel.sheet.macroenabled.12",
                "application/vnd.ms-excel",
            }
        )
        if is_excel_candidate and url_private and isinstance(slack_bot_token, str) and slack_bot_token.strip():
            excel_data, excel_content_type = _download_slack_binary_file(
                url_private,
                slack_bot_token,
                max_bytes=SLACK_MAX_EXCEL_BYTES,
            )
            if isinstance(excel_data, bytes) and excel_data:
                try:
                    saved_excel_path = _save_slack_excel_copy(
                        excel_data,
                        name,
                        channel if isinstance(channel, str) else None,
                    )
                except Exception as exc:
                    app.logger.warning("Failed to save Slack Excel file locally (%s): %s", name, exc)
                    saved_excel_path = None

                if isinstance(saved_excel_path, str) and saved_excel_path:
                    saved_excel_paths.append(saved_excel_path)
                    file_lines[-1] = f"{file_lines[-1]}; saved_excel_as={saved_excel_path}"
                    excel_summary = None
                    try:
                        excel_summary = _file_reader_plugin.summarize_excel(
                            saved_excel_path, max_preview_rows=SLACK_MAX_EXCEL_PREVIEW_ROWS
                        )
                    except Exception as exc:
                        app.logger.warning("Failed to summarize Excel via plugin (%s): %s", saved_excel_path, exc)
                    if isinstance(excel_summary, dict) and excel_summary:
                        file_content_lines.append(
                            f"Excel '{name}' workbook summary:\n{json.dumps(excel_summary, ensure_ascii=True)}"
                        )
                        preview_block = excel_summary.get("first_sheet_preview")
                        if isinstance(preview_block, dict):
                            preview_count = preview_block.get("preview_row_count")
                            if isinstance(preview_count, int):
                                file_lines[-1] = f"{file_lines[-1]}; excel_preview_rows={preview_count}"
                        sheet_count = excel_summary.get("sheet_count")
                        if isinstance(sheet_count, int):
                            file_lines[-1] = f"{file_lines[-1]}; excel_sheet_count={sheet_count}"
                    else:
                        file_lines[-1] = f"{file_lines[-1]}; excel_processed=true"

                if excel_content_type and excel_content_type not in {
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "application/vnd.ms-excel.sheet.macroenabled.12",
                    "application/vnd.ms-excel",
                }:
                    file_lines[-1] = f"{file_lines[-1]}; detected_content_type={excel_content_type}"
            else:
                app.logger.info("Attached Excel file could not be downloaded: %s", name)

        is_image_candidate = (
            mimetype.lower().startswith("image/")
            or name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))
        )
        if (
            is_image_candidate
            and len(image_data_urls) < SLACK_MAX_IMAGE_COUNT
            and url_private
            and isinstance(slack_bot_token, str)
            and slack_bot_token.strip()
        ):
            binary_data, detected_content_type = _download_slack_binary_file(url_private, slack_bot_token)
            if isinstance(binary_data, bytes) and binary_data:
                mime = detected_content_type if detected_content_type.startswith("image/") else mimetype.lower()
                if not mime.startswith("image/"):
                    mime = "image/png"

                # --- Extract EXIF immediately from raw in-memory bytes (earliest possible moment) ---
                _image_exif_full: dict | None = None
                _image_gps: dict | None = None
                is_jpeg = (
                    name.lower().endswith((".jpg", ".jpeg"))
                    or mime in ("image/jpeg", "image/jpg")
                )
                if is_jpeg:
                    _image_exif_full = _extract_exif_full(binary_data, name)
                    _image_gps = _extract_gps_from_exif(binary_data, name)
                    if _image_gps:
                        gps_str = f"GPS coordinates embedded in image: lat={_image_gps['lat']}, lon={_image_gps['lon']}"
                        file_content_lines.append(f"[{name}] {gps_str}")
                        app.logger.info("GPS EXIF extracted from Slack image %s: %s", name, gps_str)
                # --- END EXIF extract ---

                # Use unified intake — saves file, extracts EXIF once, writes media_files record
                saved_path: str | None = None
                if isinstance(slack_bot_token, str) and slack_bot_token.strip():
                    try:
                        from plugins.integrations.slack_plugin import SlackPlugin as _SP
                        _sp_instance = _SP(bot_token=slack_bot_token)
                        _intake_result = _sp_instance.intake_media(
                            raw_bytes=binary_data,
                            filename=name,
                            base_dir=SLACK_IMAGE_SAVE_BASE_DIR,
                            channel=channel if isinstance(channel, str) else None,
                            url_private=url_private if isinstance(url_private, str) else None,
                            subfolder="slack_downloads",
                            source="slack_share",
                        )
                        saved_path = _intake_result.get("full_path")
                        # Prefer GPS from typed EXIF over the legacy extraction
                        if _intake_result.get("exif") and _intake_result["exif"].get("gps"):
                            _image_gps = _intake_result["exif"]["gps"]
                        app.logger.info(
                            "intake_media: saved %s (hash=%s exif=%s gps=%s)",
                            name,
                            _intake_result.get("file_hash", "")[:16],
                            bool(_intake_result.get("exif_b64")),
                            bool(_image_gps),
                        )
                    except Exception as _exc:
                        app.logger.warning("intake_media failed for %s, falling back: %s", name, _exc)
                        try:
                            saved_path = _save_slack_image_copy(binary_data, name, mime, channel if isinstance(channel, str) else None)
                        except Exception as _exc2:
                            app.logger.warning("Fallback _save_slack_image_copy also failed (%s): %s", name, _exc2)
                else:
                    try:
                        saved_path = _save_slack_image_copy(binary_data, name, mime, channel if isinstance(channel, str) else None)
                    except Exception as exc:
                        app.logger.warning("Failed to save Slack image locally (%s): %s", name, exc)

                if isinstance(saved_path, str) and saved_path:
                    saved_image_paths.append(saved_path)
                    file_lines[-1] = f"{file_lines[-1]}; saved_as={saved_path}"

                vision_bytes, vision_mime = _resize_image_for_vision(binary_data, mime, SLACK_IMAGE_MAX_LONG_EDGE)
                encoded = base64.b64encode(vision_bytes).decode("ascii")
                image_data_urls.append(f"data:{vision_mime};base64,{encoded}")
                image_metadata.append({
                    "name": name,
                    "saved_path": saved_path if isinstance(saved_path, str) else None,
                    "gps": _image_gps,
                    "exif_full": _image_exif_full,
                    "vision_data_url": f"data:{vision_mime};base64,{encoded}",
                })
                app.logger.info("Attached image captured for OpenAI vision input: %s", name)
            else:
                app.logger.info("Attached image could not be downloaded: %s", name)

    if not file_lines:
        app.logger.info("No usable Slack file metadata extracted")
        return "", "", [], []

    prompt_suffix = "\n\nSlack attached files metadata:\n" + "\n".join(f"- {line}" for line in file_lines)
    if file_content_lines:
        prompt_suffix += "\n\nSlack attached text file contents:\n" + "\n\n".join(file_content_lines)
        app.logger.info("Slack file context includes %s file content block(s)", len(file_content_lines))
    else:
        app.logger.info("Slack file context includes metadata only (no downloadable text content)")
    if image_data_urls:
        prompt_suffix += (
            f"\n\nSlack attached image count for analysis: {len(image_data_urls)}"
        )
        app.logger.info("Slack file context includes %s image(s) for OpenAI vision", len(image_data_urls))
    if saved_image_paths:
        prompt_suffix += "\n\nSaved local image copies:\n" + "\n".join(f"- {path}" for path in saved_image_paths)
        app.logger.info("Saved %s Slack image(s) under local generated_data path", len(saved_image_paths))
    if saved_pdf_paths:
        prompt_suffix += "\n\nSaved local PDF copies:\n" + "\n".join(f"- {path}" for path in saved_pdf_paths)
        app.logger.info("Saved %s Slack PDF file(s) under local generated_data path", len(saved_pdf_paths))
    if saved_docx_paths:
        prompt_suffix += "\n\nSaved local DOCX copies:\n" + "\n".join(f"- {path}" for path in saved_docx_paths)
        app.logger.info("Saved %s Slack DOCX file(s) under local generated_data path", len(saved_docx_paths))
    if saved_excel_paths:
        prompt_suffix += "\n\nSaved local Excel copies:\n" + "\n".join(f"- {path}" for path in saved_excel_paths)
        app.logger.info("Saved %s Slack Excel file(s) under local generated_data path", len(saved_excel_paths))
    reply_suffix = "\n\nAttachments in your message: " + ", ".join(reply_items)
    return prompt_suffix, reply_suffix, image_data_urls, image_metadata


if slack_event_adapter is not None:
    @slack_event_adapter.on("message")
    def handle_slack_message(event_data: dict[str, Any]) -> None:
        """Handle Slack messages by generating and posting an AI reply."""
        event = event_data.get("event", {}) if isinstance(event_data, dict) else {}
        if not isinstance(event, dict):
            return

        subtype = event.get("subtype")
        app.logger.info("Slack message event received: subtype=%s", subtype)
        if subtype not in {None, "file_share"}:
            app.logger.info("Skipping Slack event due to unsupported subtype=%s", subtype)
            return

        if event.get("bot_id"):
            return

        if _is_duplicate_slack_event(event_data, event):
            app.logger.info("Ignoring duplicate Slack event delivery")
            return

        channel = event.get("channel")
        if not isinstance(channel, str) or not channel:
            nested_message = event.get("message")
            if isinstance(nested_message, dict):
                channel = nested_message.get("channel")
        text = _extract_slack_message_text(event)
        slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
        file_prompt_suffix, file_reply_suffix, image_data_urls, image_metadata = _extract_slack_file_context(event, slack_bot_token)
        if not isinstance(channel, str) or not channel:
            return
        if not isinstance(text, str):
            return

        if _is_unreadable_slack_preview_text(text) and not file_prompt_suffix and not image_data_urls:
            app.logger.info("Slack message contains unreadable preview placeholder text")
            text = (
                "The user pasted spreadsheet content, but Slack only provided an unreadable preview placeholder. "
                "Ask the user to upload as .tsv or paste tab-separated lines so the table can be parsed."
            )

        if not text.strip() and not file_prompt_suffix and not image_data_urls:
            block_count = len(event.get("blocks", [])) if isinstance(event.get("blocks"), list) else 0
            attachment_count = len(event.get("attachments", [])) if isinstance(event.get("attachments"), list) else 0
            nested_message = event.get("message")
            if isinstance(nested_message, dict):
                nested_block_count = len(nested_message.get("blocks", [])) if isinstance(nested_message.get("blocks"), list) else 0
                nested_attachment_count = len(nested_message.get("attachments", [])) if isinstance(nested_message.get("attachments"), list) else 0
            else:
                nested_block_count = 0
                nested_attachment_count = 0

            app.logger.info(
                "Skipping Slack message: no text/files after extraction; blocks=%s attachments=%s nested_blocks=%s nested_attachments=%s",
                block_count,
                attachment_count,
                nested_block_count,
                nested_attachment_count,
            )
            return

        app.logger.info(
            "Slack message received: channel=%s user=%s text=%s",
            channel,
            event.get("user"),
            text,
        )

        forced_conversation_id = os.getenv("SLACK_CONVERSATION_ID", "").strip()
        if forced_conversation_id:
            conversation_id = forced_conversation_id
        else:
            conversation_key = event.get("thread_ts") or channel
            conversation_id = f"slack:{conversation_key}"
        model_name = os.getenv("SLACK_OPENAI_MODEL", "gpt-4.1-mini")
        max_tool_rounds_raw = os.getenv("SLACK_OPENAI_MAX_TOOL_ROUNDS", "10").strip()
        try:
            max_tool_rounds = int(max_tool_rounds_raw)
        except ValueError:
            max_tool_rounds = 10
        if max_tool_rounds <= 0:
            max_tool_rounds = 10

        try:
            validate_request(
                "plugins.integrations.openai_plugin",
                "OpenAIFunctionCallingPlugin",
                "generate_with_function_calls_and_history",
            )
            executor.instantiate(
                "plugins.integrations.openai_plugin",
                "OpenAIFunctionCallingPlugin",
                {},
            )
            vision_urls_to_send = image_data_urls
            if vision_urls_to_send:
                app.logger.info("Sending %s vision image(s) inline to OpenAI", len(vision_urls_to_send))
            ai_result = executor.call_method(
                "plugins.integrations.openai_plugin",
                "generate_with_function_calls_and_history",
                [
                    conversation_id,
                    (text.strip() or "The user shared a file with no message. Acknowledge receipt briefly. Do NOT describe, analyze, or load any image content — wait for the user to ask.") + file_prompt_suffix + f"\n[slack_channel_id: {channel}]",
                    model_name,
                    max_tool_rounds,
                    vision_urls_to_send,
                ],
            )
            app.logger.info("Slack AI generation completed; result_type=%s", type(ai_result).__name__)
            if isinstance(ai_result, dict):
                reply_text = str(ai_result.get("text", "")).strip()
            else:
                reply_text = str(ai_result).strip()

            app.logger.debug("[MongoDB-save] ai_result type=%s is_dict=%s", type(ai_result).__name__, isinstance(ai_result, dict))
            if isinstance(ai_result, dict):
                _analyzed_paths = ai_result.get("analyzed_image_paths") or []
                _existing_saved_paths = {m.get("saved_path") for m in image_metadata}
                app.logger.debug("[MongoDB-save] analyzed_image_paths=%s", _analyzed_paths)
                app.logger.debug("[MongoDB-save] existing_saved_paths=%s", _existing_saved_paths)
                for _ap in _analyzed_paths:
                    if _ap in _existing_saved_paths:
                        app.logger.debug("[MongoDB-save] Skipping %s (already in Slack attachment metadata)", _ap)
                        continue
                    app.logger.debug("[MongoDB-save] Building metadata from path: %s", _ap)
                    try:
                        with open(_ap, "rb") as _f:
                            _ap_bytes = _f.read()
                        app.logger.debug("[MongoDB-save] File read OK: %s bytes=%d", _ap, len(_ap_bytes))
                        _ap_name = os.path.basename(_ap)

                        # Extract full EXIF and GPS from the analyzed image path
                        _ap_exif_full: dict | None = None
                        _ap_gps: dict | None = None
                        if _ap.lower().endswith((".jpg", ".jpeg")):
                            _ap_exif_full = _extract_exif_full(_ap_bytes, _ap_name)
                            _ap_gps = _extract_gps_from_exif(_ap_bytes, _ap_name)
                            if _ap_gps:
                                app.logger.debug("[MongoDB-save] GPS extracted: %s", _ap_gps)

                        _ap_mime = "image/jpeg" if _ap.lower().endswith((".jpg", ".jpeg")) else "image/png"
                        _vis_bytes, _vis_mime = _resize_image_for_vision(_ap_bytes, _ap_mime, SLACK_IMAGE_MAX_LONG_EDGE)
                        _vis_enc = base64.b64encode(_vis_bytes).decode("ascii")
                        image_metadata.append({
                            "name": _ap_name,
                            "saved_path": _ap,
                            "gps": _ap_gps,
                            "exif_full": _ap_exif_full,
                            "vision_data_url": f"data:{_vis_mime};base64,{_vis_enc}",
                        })
                        app.logger.debug("[MongoDB-save] Appended metadata for: %s", _ap_name)
                    except Exception as _exc:
                        app.logger.warning("[MongoDB-save] Could not build metadata for %s: %s", _ap, _exc)
            else:
                app.logger.debug("[MongoDB-save] ai_result is not a dict — analyzed_image_paths unavailable")

            app.logger.info("[MongoDB-save] image_metadata present=%s count=%d", bool(image_metadata), len(image_metadata))
            if image_metadata:
                def _save_image_analysis(
                    _channel=channel,
                    _user=event.get("user"),
                    _ts=event.get("ts"),
                    _query=text,
                    _model=model_name,
                    _findings=reply_text,
                    _meta=list(image_metadata),
                    _conv_id=conversation_id,
                ) -> None:
                    app.logger.info("[MongoDB-save] Thread started: channel=%s user=%s images=%d", _channel, _user, len(_meta))
                    from datetime import datetime, timezone
                    try:
                        from plugins.mongodb_plugin import MongoDBPlugin
                        mongo = MongoDBPlugin()
                        analyzed_at = datetime.now(timezone.utc).isoformat()
                        doc = {
                            "channel": _channel,
                            "user": _user,
                            "event_ts": _ts,
                            "user_query": _query,
                            "model": _model,
                            "ai_findings": _findings,
                            "image_count": len(_meta),
                            "images": _meta,
                            "analyzed_at": analyzed_at,
                        }
                        app.logger.debug("[MongoDB-save] Calling create_document with %d image(s)", len(_meta))
                        result = mongo.create_document("slack_image_analyses", doc)
                        inserted_id = result.get("inserted_id", "unknown") if isinstance(result, dict) else "unknown"
                        app.logger.info(
                            "[MongoDB-save] Saved: channel=%s images=%d id=%s",
                            _channel,
                            len(_meta),
                            inserted_id,
                        )
                        try:
                            from plugins.integrations.openai_plugin import OpenAIFunctionCallingPlugin
                            openai_plugin = OpenAIFunctionCallingPlugin()
                            history = openai_plugin._load_conversation_history(_conv_id)
                            image_names = ", ".join(m.get("name", "unknown") for m in _meta)
                            history.append({
                                "role": "assistant",
                                "content": (
                                    f"[System event] I saved the image analysis to MongoDB.\n"
                                    f"Collection: slack_image_analyses\n"
                                    f"Document ID: {inserted_id}\n"
                                    f"Images: {image_names}\n"
                                    f"Saved at: {analyzed_at}"
                                ),
                            })
                            openai_plugin._save_conversation_history(_conv_id, history)
                            app.logger.info("[MongoDB-save] History injected into conversation %s", _conv_id)
                        except Exception as exc:
                            app.logger.warning("[MongoDB-save] History injection failed (non-fatal): %s", exc)
                    except Exception as exc:
                        app.logger.exception("[MongoDB-save] Failed to save image analysis to MongoDB: %s", exc)

                app.logger.info("[MongoDB-save] Starting background thread for %d image(s)", len(image_metadata))
                threading.Thread(target=_save_image_analysis, daemon=True).start()
        except Exception:
            app.logger.exception("Failed to generate Slack AI reply")
            reply_text = "Sorry, I couldn't generate a reply right now."

        if reply_text and file_reply_suffix:
            reply_text = f"{reply_text}{file_reply_suffix}"

        if not reply_text:
            app.logger.info("Skipping Slack reply post because generated reply text is empty")
            return

        if not isinstance(slack_bot_token, str) or not slack_bot_token.strip():
            app.logger.warning("SLACK_BOT_TOKEN is not set; cannot post Slack reply")
            return

        try:
            validate_request(
                "plugins.integrations.slack_plugin",
                "SlackPlugin",
                "post_message",
            )
            executor.instantiate(
                "plugins.integrations.slack_plugin",
                "SlackPlugin",
                {"bot_token": slack_bot_token.strip(), "default_channel": SLACK_NETWORK_CHANNEL},
            )
            executor.call_method(
                "plugins.integrations.slack_plugin",
                "post_message",
                [channel, reply_text],
            )
            app.logger.info("Slack reply posted successfully to channel=%s", channel)
        except Exception:
            app.logger.exception("Failed to post Slack AI reply")


def _error_response(message: str, status_code: int = 400):
    """Standardized API error response."""
    return jsonify({"status": "error", "message": message}), status_code


@app.get("/")
def health_check() -> Any:
    """Health-check endpoint."""
    return jsonify({"status": "ok"})


@app.post("/slack/interactivity")
def slack_interactivity() -> Any:
    """Handle Slack interactive payloads such as modal form submissions."""
    if not signing_secret:
        return _error_response("SIGNING_SECRET is not configured", status_code=503)
    if not _verify_slack_signed_request(request, signing_secret):
        return _error_response("Invalid Slack signature", status_code=401)

    raw_payload = request.form.get("payload", "")
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        return _error_response("Missing Slack payload")

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return _error_response("Slack payload must be valid JSON")

    if not isinstance(payload, dict):
        return _error_response("Slack payload must be an object")

    payload_type = payload.get("type")

    if payload_type == "view_submission":
        view = payload.get("view") if isinstance(payload.get("view"), dict) else {}
        state = view.get("state") if isinstance(view.get("state"), dict) else {}
        state_values = state.get("values") if isinstance(state.get("values"), dict) else {}

        try:
            from plugins.integrations.slack_plugin import SlackPlugin
            submitted_values = SlackPlugin.extract_view_submission_values(state_values)
        except Exception:
            app.logger.exception("Failed to extract Slack form submission values")
            submitted_values = {}

        user_info = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        team_info = payload.get("team") if isinstance(payload.get("team"), dict) else {}
        container_info = payload.get("container") if isinstance(payload.get("container"), dict) else {}
        channel_id = container_info.get("channel_id")
        if not channel_id:
            root = payload.get("root")
            if isinstance(root, dict):
                channel_id = root.get("channel") or root.get("channel_id")
        trigger_id = payload.get("trigger_id")
        submission_record = {
            "type": "view_submission",
            "team_id": team_info.get("id"),
            "user_id": user_info.get("id"),
            "user_username": user_info.get("username"),
            "view_id": view.get("id"),
            "callback_id": view.get("callback_id"),
            "private_metadata": view.get("private_metadata"),
            "app_id": payload.get("api_app_id"),
            "channel_id": channel_id,
            "trigger_id": trigger_id,
            "values": submitted_values,
            "raw_state_values": state_values,
        }
        _store_slack_form_submission(submission_record)

        from threading import Thread
        def process_form_submission_async(user_info, channel_id, submitted_values, trigger_id, view, payload):
            try:
                from executor.engine import JSONExecutor
                from executor.permissions import validate_request
                executor = JSONExecutor()
                validate_request(
                    "plugins.integrations.openai_plugin",
                    "OpenAIFunctionCallingPlugin",
                    "generate_with_function_calls_and_history",
                )
                executor.instantiate(
                    "plugins.integrations.openai_plugin",
                    "OpenAIFunctionCallingPlugin",
                    {},
                )
                form_message = "Form submission:\n" + "\n".join(f"{k}: {v}" for k, v in submitted_values.items())
                openai_payload = {
                    "user_id": user_info.get("id"),
                    "form_message": form_message,
                    "trigger_id": trigger_id,
                    "channel_id": channel_id,
                    "values": submitted_values,
                }
                app.logger.debug("[Form] Payload sent to OpenAI plugin:\n%s", json.dumps(openai_payload, indent=2, ensure_ascii=False))
                ai_result = executor.call_method(
                    "plugins.integrations.openai_plugin",
                    "generate_with_function_calls_and_history",
                    [
                        f"slack:form:{user_info.get('id')}",
                        json.dumps(openai_payload),
                        os.getenv("SLACK_OPENAI_MODEL", "gpt-4.1-mini"),
                        5,
                        [],
                    ]
                )
                app.logger.debug("[Form] OpenAI plugin response: %s", ai_result)
                reply_text = ai_result.get("text", "") if isinstance(ai_result, dict) else str(ai_result)
                app.logger.debug("[Form] Reply text to post to Slack: %s", reply_text)
                slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
                post_channel = channel_id
                if not post_channel:
                    user_id = user_info.get("id")
                    if user_id and slack_bot_token:
                        try:
                            import requests
                            resp = requests.post(
                                "https://slack.com/api/conversations.open",
                                headers={
                                    "Authorization": f"Bearer {slack_bot_token.strip()}",
                                    "Content-Type": "application/json",
                                },
                                json={"users": user_id},
                                timeout=5,
                            )
                            resp_json = resp.json()
                            app.logger.debug("[Form] conversations.open response: %s", resp_json)
                            if resp_json.get("ok") and resp_json.get("channel", {}).get("id"):
                                post_channel = resp_json["channel"]["id"]
                        except Exception as e:
                            app.logger.warning("[Form] Failed to open DM channel: %s", e)
                if not post_channel:
                    post_channel = "#general"
                app.logger.debug("[Form] Posting reply to channel: %s", post_channel)
                if slack_bot_token:
                    validate_request(
                        "plugins.integrations.slack_plugin",
                        "SlackPlugin",
                        "post_message",
                    )
                    executor.instantiate(
                        "plugins.integrations.slack_plugin",
                        "SlackPlugin",
                        {"bot_token": slack_bot_token.strip(), "default_channel": SLACK_NETWORK_CHANNEL},
                    )
                    executor.call_method(
                        "plugins.integrations.slack_plugin",
                        "post_message",
                        [post_channel, reply_text],
                    )
            except Exception:
                app.logger.exception("Failed to process form submission with OpenAI or post reply to Slack (async)")

        Thread(target=process_form_submission_async, args=(user_info, channel_id, submitted_values, trigger_id, view, payload), daemon=True).start()
        return jsonify({"response_action": "clear"})

    if payload_type == "block_actions":
        user_info = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        container_info = payload.get("container") if isinstance(payload.get("container"), dict) else {}
        channel_id = container_info.get("channel_id")
        actions = payload.get("actions", [])
        _store_slack_form_submission(
            {
                "type": "block_actions",
                "user_id": user_info.get("id"),
                "user_username": user_info.get("username"),
                "channel_id": channel_id,
                "message_ts": container_info.get("message_ts"),
                "actions": actions,
            }
        )

        trigger_id = payload.get("trigger_id")
        modal_view = None
        redis_client = _slack_dedupe_redis_client
        modal_key = None
        app.logger.debug("[block_actions] actions received: %s", actions)
        for action in actions:
            app.logger.debug("[block_actions] Inspecting action: %s", action)
            value = action.get("value")
            if value and isinstance(value, str) and value.startswith("modalview:"):
                modal_key = value[len("modalview:"):]
                app.logger.debug("[block_actions] Extracted modal_key: %s from action_id: %s", modal_key, action.get("action_id"))
        if modal_key:
            app.logger.debug("[block_actions] modal_key=%s redis_client=%s", modal_key, "present" if redis_client is not None else "None")
        if modal_key and redis_client is not None:
            try:
                modal_json = redis_client.get(f"slack:modal_view:{modal_key}")
                app.logger.debug("[block_actions] modal_json from Redis for key slack:modal_view:%s: %s", modal_key, modal_json)
                if modal_json:
                    modal_view = json.loads(modal_json)
            except Exception as exc:
                app.logger.warning(f"Failed to fetch modal_view from Redis: {exc}")
        if not modal_view:
            modal_view = {
                "type": "modal",
                "title": {"type": "plain_text", "text": "My Modal"},
                "close": {"type": "plain_text", "text": "Close"},
                "submit": {"type": "plain_text", "text": "Submit"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "input_c",
                        "label": {"type": "plain_text", "text": "What are your thoughts?"},
                        "element": {"type": "plain_text_input", "action_id": "user_input"}
                    }
                ]
            }
        if trigger_id:
            try:
                from executor.engine import JSONExecutor
                from executor.permissions import validate_request
                executor = JSONExecutor()
                validate_request(
                    "plugins.integrations.slack_plugin",
                    "SlackPlugin",
                    "open_modal",
                )
                executor.instantiate(
                    "plugins.integrations.slack_plugin",
                    "SlackPlugin",
                    {"bot_token": os.getenv("SLACK_BOT_TOKEN", ""), "default_channel": SLACK_NETWORK_CHANNEL},
                )
                result = executor.call_method(
                    "plugins.integrations.slack_plugin",
                    "open_modal",
                    [{"trigger_id": trigger_id, "modal_view": modal_view}],
                )
                return jsonify(result)
            except Exception as exc:
                app.logger.exception("Failed to open Slack modal: %s", exc)
                return jsonify({"status": "error", "message": f"Failed to open modal: {exc}"}), 500
        return jsonify({"status": "error", "message": "Missing trigger_id for modal opening"}), 400

    return jsonify({"status": "ignored", "payload_type": payload_type})


@app.get("/slack/form-submissions")
def slack_form_submissions() -> Any:
    """Return recent Slack form submissions captured by the interactivity endpoint."""
    raw_limit = request.args.get("limit", "25")
    try:
        limit = int(raw_limit)
    except ValueError:
        return _error_response("limit must be an integer")

    if limit <= 0 or limit > 200:
        return _error_response("limit must be between 1 and 200")

    return jsonify(
        {
            "status": "success",
            "count": len(_get_recent_slack_form_submissions(limit)),
            "submissions": _get_recent_slack_form_submissions(limit),
        }
    )


def _validate_execution_fields(payload: dict[str, Any]) -> tuple[str, str, str, dict[str, Any], list[Any]]:
    """Validate shared execute/workflow step fields and return normalized values."""
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


def _resolve_result_path(value: Any, path: str) -> Any:
    """Resolve dotted path access for dict results."""
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Reference path '{path}' was not found in step result")
        current = current[part]
    return current


def _resolve_references(value: Any, step_results: dict[str, Any]) -> Any:
    """Resolve ${steps.<id>.result[.path]} references in workflow step inputs."""
    if isinstance(value, dict):
        return {key: _resolve_references(item, step_results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_references(item, step_results) for item in value]
    if isinstance(value, str):
        match = WORKFLOW_REF_PATTERN.fullmatch(value.strip())
        if match is None:
            return value

        step_id = match.group(1)
        result_path = match.group(2)
        if step_id not in step_results:
            raise ValueError(f"Referenced step '{step_id}' has no available result")

        resolved = step_results[step_id]
        if result_path:
            return _resolve_result_path(resolved, result_path)
        return resolved

    return value


@app.post("/execute")
def execute() -> Any:
    """Validate and execute a JSON-defined plugin method call."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response("Request body must be valid JSON")

    try:
        module_name, class_name, method_name, constructor_args, args = _validate_execution_fields(
            payload
        )
        validate_request(module_name, class_name, method_name)
        executor.instantiate(module_name, class_name, constructor_args)
        result = executor.call_method(module_name, method_name, args)
        return jsonify({"status": "success", "result": result})
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)
    except (ImportError, AttributeError, TypeError) as exc:
        message = str(exc) if str(exc) else "Invalid execution request"
        return _error_response(message, status_code=400)
    except Exception:
        app.logger.exception("Unhandled execution error")
        return _error_response("Internal server error", status_code=500)


@app.post("/workflow")
def workflow() -> Any:
    """Execute a chain of allowlisted plugin calls in sequence."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response("Request body must be valid JSON")

    steps = payload.get("steps")
    stop_on_error = payload.get("stop_on_error", True)
    if not isinstance(steps, list) or not steps:
        return _error_response("steps must be a non-empty array")
    if not isinstance(stop_on_error, bool):
        return _error_response("stop_on_error must be a boolean")

    step_results: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    has_errors = False

    try:
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

            module_name, class_name, method_name, constructor_args, args = _validate_execution_fields(step)
            constructor_args = _resolve_references(constructor_args, step_results)
            args = _resolve_references(args, step_results)

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
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "message": f"Workflow failed at step '{step_id}'",
                                "failed_step": step_id,
                                "results": results,
                            }
                        ),
                        400,
                    )

        return jsonify({"status": "success", "has_errors": has_errors, "results": results})
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)
    except Exception:
        app.logger.exception("Unhandled workflow execution error")
        return _error_response("Internal server error", status_code=500)


# ---------------------------------------------------------------------------
# File Storage — helpers
# ---------------------------------------------------------------------------

def _check_file_api_key() -> bool:
    """Return True when no key is configured, or when the request supplies it."""
    if not FILE_UPLOAD_API_KEY:
        return True
    provided = (
        request.headers.get("X-API-Key", "")
        or request.args.get("api_key", "")
    ).strip()
    if not provided:
        return False
    return hmac.compare_digest(FILE_UPLOAD_API_KEY, provided)


# ---------------------------------------------------------------------------
# File Storage — routes
# ---------------------------------------------------------------------------

@app.errorhandler(413)
def _handle_request_entity_too_large(error: Any) -> Any:
    return _error_response(
        f"File exceeds the maximum allowed size of {FILE_MAX_UPLOAD_MB} MB",
        status_code=413,
    )


def _trigger_upload_notification(
    filename: str,
    relative_path: str,
    size_bytes: int,
    lat: float | None = None,
    lon: float | None = None,
) -> None:
    """Fire-and-forget: post a Slack upload notification directly via SlackPlugin."""
    import threading
    from datetime import datetime, timezone

    def _notify() -> None:
        app.logger.info("[UploadNotify] Thread started")
        bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        if not bot_token:
            app.logger.warning("Upload notification: SLACK_BOT_TOKEN not set, skipping")
            return

        uploaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        size_kb = size_bytes / 1024

        app.logger.info("[UploadNotify] Posting to channel: %s", SLACK_NETWORK_CHANNEL)
        try:
            from plugins.integrations.slack_plugin import SlackPlugin
            from plugins.integrations.openai_plugin import OpenAIFunctionCallingPlugin
            slack = SlackPlugin(bot_token=bot_token, default_channel=SLACK_NETWORK_CHANNEL)
            upload_result: dict[str, Any] | None = None

            # Upload the actual file to Slack when available.
            try:
                absolute_path = (_MEDIA_STORAGE_PATH / relative_path).resolve()
                absolute_path.relative_to(_MEDIA_STORAGE_PATH)
                if absolute_path.exists() and absolute_path.is_file():
                    upload_result = slack.upload_local_file(
                        str(absolute_path),
                        channel=SLACK_NETWORK_CHANNEL,
                        initial_comment=(
                            f":file_folder: Uploaded via Streamlit: {filename} "
                            f"({size_kb:.1f} KB)\n"
                            f"Path: {MEDIA_STORAGE_DIR}/{relative_path}\n"
                            f"Uploaded: {uploaded_at}"
                            + (
                                f"\nLocation: {lat:.6f}, {lon:.6f}"
                                f"\nMap: https://maps.google.com/?q={lat:.6f},{lon:.6f}"
                                if lat is not None and lon is not None
                                else ""
                            )
                        ),
                    )
                    app.logger.info("[UploadNotify] upload_local_file result: %s", upload_result)
                    app.logger.info("Upload notification posted to %s for file %s", SLACK_NETWORK_CHANNEL, filename)

                    # Keep conversation memory up to date without triggering model generation.
                    try:
                        channel_for_history = (
                            upload_result.get("channel_id")
                            if isinstance(upload_result.get("channel_id"), str)
                            else (
                                upload_result.get("channel")
                                if isinstance(upload_result.get("channel"), str)
                                else SLACK_NETWORK_CHANNEL
                            )
                        )
                        forced_conversation_id = os.getenv("SLACK_CONVERSATION_ID", "").strip()
                        conversation_id = (
                            forced_conversation_id
                            if forced_conversation_id
                            else f"slack:{channel_for_history}"
                        )
                        openai_plugin = OpenAIFunctionCallingPlugin()
                        history = openai_plugin._load_conversation_history(conversation_id)
                        history.append(
                            {
                                "role": "assistant",
                                "content": (
                                    "[System event] A file was uploaded via the Streamlit UI and posted to Slack.\n"
                                    f"File: {filename}\n"
                                    f"Size: {size_kb:.1f} KB\n"
                                    f"Path: {MEDIA_STORAGE_DIR}/{relative_path}\n"
                                    f"Uploaded at: {uploaded_at}"
                                    + (
                                        f"\nGPS location: lat={lat:.6f}, lon={lon:.6f}"
                                        f"\nGoogle Maps: https://maps.google.com/?q={lat:.6f},{lon:.6f}"
                                        if lat is not None and lon is not None
                                        else ""
                                    )
                                ),
                            }
                        )
                        openai_plugin._save_conversation_history(conversation_id, history)
                        app.logger.info("Upload notification history written for conversation %s", conversation_id)
                    except Exception as exc:
                        app.logger.warning("Upload notification: history write failed (non-fatal): %s", exc)
                else:
                    app.logger.warning(
                        "Upload notification: local file missing for Slack upload: %s",
                        absolute_path,
                    )
            except Exception as exc:
                app.logger.warning("Upload notification: Slack file upload failed: %s", exc)
            if upload_result is None:
                app.logger.warning("Upload notification: Slack file upload did not complete")

        except Exception as exc:
            app.logger.warning("Upload notification: Slack post failed: %s", exc)

    threading.Thread(target=_notify, daemon=True).start()


@app.post("/files/upload")
def upload_file() -> Any:
    """Accept a multipart/form-data file and store it in the generated_data directory.

    Form fields:
      - file (required): the file to upload
      - folder (optional): subdirectory within generated_data, e.g. "videos/2026"

    Headers:
      - X-API-Key: required when FILE_UPLOAD_API_KEY is set in the environment
    """
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    if "file" not in request.files:
        return _error_response("No 'file' field in the multipart request")

    f = request.files["file"]
    if not f.filename:
        return _error_response("No filename provided")

    ext = Path(f.filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_FILE_EXTENSIONS:
        return _error_response(
            f"File type '.{ext}' is not allowed. "
            f"Allowed: {', '.join(sorted(ALLOWED_FILE_EXTENSIONS))}"
        )

    safe_name = _media_storage_plugin._sanitize_filename(f.filename)
    folder = request.form.get("folder", "").strip()

    try:
        dest = _media_storage_plugin._resolve_path(folder, safe_name)
    except ValueError as exc:
        return _error_response(str(exc))

    if dest.exists():
        ts = int(time.time())
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        safe_name = f"{stem}_{ts}{suffix}"
        try:
            dest = _media_storage_plugin._resolve_path(folder, safe_name)
        except ValueError as exc:
            return _error_response(str(exc))

    dest.parent.mkdir(parents=True, exist_ok=True)

    lat_raw = request.form.get("lat", "").strip()
    lon_raw = request.form.get("lon", "").strip()
    has_gps = bool(lat_raw and lon_raw)

    _JPEG_EXTS = {".jpg", ".jpeg"}
    _CONVERTIBLE_EXTS = {".png", ".webp", ".gif", ".bmp", ".tiff"}
    if has_gps and dest.suffix.lower() in _CONVERTIBLE_EXTS and _PILImage is not None:
        try:
            import io as _io_mod
            img = _PILImage.open(f.stream)
            img = img.convert("RGB")
            buf = _io_mod.BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            stem = Path(safe_name).stem
            safe_name = f"{stem}.jpg"
            try:
                dest = _media_storage_plugin._resolve_path(folder, safe_name)
            except ValueError as exc:
                return _error_response(str(exc))
            if dest.exists():
                ts_now = int(time.time())
                safe_name = f"{stem}_{ts_now}.jpg"
                try:
                    dest = _media_storage_plugin._resolve_path(folder, safe_name)
                except ValueError as exc:
                    return _error_response(str(exc))
            dest.write_bytes(buf.getvalue())
            app.logger.info("Converted %s to JPEG for GPS EXIF embedding: %s", ext, safe_name)
        except Exception as exc:
            app.logger.warning("JPEG conversion failed, saving original format: %s", exc)
            f.stream.seek(0)
            f.save(str(dest))
    else:
        f.save(str(dest))

    if has_gps and dest.suffix.lower() in _JPEG_EXTS:
        try:
            import piexif

            existing_bytes = dest.read_bytes()
            try:
                existing_exif = piexif.load(existing_bytes)
            except Exception:
                existing_exif = {"GPS": {}}

            already_has_gps = bool(existing_exif.get("GPS"))
            if already_has_gps:
                app.logger.info("Skipping GPS EXIF write for %s — existing GPS data found", dest.name)
            else:
                def _to_dms_rational(degrees: float) -> tuple:
                    d = int(abs(degrees))
                    m_float = (abs(degrees) - d) * 60
                    m = int(m_float)
                    s = round((m_float - m) * 60 * 10000)
                    return ((d, 1), (m, 1), (s, 10000))

                lat_val = float(lat_raw)
                lon_val = float(lon_raw)
                existing_exif["GPS"] = {
                    piexif.GPSIFD.GPSLatitudeRef: b"N" if lat_val >= 0 else b"S",
                    piexif.GPSIFD.GPSLatitude: _to_dms_rational(lat_val),
                    piexif.GPSIFD.GPSLongitudeRef: b"E" if lon_val >= 0 else b"W",
                    piexif.GPSIFD.GPSLongitude: _to_dms_rational(lon_val),
                }
                exif_bytes = piexif.dump(existing_exif)
                piexif.insert(exif_bytes, existing_bytes, str(dest))
                app.logger.info("GPS EXIF written to %s: lat=%s, lon=%s", dest.name, lat_raw, lon_raw)
        except Exception as exc:
            app.logger.warning("Failed to write GPS EXIF to %s: %s", dest.name, exc)

    size_bytes = dest.stat().st_size
    relative = dest.relative_to(_media_storage_plugin._base).as_posix()

    notify_lat: float | None = None
    notify_lon: float | None = None
    if has_gps:
        try:
            notify_lat = float(lat_raw)
            notify_lon = float(lon_raw)
        except ValueError:
            pass

    # Write slack_files record with EXIF extracted directly from the saved file.
    gps = {"lat": notify_lat, "lon": notify_lon} if notify_lat is not None and notify_lon is not None else None

    # Step 1: Extract exif_b64 directly from disk — independent of any Slack/plugin calls.
    _exif_b64: str | None = None
    if dest.suffix.lower() in (".jpg", ".jpeg", ".tiff", ".tif"):
        try:
            import piexif as _piexif_upload, base64 as _b64_upload
            _raw_bytes = dest.read_bytes()
            _raw_exif = _piexif_upload.load(_raw_bytes)
            _exif_b64 = _b64_upload.b64encode(_piexif_upload.dump(_raw_exif)).decode("ascii")
        except Exception as _exif_exc:
            app.logger.warning("Could not extract EXIF from %s: %s", safe_name, _exif_exc)

    # Step 2: Always write the slack_files MongoDB record (this is the source of truth).
    try:
        from plugins.integrations.slack_plugin import SlackPlugin
        _bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        _slack = SlackPlugin(bot_token=_bot_token, default_channel=SLACK_NETWORK_CHANNEL) if _bot_token else None
        if _slack:
            _slack._save_file_record(
                local_file_path=str(dest),
                file_id=None,
                filename=safe_name,
                title=safe_name,
                channel=SLACK_NETWORK_CHANNEL,
                channel_id=None,
                permalink=None,
                url_private=None,
                exif_b64=_exif_b64,
                gps=gps,
            )
            app.logger.info("slack_files record written for %s (exif=%s)", safe_name, bool(_exif_b64))
        else:
            app.logger.warning("SLACK_BOT_TOKEN not set; skipping slack_files record for %s", safe_name)
    except Exception as exc:
        app.logger.warning("Failed to write slack_files record for %s: %s", safe_name, exc)

    _trigger_upload_notification(safe_name, relative, size_bytes, lat=notify_lat, lon=notify_lon)

    return jsonify({
        "status": "success",
        "filename": safe_name,
        "path": relative,
        "size_bytes": size_bytes,
        "download_url": f"/files/download/{relative}",
    })


@app.get("/files/download/<path:filename>")
def download_file(filename: str) -> Any:
    """Stream a stored file back to the caller."""
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    try:
        target = (_MEDIA_STORAGE_PATH / filename).resolve()
        target.relative_to(_MEDIA_STORAGE_PATH)
    except ValueError:
        return _error_response("Invalid file path", status_code=400)

    if not target.exists() or not target.is_file():
        # Transparent Slack fallback: query MongoDB for the file record and
        # re-download from Slack so ephemeral dyno storage is fully transparent.
        recovered = False
        try:
            from plugins.integrations.slack_plugin import SlackPlugin
            bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
            if bot_token:
                slack = SlackPlugin(bot_token=bot_token, default_channel=SLACK_NETWORK_CHANNEL)
                slack.get_file({"path": str(target)})
                if target.exists() and target.is_file():
                    app.logger.info("File recovered from Slack for path: %s", target)
                    recovered = True
        except Exception as exc:
            app.logger.warning("Slack file recovery failed for %s: %s", target, exc)

        if not recovered:
            return _error_response("File not found", status_code=404)

    as_attachment = request.args.get("download", "false").lower() == "true"
    return send_from_directory(str(target.parent), target.name, as_attachment=as_attachment)


def _query_slack_files_index(folder: str) -> list[dict[str, Any]]:
    """Query MongoDB slack_files for records whose local_file_path falls under the given folder.

    Returns a list of dicts shaped like MediaStoragePlugin list_files entries,
    with extra keys: source="slack", permalink, url_private, file_id.
    Only returns records not already present on the local filesystem.
    """
    try:
        from pymongo import MongoClient as _MongoClient
        from urllib.parse import urlparse as _urlparse
    except ImportError:
        return []

    mongo_uri = os.getenv("MONGODB_URI", "").strip()
    if not mongo_uri:
        return []

    try:
        client = _MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db_name = os.getenv("MONGODB_DATABASE", "").strip()
        if not db_name:
            parsed_uri = _urlparse(mongo_uri)
            raw_path = parsed_uri.path.lstrip("/")
            db_name = raw_path.split("?")[0] if raw_path else ""
        if not db_name:
            db_name = "dynamic_exec"
        collection = client[db_name]["slack_files"]
    except Exception:
        return []

    # Build a prefix filter: match records whose local_file_path starts with
    # the resolved scan directory.
    if folder:
        try:
            scan_dir = (_MEDIA_STORAGE_PATH / folder).resolve()
            scan_dir.relative_to(_MEDIA_STORAGE_PATH)
        except (ValueError, Exception):
            return []
    else:
        scan_dir = _MEDIA_STORAGE_PATH

    prefix = str(scan_dir)

    try:
        cursor = collection.find(
            {"local_file_path": {"$regex": f"^{re.escape(prefix)}"}},
            {"_id": 0, "local_file_path": 1, "filename": 1, "permalink": 1,
             "url_private": 1, "file_id": 1, "title": 1},
        )
        records = list(cursor)
    except Exception:
        return []

    extra: list[dict[str, Any]] = []
    for rec in records:
        local_path = Path(rec.get("local_file_path", ""))
        # Skip if the file is already present locally — the local listing covers it.
        if local_path.exists() and local_path.is_file():
            continue
        try:
            relative = local_path.relative_to(_MEDIA_STORAGE_PATH).as_posix()
        except ValueError:
            relative = local_path.name

        entry: dict[str, Any] = {
            "name": local_path.name,
            "path": relative,
            "type": "file",
            "source": "slack",
            "download_url": f"/files/download/{relative}",
            "permalink": rec.get("permalink"),
            "url_private": rec.get("url_private"),
            "file_id": rec.get("file_id"),
            "title": rec.get("title"),
        }
        extra.append(entry)

    return extra


@app.get("/files/list")
def list_files() -> Any:
    """List files and directories inside the media storage root or a subfolder.

    Merges local filesystem entries with MongoDB slack_files records so that
    files uploaded to Slack (but no longer on the ephemeral dyno disk) still
    appear in the listing with source="slack". Accessing their download_url
    will transparently re-fetch them from Slack on demand.

    Query params:
      - folder (optional): subdirectory to list, e.g. "videos/2026"

    Headers:
      - X-API-Key: required when FILE_UPLOAD_API_KEY is set in the environment
    """
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    folder = request.args.get("folder", "").strip()
    try:
        result = _media_storage_plugin.list_files(folder)
    except FileNotFoundError:
        # Folder may not exist locally after a dyno restart — still check MongoDB.
        result = {"status": "success", "folder": folder or "/", "count": 0, "files": []}
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)

    # Tag locally-found files with source="local"
    for entry in result.get("files", []):
        if entry.get("type") == "file":
            entry.setdefault("source", "local")

    # Merge in any Slack-only records from MongoDB
    slack_only = _query_slack_files_index(folder)
    result["files"] = result.get("files", []) + slack_only
    result["count"] = len(result["files"])
    if slack_only:
        result["note"] = (
            f"{len(slack_only)} file(s) not on local disk — stored in Slack. "
            "Access via download_url to restore automatically."
        )

    return jsonify(result)


@app.delete("/files/delete/<path:filename>")
def delete_file(filename: str) -> Any:
    """Delete a stored file."""
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    try:
        result = _media_storage_plugin.delete_file(filename)
        return jsonify(result)
    except FileNotFoundError:
        return _error_response("File not found", status_code=404)
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)


# ---------------------------------------------------------------------------
# Staging — routes
# ---------------------------------------------------------------------------

@app.post("/files/stage/<session_id>")
def stage_file(session_id: str) -> Any:
    """Upload a single file into a temporary staging area for a session."""
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    if not _media_storage_plugin._valid_session_id(session_id):
        return _error_response("Invalid session ID", status_code=400)

    f = request.files.get("file")
    if not f or not f.filename:
        return _error_response("No file provided")

    session_dir = _media_storage_plugin._safe_stage_path(session_id)
    if session_dir is None:
        return _error_response("Invalid session ID", status_code=400)

    safe_name = secure_filename(f.filename)
    if not safe_name:
        return _error_response("Invalid filename")

    session_dir.mkdir(parents=True, exist_ok=True)
    dest = session_dir / safe_name
    f.save(str(dest))

    size_bytes = dest.stat().st_size
    _trigger_upload_notification(safe_name, f"staging/{session_id}/{safe_name}", size_bytes)

    return jsonify({
        "filename": safe_name,
        "size_bytes": size_bytes,
        "staged": True,
    })


@app.get("/files/stage/<session_id>")
def list_staged_route(session_id: str) -> Any:
    """List files currently staged for a session."""
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    try:
        result = _media_storage_plugin.list_staged(session_id)
        return jsonify(result)
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)


@app.delete("/files/stage/<session_id>")
def clear_staged_route(session_id: str) -> Any:
    """Remove all staged files for a session."""
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    try:
        result = _media_storage_plugin.clear_staged(session_id)
        return jsonify(result)
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)


@app.delete("/files/stage/<session_id>/<filename>")
def remove_staged_file_route(session_id: str, filename: str) -> Any:
    """Remove a single staged file."""
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    try:
        result = _media_storage_plugin.remove_staged_file(session_id, filename)
        return jsonify(result)
    except ValueError as exc:
        return _error_response(str(exc), status_code=400)


# ---------------------------------------------------------------------------
# Rename-zip — route
# ---------------------------------------------------------------------------

@app.post("/files/rename-zip")
def rename_zip() -> Any:
    """Sort, rename, and zip uploaded images and videos grouped by video markers."""
    if not _check_file_api_key():
        return _error_response("Unauthorized", status_code=401)

    if request.is_json:
        body = request.get_json(silent=True) or {}
        session_id = body.get("session_id", "")
        sort_order = body.get("sort_order", "date_taken")
        try:
            result = _media_storage_plugin.rename_zip(session_id, sort_order)
            return jsonify(result)
        except FileNotFoundError as exc:
            return _error_response(str(exc), status_code=404)
        except ValueError as exc:
            return _error_response(str(exc), status_code=422)
        except Exception:
            app.logger.exception("rename_zip (staged) processing failed")
            return _error_response("Processing failed", status_code=500)

    uploaded = request.files.getlist("files")
    if not uploaded:
        return _error_response("No files provided")

    sort_order = request.form.get("sort_order", "date_taken")
    files_list = [(f.filename or f"file_{i}", f.read()) for i, f in enumerate(uploaded)]

    try:
        result = _media_storage_plugin._rename_zip_from_file_data(files_list, sort_order)
        return jsonify(result)
    except ValueError as exc:
        return _error_response(str(exc), status_code=422)
    except Exception:
        app.logger.exception("rename_zip processing failed")
        return _error_response("Processing failed", status_code=500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)