"""
Flask endpoint to add to app.py
================================
Paste this into your Flask app alongside the existing /files/* routes.

Requirements on the Flask server:
  pip install Pillow hachoir

Environment variables (same ones already used by the other /files/* routes):
  FILE_STORAGE_DIR   — root directory where files are stored
  FILE_UPLOAD_API_KEY — shared secret checked in X-API-Key header

The endpoint accepts the same multipart files that the Streamlit page sends via
  POST /files/rename-zip
and returns a JSON response with a download_url the user can click.
"""

import hashlib
import io
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

# ── Flask / auth imports ── adapt to wherever your route registration lives
from flask import Blueprint, jsonify, request

# Reuse / replicate the auth decorator you already have for /files/upload etc.
# e.g.:  from your_auth_module import require_api_key
# If your routes are registered directly on `app`, replace `files_bp.route` with
# `app.route` or whatever pattern you use.

files_bp = Blueprint("files_rename_zip", __name__)  # adjust / remove if not needed

FILE_STORAGE_DIR = os.getenv("FILE_STORAGE_DIR", "./storage")

# ---------------------------------------------------------------------------
# Helpers — identical logic to deploy/heroku/pages/cam_img_rename.py
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
                return datetime.strptime(
                    exif_data[dto_tag], "%Y:%m:%d %H:%M:%S"
                ).timestamp()
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


def _taken_time(data: bytes, ext: str, upload_index: int) -> float:
    if ext in IMAGE_EXTS:
        t = _image_date_from_bytes(data)
    elif ext in VIDEO_EXTS:
        t = _video_date_from_bytes(data, ext)
    else:
        t = None
    return t if t is not None else float(upload_index)


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


def _build_plan_and_zip(files_list, use_upload_order: bool) -> tuple[bytes, int, int]:
    """
    files_list: list of (filename: str, data: bytes)
    Returns (zip_bytes, image_count, video_count)
    """
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

    for original_name, ext, data, _ in entries:
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
# Route
# ---------------------------------------------------------------------------


@files_bp.route("/files/rename-zip", methods=["POST"])
# @require_api_key   ← uncomment / replace with your existing auth decorator
def rename_zip():
    """
    Accepts multipart/form-data with:
      files       — one or more uploaded files (images + videos)
      sort_order  — "date_taken" (default) | "upload_order"

    Returns JSON:
      {
        "filename":     "renamed_media_20260408_153000.zip",
        "size_bytes":   123456,
        "download_url": "/files/download/zips/renamed_media_20260408_153000.zip"
      }
    """
    uploaded = request.files.getlist("files")
    if not uploaded:
        return jsonify({"error": "No files provided"}), 400

    sort_order = request.form.get("sort_order", "date_taken")
    use_upload_order = sort_order == "upload_order"

    files_list = [(f.filename, f.read()) for f in uploaded]

    try:
        zip_bytes, n_images, n_videos = _build_plan_and_zip(files_list, use_upload_order)
    except Exception as exc:
        return jsonify({"error": f"Processing failed: {exc}"}), 500

    if not zip_bytes or n_images == 0:
        return jsonify(
            {
                "error": (
                    "No renameable images found. "
                    "Upload at least one video marker alongside your images."
                )
            }
        ), 422

    # Store the ZIP under FILE_STORAGE_DIR/zips/
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    # Add a short hash to avoid collisions on concurrent requests
    short_hash = hashlib.md5(zip_bytes[:512]).hexdigest()[:6]
    zip_filename = f"renamed_media_{ts}_{short_hash}.zip"
    zip_dir = os.path.join(FILE_STORAGE_DIR, "zips")
    os.makedirs(zip_dir, exist_ok=True)
    zip_path = os.path.join(zip_dir, zip_filename)

    with open(zip_path, "wb") as fh:
        fh.write(zip_bytes)

    return jsonify(
        {
            "filename": zip_filename,
            "size_bytes": len(zip_bytes),
            "download_url": f"/files/download/zips/{zip_filename}",
            "images_renamed": n_images,
            "video_markers": n_videos,
        }
    )
