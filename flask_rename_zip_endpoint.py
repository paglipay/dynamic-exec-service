"""
Flask endpoints to add to app.py
==================================
Paste this into your Flask app alongside the existing /files/* routes.

pip requirements on the Flask server (add to your server's requirements.txt):
    Pillow
    hachoir
    werkzeug   (already a Flask dependency)

Environment variables (same ones already used by the other /files/* routes):
    FILE_STORAGE_DIR     — root directory where files are stored
    FILE_UPLOAD_API_KEY  — shared secret checked in X-API-Key header

New routes added:
    POST   /files/stage/<session_id>           — stage one file (called per-file from Streamlit)
    GET    /files/stage/<session_id>           — list staged files for a session
    DELETE /files/stage/<session_id>           — clear all staged files for a session
    DELETE /files/stage/<session_id>/<filename>— remove one staged file
    POST   /files/rename-zip                   — build ZIP from staged session (returns download_url)

The existing GET /files/download/<path> route already serves the completed ZIP.
"""

import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

# Adjust / remove this Blueprint if your routes are registered directly on `app`.
files_bp = Blueprint("files_rename_zip", __name__)

FILE_STORAGE_DIR = os.getenv("FILE_STORAGE_DIR", "./storage")

# ---------------------------------------------------------------------------
# Auth helper — replace with your existing API key decorator/check
# ---------------------------------------------------------------------------

def _check_api_key():
    """
    Returns a 401 JSON response if the request is missing or has a wrong
    X-API-Key header, or None if auth passes.
    Adapt this to match how your existing /files/* routes authenticate.
    """
    expected = os.getenv("FILE_UPLOAD_API_KEY", "")
    if expected and request.headers.get("X-API-Key") != expected:
        return jsonify({"error": "Unauthorized"}), 401
    return None


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

def _valid_session_id(sid: str) -> bool:
    return bool(_UUID_RE.match(sid))


def _safe_stage_path(session_id: str, filename: str | None = None) -> str | None:
    """
    Return the absolute path for a staging dir / file, or None if the path
    would escape the staging root (path-traversal guard).
    """
    stage_root = os.path.realpath(os.path.join(FILE_STORAGE_DIR, "staging"))
    session_dir = os.path.realpath(os.path.join(stage_root, session_id))
    if not session_dir.startswith(stage_root + os.sep) and session_dir != stage_root:
        return None
    if filename is None:
        return session_dir
    safe_name = secure_filename(filename)
    if not safe_name:
        return None
    file_path = os.path.realpath(os.path.join(session_dir, safe_name))
    if not file_path.startswith(session_dir + os.sep):
        return None
    return file_path


# ---------------------------------------------------------------------------
# Staging endpoints
# ---------------------------------------------------------------------------

@files_bp.route("/files/stage/<session_id>", methods=["POST"])
def stage_file(session_id):
    auth_err = _check_api_key()
    if auth_err:
        return auth_err

    if not _valid_session_id(session_id):
        return jsonify({"error": "Invalid session ID"}), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file provided"}), 400

    session_dir = _safe_stage_path(session_id)
    if session_dir is None:
        return jsonify({"error": "Invalid session ID"}), 400

    safe_name = secure_filename(f.filename)
    if not safe_name:
        return jsonify({"error": "Invalid filename"}), 400

    os.makedirs(session_dir, exist_ok=True)
    dest = os.path.join(session_dir, safe_name)
    f.save(dest)

    return jsonify({
        "filename": safe_name,
        "size_bytes": os.path.getsize(dest),
        "staged": True,
    })


@files_bp.route("/files/stage/<session_id>", methods=["GET"])
def list_staged(session_id):
    auth_err = _check_api_key()
    if auth_err:
        return auth_err

    if not _valid_session_id(session_id):
        return jsonify({"error": "Invalid session ID"}), 400

    session_dir = _safe_stage_path(session_id)
    if session_dir is None or not os.path.isdir(session_dir):
        return jsonify({"files": [], "session_id": session_id})

    files = []
    for name in sorted(os.listdir(session_dir)):
        path = os.path.join(session_dir, name)
        if os.path.isfile(path):
            files.append({"name": name, "size_bytes": os.path.getsize(path)})

    return jsonify({"files": files, "session_id": session_id})


