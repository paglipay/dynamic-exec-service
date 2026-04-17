"""Media storage plugin for file management, staging, and rename-zip operations."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.utils import secure_filename

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class MediaStoragePlugin:
    """Manage files in a confined media storage directory.

    Constructor args:
        base_dir (str): Root storage directory. Defaults to "generated_data".
    """

    VIDEO_EXTS: frozenset[str] = frozenset(
        {".mov", ".mp4", ".avi", ".mkv", ".wmv", ".flv", ".mpeg", ".mpg"}
    )
    IMAGE_EXTS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp"}
    )

    def __init__(self, base_dir: str = "generated_data") -> None:
        if not isinstance(base_dir, str) or not base_dir.strip():
            raise ValueError("base_dir must be a non-empty string")
        self._base = Path(base_dir).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    def _sanitize_filename(self, name: str) -> str:
        """Return a filesystem-safe filename, preserving the original extension."""
        stem = Path(name).stem or "file"
        suffix = Path(name).suffix.lower()
        safe_stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._") or "file"
        return f"{safe_stem}{suffix}"

    def _resolve_path(self, folder: str, filename: str) -> Path:
        """Resolve a path confined within the storage base directory."""
        if folder:
            if not re.fullmatch(r"[A-Za-z0-9_\-/]+", folder):
                raise ValueError(
                    "folder may only contain letters, numbers, hyphens, "
                    "underscores, and forward slashes"
                )
            folder_path = (self._base / folder).resolve()
            try:
                folder_path.relative_to(self._base)
            except ValueError as exc:
                raise ValueError("Invalid folder path") from exc
        else:
            folder_path = self._base

        dest = (folder_path / filename).resolve()
        try:
            dest.relative_to(self._base)
        except ValueError as exc:
            raise ValueError("Invalid file path") from exc
        return dest

    def _valid_session_id(self, sid: str) -> bool:
        return bool(_UUID_RE.match(sid))

    def _stage_root(self) -> Path:
        return (self._base / "staging").resolve()

    def _safe_stage_path(self, session_id: str, filename: str | None = None) -> Path | None:
        """Return the absolute Path for a staging dir or file, or None if out-of-bounds."""
        stage_root = self._stage_root()
        session_dir = (stage_root / session_id).resolve()
        try:
            session_dir.relative_to(stage_root)
        except ValueError:
            return None
        if filename is None:
            return session_dir
        safe_name = secure_filename(filename)
        if not safe_name:
            return None
        file_path = (session_dir / safe_name).resolve()
        try:
            file_path.relative_to(session_dir)
        except ValueError:
            return None
        return file_path

    def _image_date_from_bytes(self, data: bytes) -> float | None:
        """Extract DateTimeOriginal from EXIF as a UTC timestamp, or None."""
        try:
            from PIL import Image, ExifTags  # type: ignore[import-not-found]

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

    def _video_date_from_bytes(self, data: bytes, suffix: str) -> float | None:
        """Extract creation date from a video's metadata via hachoir, or None."""
        tmp_path = None
        try:
            from hachoir.metadata import extractMetadata  # type: ignore[import-not-found]
            from hachoir.parser import createParser  # type: ignore[import-not-found]

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

    def _media_taken_time(self, data: bytes, ext: str, upload_index: int) -> float:
        """Return the best available sort key for a media file."""
        t: float | None = None
        if ext in self.IMAGE_EXTS:
            t = self._image_date_from_bytes(data)
        elif ext in self.VIDEO_EXTS:
            t = self._video_date_from_bytes(data, ext)
        return t if t is not None else float(upload_index)

    def _resize_image_bytes(self, data: bytes, max_px: int = 1920) -> bytes:
        """Downsample an image to max_px on its longest side, preserving EXIF."""
        try:
            from PIL import Image  # type: ignore[import-not-found]

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
                save_kwargs: dict[str, Any] = {"format": fmt}
                if exif:
                    save_kwargs["exif"] = exif
                resized.save(buf, **save_kwargs)
                return buf.getvalue()
        except Exception:
            return data

    def _build_rename_zip(
        self,
        files_list: list[tuple[str, bytes]],
        use_upload_order: bool,
    ) -> tuple[bytes, int, int]:
        """Sort, rename, and zip uploaded media files. Returns (zip_bytes, image_count, video_count)."""
        entries: list[tuple[str, str, bytes, float]] = []
        for i, (name, data) in enumerate(files_list):
            ext = Path(name).suffix.lower()
            if ext not in self.IMAGE_EXTS and ext not in self.VIDEO_EXTS:
                continue
            if ext in self.IMAGE_EXTS:
                data = self._resize_image_bytes(data)
            sort_key = float(i) if use_upload_order else self._media_taken_time(data, ext, i)
            entries.append((name, ext, data, sort_key))

        entries.sort(key=lambda x: x[3])

        plan: list[tuple[str, bytes]] = []
        video_count = 0
        image_count = 0
        image_plan_count = 0
        current_prefix: str | None = None

        for _original_name, ext, data, _ in entries:
            if ext in self.VIDEO_EXTS:
                video_count += 1
                current_prefix = f"{video_count:02d}"
                image_count = 0
                new_name = f"{current_prefix}{ext}"
                plan.append((new_name, data))
            elif ext in self.IMAGE_EXTS and current_prefix is not None:
                image_count += 1
                image_plan_count += 1
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
        return spool.read(), image_plan_count, video_count

    def _save_zip(self, zip_bytes: bytes) -> tuple[str, str]:
        """Persist zip_bytes under the zips/ subdirectory. Returns (filename, relative_path)."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_hash = hashlib.md5(zip_bytes[:512]).hexdigest()[:6]  # nosec — not crypto
        zip_filename = f"renamed_media_{ts}_{short_hash}.zip"
        zip_dir = (self._base / "zips").resolve()
        zip_dir.mkdir(parents=True, exist_ok=True)
        zip_path = zip_dir / zip_filename
        zip_path.write_bytes(zip_bytes)
        relative = zip_path.relative_to(self._base).as_posix()
        return zip_filename, relative

    # ---------------------------------------------------------------------------
    # Public methods — allowlisted for /execute, /workflow, and OpenAI tool calls
    # ---------------------------------------------------------------------------

    def list_files(self, folder: str = "") -> dict[str, Any]:
        """List files and directories inside the storage root or a subfolder.

        Args:
            folder: Optional subdirectory relative to base_dir (e.g. "videos/2026").
        """
        if folder:
            if not re.fullmatch(r"[A-Za-z0-9_\-/]+", folder):
                raise ValueError(
                    "folder may only contain letters, numbers, hyphens, "
                    "underscores, and forward slashes"
                )
            try:
                scan_dir = (self._base / folder).resolve()
                scan_dir.relative_to(self._base)
            except ValueError:
                raise ValueError("Invalid folder path")
        else:
            scan_dir = self._base

        if not scan_dir.exists():
            raise FileNotFoundError(f"Folder not found: {folder or '/'}")

        entries = []
        for item in sorted(scan_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            relative = item.relative_to(self._base).as_posix()
            entry: dict[str, Any] = {
                "name": item.name,
                "path": relative,
                "type": "file" if item.is_file() else "directory",
            }
            if item.is_file():
                entry["size_bytes"] = item.stat().st_size
                entry["download_url"] = f"/files/download/{relative}"
            entries.append(entry)

        return {
            "status": "success",
            "folder": folder or "/",
            "count": len(entries),
            "files": entries,
        }

    def delete_file(self, relative_path: str) -> dict[str, Any]:
        """Delete a stored file by its path relative to the storage root.

        Args:
            relative_path: Path to the file relative to base_dir.
        """
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("relative_path must be a non-empty string")
        try:
            target = (self._base / relative_path).resolve()
            target.relative_to(self._base)
        except ValueError:
            raise ValueError("Invalid file path")

        if not target.exists():
            raise FileNotFoundError("File not found")
        if not target.is_file():
            raise ValueError("Path is not a file")

        target.unlink()
        return {"status": "success", "deleted": relative_path}

    def list_staged(self, session_id: str) -> dict[str, Any]:
        """List files currently staged for a session.

        Args:
            session_id: UUID identifying the staging session.
        """
        if not self._valid_session_id(session_id):
            raise ValueError("Invalid session ID")
        session_dir = self._safe_stage_path(session_id)
        if session_dir is None or not session_dir.is_dir():
            return {"files": [], "session_id": session_id}
        files = [
            {"name": p.name, "size_bytes": p.stat().st_size}
            for p in sorted(session_dir.iterdir())
            if p.is_file()
        ]
        return {"files": files, "session_id": session_id}

    def clear_staged(self, session_id: str) -> dict[str, Any]:
        """Remove all staged files for a session.

        Args:
            session_id: UUID identifying the staging session.
        """
        if not self._valid_session_id(session_id):
            raise ValueError("Invalid session ID")
        session_dir = self._safe_stage_path(session_id)
        if session_dir is not None and session_dir.is_dir():
            shutil.rmtree(str(session_dir))
        return {"cleared": True}

    def remove_staged_file(self, session_id: str, filename: str) -> dict[str, Any]:
        """Remove a single file from a staging session.

        Args:
            session_id: UUID identifying the staging session.
            filename: Name of the file to remove.
        """
        if not self._valid_session_id(session_id):
            raise ValueError("Invalid session ID")
        file_path = self._safe_stage_path(session_id, filename)
        if file_path is None:
            raise ValueError("Invalid filename")
        if file_path.is_file():
            file_path.unlink()
        return {"removed": True}

    def rename_zip_from_staged(
        self,
        session_id: str,
        sort_order: str = "date_taken",
    ) -> dict[str, Any]:
        """Build a rename-zip archive from staged files for a session.

        Sorts and renames images grouped by video markers, zips the result,
        saves it under zips/, and removes the staging directory on success.

        Args:
            session_id: UUID identifying the staging session.
            sort_order: "date_taken" (default) or "upload_order".
        """
        if not self._valid_session_id(session_id):
            raise ValueError("Invalid or missing session_id")
        session_dir = self._safe_stage_path(session_id)
        if session_dir is None or not session_dir.is_dir():
            raise FileNotFoundError("No staged files found for this session")

        files_list = [
            (p.name, p.read_bytes())
            for p in sorted(session_dir.iterdir())
            if p.is_file()
        ]
        if not files_list:
            raise FileNotFoundError("No staged files found for this session")

        use_upload_order = sort_order == "upload_order"
        zip_bytes, n_images, n_videos = self._build_rename_zip(files_list, use_upload_order)

        if not zip_bytes or n_images == 0:
            raise ValueError(
                "No renameable images found. "
                "Stage at least one video marker alongside your images."
            )

        zip_filename, relative = self._save_zip(zip_bytes)
        shutil.rmtree(str(session_dir), ignore_errors=True)

        return {
            "status": "success",
            "filename": zip_filename,
            "size_bytes": len(zip_bytes),
            "download_url": f"/files/download/{relative}",
            "images_renamed": n_images,
            "video_markers": n_videos,
        }

    def rename_zip_from_file_data(
        self,
        files_list: list[tuple[str, bytes]],
        sort_order: str = "date_taken",
    ) -> dict[str, Any]:
        """Build a rename-zip archive from in-memory file data (multipart upload path).

        Args:
            files_list: List of (filename, bytes) tuples.
            sort_order: "date_taken" (default) or "upload_order".
        """
        if not files_list:
            raise ValueError("No files provided")

        use_upload_order = sort_order == "upload_order"
        zip_bytes, n_images, n_videos = self._build_rename_zip(files_list, use_upload_order)

        if not zip_bytes or n_images == 0:
            raise ValueError(
                "No renameable images found. "
                "Upload at least one video marker alongside your images."
            )

        zip_filename, relative = self._save_zip(zip_bytes)

        return {
            "status": "success",
            "filename": zip_filename,
            "size_bytes": len(zip_bytes),
            "download_url": f"/files/download/{relative}",
            "images_renamed": n_images,
            "video_markers": n_videos,
        }

    def zip_files(
        self,
        file_paths: list[str],
        zip_name: str = "",
    ) -> dict[str, Any]:
        """Zip existing files already on disk in media_storage into an archive.

        Use this when files are already stored in media_storage and you want to
        bundle them without renaming. The resulting ZIP is saved under zips/.

        Args:
            file_paths: List of relative paths within media_storage (e.g. ["01.mp4", "01A.jpg"]).
            zip_name: Optional custom ZIP filename (without .zip). Defaults to timestamped name.

        Returns:
            dict with status, filename, size_bytes, download_url, and local_path
            (local_path is the full relative path for upload_local_file).
        """
        if not isinstance(file_paths, list) or not file_paths:
            raise ValueError("file_paths must be a non-empty list")

        # Validate and resolve all paths before touching the filesystem
        resolved: list[tuple[str, Path]] = []
        for rel in file_paths:
            if not isinstance(rel, str) or not rel.strip():
                raise ValueError(f"Invalid entry in file_paths: {rel!r}")
            candidate = (self._base / rel).resolve()
            try:
                candidate.relative_to(self._base)
            except ValueError:
                raise ValueError(f"Path escapes base directory: {rel!r}")
            if not candidate.is_file():
                raise FileNotFoundError(f"File not found in media_storage: {rel}")
            resolved.append((rel, candidate))

        # Build the zip in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arcname, abs_path in resolved:
                zf.write(abs_path, arcname=Path(arcname).name)
        zip_bytes = buf.getvalue()

        # Persist using existing _save_zip helper (saves under media_storage/zips/)
        if zip_name and isinstance(zip_name, str) and zip_name.strip():
            safe_stem = re.sub(r"[^A-Za-z0-9_\-]", "_", zip_name.strip())
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            zip_filename = f"{safe_stem}_{ts}.zip"
            zip_dir = (self._base / "zips").resolve()
            zip_dir.mkdir(parents=True, exist_ok=True)
            zip_path = zip_dir / zip_filename
            zip_path.write_bytes(zip_bytes)
            relative = zip_path.relative_to(self._base).as_posix()
        else:
            zip_filename, relative = self._save_zip(zip_bytes)

        local_path = f"{self._base.name}/{relative}"
        return {
            "status": "success",
            "filename": zip_filename,
            "size_bytes": len(zip_bytes),
            "download_url": f"/files/download/{relative}",
            "local_path": local_path,
            "files_zipped": len(resolved),
        }
