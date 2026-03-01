"""Flask entrypoint for the dynamic execution service."""

from __future__ import annotations

import base64
import io
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib import error as urlerror, request as urlrequest
from urllib.parse import urljoin

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from slackeventsapi import SlackEventAdapter

try:
    from pypdf import PdfReader  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None  # type: ignore[assignment]

try:
    import fitz  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore[assignment]

from executor.engine import JSONExecutor
from executor.permissions import validate_request


app = Flask(__name__)
try:
    signing_secret = os.environ["SIGNING_SECRET"]
except KeyError:
    env_path = Path(".") / ".env"
    load_dotenv(dotenv_path=env_path)
    signing_secret = os.getenv("SIGNING_SECRET")

slack_event_adapter: SlackEventAdapter | None = None
if signing_secret:
    slack_event_adapter = SlackEventAdapter(
        signing_secret, "/slack/events", app
    )
else:
    app.logger.warning("SIGNING_SECRET is not set; Slack event subscriptions are disabled")
executor = JSONExecutor()
WORKFLOW_REF_PATTERN = re.compile(r"^\$\{steps\.([^\.]+)\.result(?:\.(.+))?\}$")
SLACK_EVENT_TTL_SECONDS = 300
SLACK_MAX_IMAGE_BYTES = 5 * 1024 * 1024
SLACK_MAX_IMAGE_COUNT = 3
SLACK_MAX_PDF_BYTES = 15 * 1024 * 1024
SLACK_MAX_PDF_TEXT_CHARS = 20000
SLACK_MAX_PDF_IMAGE_PAGES = 3
SLACK_IMAGE_SAVE_BASE_DIR = os.getenv("SLACK_IMAGE_SAVE_BASE_DIR", "generated_data")
_processed_slack_events: dict[str, float] = {}
_processed_slack_events_lock = threading.Lock()


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
                    current_url = urljoin(current_url, location)
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
                    current_url = urljoin(current_url, location)
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
    """Download a Slack private file as bytes with content type."""
    if not isinstance(url, str) or not url.strip():
        return None, ""
    if not isinstance(bot_token, str) or not bot_token.strip():
        return None, ""
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        return None, ""

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
                    current_url = urljoin(current_url, location)
                    continue

                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    app.logger.warning("Slack binary file exceeded max allowed size")
                    return None, ""

                return data, content_type
        except urlerror.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location") if exc.headers else None
                if location:
                    current_url = urljoin(current_url, location)
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
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _sanitize_slack_filename(original_name)
    stem = Path(safe_name).stem or "image"
    extension = _guess_image_extension(content_type, safe_name)

    unique_suffix = str(int(time.time() * 1000))
    target_path = target_dir / f"{stem}_{unique_suffix}{extension}"
    target_path.write_bytes(binary_data)
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
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _sanitize_slack_filename(original_name)
    stem = Path(safe_name).stem or "document"
    unique_suffix = str(int(time.time() * 1000))
    target_path = target_dir / f"{stem}_{unique_suffix}.pdf"
    target_path.write_bytes(binary_data)
    return str(target_path)


def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = SLACK_MAX_PDF_TEXT_CHARS) -> str:
    """Extract bounded text from a PDF payload."""
    if PdfReader is None:
        return ""
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        return ""

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        app.logger.warning("Failed to parse PDF for text extraction: %s", exc)
        return ""

    chunks: list[str] = []
    current_size = 0
    for page in getattr(reader, "pages", []):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            continue

        remaining = max_chars - current_size
        if remaining <= 0:
            break
        trimmed = text[:remaining]
        chunks.append(trimmed)
        current_size += len(trimmed)

    return "\n\n".join(chunks).strip()


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


def _extract_slack_file_context(event: dict[str, Any], slack_bot_token: str | None) -> tuple[str, str, list[str]]:
    """Build prompt/reply snippets and image data URLs from Slack files when available."""
    files = event.get("files")
    if not isinstance(files, list) or not files:
        app.logger.info("No Slack files attached on this event")
        return "", "", []

    app.logger.info("Slack files detected: count=%s", len(files))

    file_lines: list[str] = []
    file_content_lines: list[str] = []
    image_data_urls: list[str] = []
    saved_image_paths: list[str] = []
    saved_pdf_paths: list[str] = []
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
        if is_text_candidate and url_private and isinstance(slack_bot_token, str) and slack_bot_token.strip():
            file_text = _download_slack_text_file(url_private, slack_bot_token)
            if isinstance(file_text, str) and file_text.strip():
                app.logger.info("Attached text file content captured: %s", name)
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

                extracted_pdf_text = _extract_pdf_text(pdf_data)
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

                try:
                    saved_path = _save_slack_image_copy(binary_data, name, mime, channel if isinstance(channel, str) else None)
                except Exception as exc:
                    app.logger.warning("Failed to save Slack image locally (%s): %s", name, exc)
                    saved_path = None
                if isinstance(saved_path, str) and saved_path:
                    saved_image_paths.append(saved_path)
                    file_lines[-1] = f"{file_lines[-1]}; saved_as={saved_path}"

                encoded = base64.b64encode(binary_data).decode("ascii")
                image_data_urls.append(f"data:{mime};base64,{encoded}")
                app.logger.info("Attached image captured for OpenAI vision input: %s", name)
            else:
                app.logger.info("Attached image could not be downloaded: %s", name)

    if not file_lines:
        app.logger.info("No usable Slack file metadata extracted")
        return "", "", []

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
    reply_suffix = "\n\nAttachments in your message: " + ", ".join(reply_items)
    return prompt_suffix, reply_suffix, image_data_urls


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
        text = event.get("text", "")
        slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
        file_prompt_suffix, file_reply_suffix, image_data_urls = _extract_slack_file_context(event, slack_bot_token)
        if not isinstance(channel, str) or not channel:
            return
        if not isinstance(text, str):
            return
        if not text.strip() and not file_prompt_suffix and not image_data_urls:
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
        max_tool_rounds_raw = os.getenv("SLACK_OPENAI_MAX_TOOL_ROUNDS", "5").strip()
        try:
            max_tool_rounds = int(max_tool_rounds_raw)
        except ValueError:
            max_tool_rounds = 5
        if max_tool_rounds <= 0:
            max_tool_rounds = 5

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
            ai_result = executor.call_method(
                "plugins.integrations.openai_plugin",
                "generate_with_function_calls_and_history",
                [
                    conversation_id,
                    (text.strip() or "Please analyze attached files.") + file_prompt_suffix,
                    model_name,
                    max_tool_rounds,
                    image_data_urls,
                ],
            )
            if isinstance(ai_result, dict):
                reply_text = str(ai_result.get("text", "")).strip()
            else:
                reply_text = str(ai_result).strip()
        except Exception:
            app.logger.exception("Failed to generate Slack AI reply")
            reply_text = "Sorry, I couldn't generate a reply right now."

        if reply_text and file_reply_suffix:
            reply_text = f"{reply_text}{file_reply_suffix}"

        if not reply_text:
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
                {"bot_token": slack_bot_token.strip(), "default_channel": "#general"},
            )
            executor.call_method(
                "plugins.integrations.slack_plugin",
                "post_message",
                [channel, reply_text],
            )
        except Exception:
            app.logger.exception("Failed to post Slack AI reply")


def _error_response(message: str, status_code: int = 400):
    """Standardized API error response."""
    return jsonify({"status": "error", "message": message}), status_code


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=True)