@files_bp.route("/files/stage/<session_id>", methods=["DELETE"])
def clear_staged(session_id):
    auth_err = _check_api_key()
    if auth_err:
        return auth_err

    if not _valid_session_id(session_id):
        return jsonify({"error": "Invalid session ID"}), 400

    session_dir = _safe_stage_path(session_id)
    if session_dir and os.path.isdir(session_dir):
        shutil.rmtree(session_dir)

    return jsonify({"cleared": True})


@files_bp.route("/files/stage/<session_id>/<filename>", methods=["DELETE"])
def remove_staged_file(session_id, filename):
    auth_err = _check_api_key()
    if auth_err:
        return auth_err

    if not _valid_session_id(session_id):
        return jsonify({"error": "Invalid session ID"}), 400

    file_path = _safe_stage_path(session_id, filename)
    if file_path is None:
        return jsonify({"error": "Invalid filename"}), 400

    if os.path.isfile(file_path):
        os.unlink(file_path)

    return jsonify({"removed": True})


# ---------------------------------------------------------------------------
# Processing helpers (same logic as Streamlit page — runs on flask server)
# ---------------------------------------------------------------------------

VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv", ".wmv", ".flv", ".mpeg", ".mpg"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp"}


def _image_date_from_bytes(data: bytes):
    try:
        from PIL import Image, ExifTags
        with Image.open(io.BytesIO(data)) as img:
            exif_data = img._getexif()
            if not exif_data:
                return None
            tag_map = {v: k for k, v in ExifTags.TAGS.items()}
            dto_tag = tag_map.get("DateTimeOriginal")
            if dto_tag and dto_tag in exif_data:
                return datetime.strptime(exif_data[dto_tag], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        pass
    return None


def _video_date_from_bytes(data: bytes, suffix: str):
    tmp_path = None
    try:
        from hachoir.metadata import extractMetadata
        from hachoir.parser import createParser
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        parser = createParser(tmp_path)
        if parser:
            with parser:
                metadata = extractMetadata(parser)
            if metadata:
                for field in ("creation_date", "date_time_original"):
                    val = metadata.get(field)
                    if val:
                        if hasattr(val, "timestamp"):
                            return val.timestamp()
                        if isinstance(val, str):
                            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
                                try:
                                    return datetime.strptime(val, fmt).timestamp()
                                except ValueError:
                                    pass
    except Exception:
        pass
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return None


def _taken_time(data: bytes, ext: str, index: int) -> float:
    if ext in IMAGE_EXTS:
        t = _image_date_from_bytes(data)
    elif ext in VIDEO_EXTS:
        t = _video_date_from_bytes(data, ext)
    else:
        t = None
    return t if t is not None else float(index)


def _resize_image_bytes(data: bytes, max_px: int = 1920) -> bytes:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
            if max(w, h) <= max_px:
                return data
            scale = max_px / max(w, h)
            new_size = (int(w * scale), int(h * scale))
            exif = img.info.get("exif", b"")
            resized = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            fmt = img.format or "JPEG"
            save_kwargs = {"format": fmt}
            if exif:
                save_kwargs["exif"] = exif
            resized.save(buf, **save_kwargs)
            return buf.getvalue()
    except Exception:
        return data


def _build_plan_and_zip(files_list: list[tuple[str, bytes]], use_upload_order: bool):
    """files_list: [(filename, bytes), ...]  Returns (zip_bytes, n_images, n_videos)."""
    entries = []
    for i, (name, data) in enumerate(files_list):
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
            continue
        if ext in IMAGE_EXTS:
            data = _resize_image_bytes(data)
        sort_key = float(i) if use_upload_order else _taken_time(data, ext, i)
        entries.append((name, ext, data, sort_key))

    entries.sort(key=lambda x: x[3])

    plan = []
    video_count = 0
    image_count = 0
    current_prefix = None

    for name, ext, data, _ in entries:
        if ext in VIDEO_EXTS:
            video_count += 1
            current_prefix = f"{video_count:02d}"
            image_count = 0
        elif ext in IMAGE_EXTS and current_prefix:
            image_count += 1
            if image_count == 1:
                new_name = f"{current_prefix}_INSTALL{ext}"
            else:
                letter = chr(ord("A") + image_count - 2)
                new_name = f"{current_prefix}{letter}{ext}"
            plan.append((new_name, data))

    spool = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for new_name, data in plan:
            zf.writestr(new_name, data)
    spool.seek(0)
    return spool.read(), len(plan), video_count


# ---------------------------------------------------------------------------
# Rename-zip endpoint
# ---------------------------------------------------------------------------

@files_bp.route("/files/rename-zip", methods=["POST"])
def rename_zip():
    """
    Expects JSON body:
        {"session_id": "<uuid>", "sort_order": "date_taken" | "upload_order"}

    Reads staged files for the session, runs rename+zip, stores the ZIP under
    FILE_STORAGE_DIR/zips/, and returns:
        {"filename", "size_bytes", "download_url", "images_renamed", "video_markers"}

    The download_url is served by the existing GET /files/download/<path> route.
    """
    auth_err = _check_api_key()
    if auth_err:
        return auth_err

    body = request.get_json(silent=True) or {}
    file_paths = body.get("file_paths")
    sort_order = body.get("sort_order", "date_taken")
    files_list = []
    use_upload_order = sort_order == "upload_order"

    if file_paths:
        # Accept a list of arbitrary file paths to zip
        if not isinstance(file_paths, list) or not all(isinstance(p, str) for p in file_paths):
            return jsonify({"error": "file_paths must be a list of strings"}), 400
        for path in file_paths:
            # Security: Only allow files under allowed base directories
            abs_path = os.path.realpath(path)
            allowed_roots = [os.path.realpath("media_storage"), os.path.realpath("generated_data")]
            if not any(abs_path.startswith(root) for root in allowed_roots):
                return jsonify({"error": f"File path not allowed: {path}"}), 403
            if not os.path.isfile(abs_path):
                return jsonify({"error": f"File not found: {path}"}), 404
            with open(abs_path, "rb") as fh:
                files_list.append((os.path.basename(abs_path), fh.read()))
    else:
        # Fallback to session-based staging logic
        session_id = body.get("session_id", "")
        if not _valid_session_id(session_id):
            return jsonify({"error": "Invalid or missing session_id"}), 400
        session_dir = _safe_stage_path(session_id)
        if session_dir is None or not os.path.isdir(session_dir):
            return jsonify({"error": "No staged files found for this session"}), 404
        for name in sorted(os.listdir(session_dir)):
            path = os.path.join(session_dir, name)
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    files_list.append((name, fh.read()))

    if not files_list:
        return jsonify({"error": "No files found to zip"}), 404

    try:
        zip_bytes, n_images, n_videos = _build_plan_and_zip(files_list, use_upload_order)
    except Exception as exc:
        return jsonify({"error": f"Processing failed: {exc}"}), 500

    if n_images == 0:
        return jsonify({
            "error": (
                "No renameable images found. "
                "Stage at least one video marker alongside your images."
            )
        }), 422

    # Store ZIP under FILE_STORAGE_DIR/zips/
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    short_hash = hashlib.md5(zip_bytes[:512]).hexdigest()[:6]
    zip_filename = f"renamed_media_{ts}_{short_hash}.zip"
    zip_dir = os.path.join(FILE_STORAGE_DIR, "zips")
    os.makedirs(zip_dir, exist_ok=True)
    zip_path = os.path.join(zip_dir, zip_filename)
    with open(zip_path, "wb") as fh:
        fh.write(zip_bytes)

    # Clean up staging dir if used
    if not file_paths:
        shutil.rmtree(session_dir, ignore_errors=True)

    return jsonify({
        "filename": zip_filename,
        "size_bytes": len(zip_bytes),
        "download_url": f"/files/download/zips/{zip_filename}",
        "images_renamed": n_images,
        "video_markers": n_videos,
    })
