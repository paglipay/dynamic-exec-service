"""SlackImageRestorePlugin — downloads Slack images/videos, restores EXIF, and places them into
project folders. Mirrors restore_exif.py as a callable plugin for the /workflow endpoint.

Constructor args:
    projects_root (str):      Root path containing project subdirectories.
    image_subfolder (str):    Subfolder within each project for images.
                              Default: "Camera/Design/Pictures"
    video_subfolder (str):    Subfolder within each project for videos.
                              Default: same as image_subfolder.
    staging_dir (str):        Directory to cache downloaded files before placing.
                              Default: "generated_data". Files are moved after placement.
    max_distance_km (float):  Haversine threshold in km. Default 2.0.
    site_field (str):         School record field for site label. Default "Site".
    loc_code_field (str):     School record field for loc code. Default "Loc Code".
    slack_bot_token (str):    Slack bot token. Falls back to SLACK_BOT_TOKEN env var.
    update_mongo (bool):      Write download_status=success back to MongoDB. Default False.
    mongodb_uri (str):        MongoDB URI (required when update_mongo=True).
    database (str):           MongoDB database name (required when update_mongo=True).
    mongo_collection (str):   Collection to update. Default "slack_files".

Method:
    restore_and_place(schools, image_docs, skip_downloaded=False, dry_run=False)
      Returns summary counts + project_names (for chaining into deploy_template_to_projects).
"""

from __future__ import annotations

import base64
import io
import math
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib import error as urlerror, request as urlrequest
from urllib.parse import urljoin

try:
    import piexif as _piexif  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    _piexif = None  # type: ignore[assignment]

try:
    from PIL import Image as _PILImage  # type: ignore[import-not-found]
    from PIL.ExifTags import TAGS as _EXIF_TAGS
except ImportError:  # pragma: no cover
    _PILImage = None  # type: ignore[assignment]
    _EXIF_TAGS = {}  # type: ignore[assignment]

# ── Constants ─────────────────────────────────────────────────────────────────

_JPEG_MAGIC = b"\xff\xd8\xff"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}
_JPEG_EXTS = {".jpg", ".jpeg"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".3gp", ".webm"}
_TIMESTAMP_RE = re.compile(r"_\d{8,}$")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _get_doc_gps(doc: dict) -> tuple[float | None, float | None]:
    """Extract lat/lon from a slack_files doc using all fallback paths (mirrors restore_exif.py)."""
    # Priority 1: nested gps object
    gps = doc.get("gps") or {}
    if isinstance(gps, dict):
        lat = gps.get("lat") or gps.get("latitude")
        lon = gps.get("lon") or gps.get("longitude")
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon)
            except (TypeError, ValueError):
                pass

    # Priority 2: top-level latitude/longitude or lat/lon
    lat = doc.get("latitude") or doc.get("lat")
    lon = doc.get("longitude") or doc.get("lon")
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            pass

    # Priority 3: nested location object
    location = doc.get("location")
    if isinstance(location, dict):
        lat = location.get("lat") or location.get("latitude")
        lon = location.get("lng") or location.get("lon") or location.get("longitude")
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon)
            except (TypeError, ValueError):
                pass

    return None, None


def _find_nearest(
    lat: float, lon: float, schools: list[dict], max_km: float
) -> tuple[dict | None, float]:
    best: dict | None = None
    best_dist = float("inf")
    for s in schools:
        try:
            slat = float(s["latitude"])
            slon = float(s["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        d = _haversine_km(lat, lon, slat, slon)
        if d < best_dist:
            best_dist = d
            best = s
    if best is not None and best_dist <= max_km:
        return best, best_dist
    return None, best_dist


def _clean_filename(name: str) -> str:
    """Strip trailing Slack-appended Unix timestamps from the file stem."""
    p = Path(name)
    stem = _TIMESTAMP_RE.sub("", p.stem)
    return stem + p.suffix


# ── Download (preserves Authorization across redirects) ───────────────────────

class _NoRedirectHandler(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # handled manually


_no_redirect_opener = urlrequest.build_opener(_NoRedirectHandler())


def _download_url(url: str, token: str, max_bytes: int = 50 * 1024 * 1024) -> bytes | None:
    if not url or not token:
        return None
    current_url = url.strip()
    auth_header = f"Bearer {token.strip()}"
    for _ in range(6):
        req = urlrequest.Request(
            current_url, headers={"Authorization": auth_header}, method="GET"
        )
        try:
            with _no_redirect_opener.open(req, timeout=25) as response:
                status = response.status
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    current_url = urljoin(current_url, location)
                    continue
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    return None
                return data
        except urlerror.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location") if exc.headers else None
                if location:
                    current_url = urljoin(current_url, location)
                    continue
            return None
        except urlerror.URLError:
            return None
    return None


# ── EXIF helpers ──────────────────────────────────────────────────────────────

def _read_gps_from_image(image_bytes: bytes) -> tuple[float | None, float | None]:
    if _PILImage is None or not _EXIF_TAGS:
        return None, None
    try:
        img = _PILImage.open(io.BytesIO(image_bytes))
        exif_raw = img._getexif()
        if not exif_raw:
            return None, None
        for tag_id, value in exif_raw.items():
            if _EXIF_TAGS.get(tag_id) == "GPSInfo":
                def _dms(dms, ref):
                    d, m, s = float(dms[0]), float(dms[1]), float(dms[2])
                    val = d + m / 60 + s / 3600
                    return -val if ref in ("S", "W") else val
                return _dms(value[2], value[1]), _dms(value[4], value[3])
        return None, None
    except Exception:
        return None, None


def _dd_to_dms_rational(dd: float) -> tuple:
    dd = abs(dd)
    deg = int(dd)
    mn = int((dd - deg) * 60)
    sec = round((dd - deg - mn / 60) * 3600 * 100)
    return ((deg, 1), (mn, 1), (sec, 100))


def _inject_exif_from_b64(image_bytes: bytes, exif_b64: str) -> bytes | None:
    if _piexif is None:
        return None
    try:
        exif_bytes = base64.b64decode(exif_b64)
        _piexif.load(exif_bytes)  # validate
        out = io.BytesIO()
        _piexif.insert(exif_bytes, image_bytes, out)
        return out.getvalue()
    except Exception:
        return None


def _inject_gps_exif(image_bytes: bytes, lat: float, lon: float) -> bytes | None:
    if _piexif is None or _PILImage is None:
        return None
    try:
        img = _PILImage.open(io.BytesIO(image_bytes))
        try:
            exif_dict = _piexif.load(img.info.get("exif", b""))
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
        exif_dict["GPS"] = {
            _piexif.GPSIFD.GPSLatitudeRef:  b"N" if lat >= 0 else b"S",
            _piexif.GPSIFD.GPSLatitude:     _dd_to_dms_rational(lat),
            _piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            _piexif.GPSIFD.GPSLongitude:    _dd_to_dms_rational(lon),
        }
        out = io.BytesIO()
        _piexif.insert(_piexif.dump(exif_dict), image_bytes, out)
        return out.getvalue()
    except Exception:
        return None


def _to_jpeg(image_bytes: bytes) -> bytes | None:
    """Convert any PIL-readable image to JPEG bytes."""
    if _PILImage is None:
        return None
    try:
        img = _PILImage.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        return None


# ── Plugin ────────────────────────────────────────────────────────────────────

class SlackImageRestorePlugin:
    """Download Slack images, restore EXIF data, and place into project folders."""

    def __init__(
        self,
        projects_root: str,
        image_subfolder: str = "Camera/Design/Pictures",
        video_subfolder: str | None = None,
        staging_dir: str = "generated_data",
        max_distance_km: float = 2.0,
        site_field: str = "Site",
        loc_code_field: str = "Loc Code",
        slack_bot_token: str | None = None,
        update_mongo: bool = False,
        mongodb_uri: str | None = None,
        database: str | None = None,
        mongo_collection: str = "slack_files",
    ) -> None:
        if not isinstance(projects_root, str) or not projects_root.strip():
            raise ValueError("projects_root must be a non-empty string")
        self._projects_root = Path(projects_root)
        self._image_subfolder = image_subfolder or "Camera/Design/Pictures"
        self._video_subfolder = video_subfolder or self._image_subfolder
        self._staging_dir = Path(staging_dir or "generated_data")
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._max_km = float(max_distance_km)
        except (TypeError, ValueError):
            raise ValueError("max_distance_km must be a number")
        if self._max_km <= 0:
            raise ValueError("max_distance_km must be positive")
        self._site_field = site_field or "Site"
        self._loc_field = loc_code_field or "Loc Code"
        self._token = (slack_bot_token or os.getenv("SLACK_BOT_TOKEN", "")).strip()
        self._update_mongo = bool(update_mongo)
        self._mongodb_uri = (mongodb_uri or os.getenv("MONGODB_URI", "")).strip()
        self._database = (database or os.getenv("MONGODB_DATABASE", "")).strip()
        self._mongo_collection = mongo_collection or "slack_files"

    def _dest_folder(self, site: str, loc_code: str, video: bool = False) -> Path:
        subfolder = self._video_subfolder if video else self._image_subfolder
        return self._projects_root / f"{site} ({loc_code})" / subfolder

    def _mongo_mark_success(self, doc_id: str, placed_info: dict) -> None:
        if not self._update_mongo or not self._mongodb_uri or not doc_id:
            return
        try:
            from pymongo import MongoClient
            from bson import ObjectId
            from datetime import datetime, timezone
            client = MongoClient(self._mongodb_uri, serverSelectionTimeoutMS=5000)
            db_name = self._database or "dynamic_exec"
            col = client[db_name][self._mongo_collection]
            try:
                filter_id: Any = ObjectId(doc_id)
            except Exception:
                filter_id = doc_id
            col.update_one(
                {"_id": filter_id},
                {"$set": {
                    "download_status": "success",
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "placed": placed_info,
                }},
            )
        except Exception:
            pass  # non-fatal

    def _process_video(
        self,
        video_bytes: bytes,
        filename: str,
        valid_schools: list[dict],
        fallback_lat: float | None,
        fallback_lon: float | None,
        dry_run: bool,
    ) -> tuple[str, dict]:
        """GPS-match a video file and place it into the project folder."""
        if not video_bytes:
            return "bad_image", {}

        lat, lon = fallback_lat, fallback_lon
        if lat is None:
            return "no_gps", {}

        school, dist_km = _find_nearest(lat, lon, valid_schools, self._max_km)
        if school is None:
            return "no_match", {}

        site = str(school.get(self._site_field) or "").strip()
        loc_code = str(school.get(self._loc_field) or "").strip()
        if not site or not loc_code:
            return "no_match", {}

        dest_dir = self._dest_folder(site, loc_code, video=True)
        dest_path = dest_dir / filename
        info: dict = {
            "dest": str(dest_path),
            "school": school.get("School Name") or school.get("name") or site,
            "site": site,
            "loc_code": loc_code,
            "distance_km": round(dist_km, 4),
            "project_name": f"{site} ({loc_code})",
        }

        if dry_run:
            return "would_write", info

        if dest_path.exists():
            return "skipped", info

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(video_bytes)
        return "written", info

    def _process_image(
        self,
        image_bytes: bytes,
        filename: str,
        valid_schools: list[dict],
        fallback_lat: float | None,
        fallback_lon: float | None,
        exif_b64: str | None,
        dry_run: bool,
    ) -> tuple[str, dict]:
        """Match, restore EXIF, and save one image. Returns (status, info)."""
        # Ensure JPEG
        if not image_bytes:
            return "bad_image", {}
        if image_bytes[:3] != _JPEG_MAGIC:
            print(
                f"[restore]   Not JPEG — first bytes: {image_bytes[:40]!r} "
                f"(len={len(image_bytes)})",
                flush=True,
            )
            converted = _to_jpeg(image_bytes)
            if not converted:
                return "bad_image", {}
            image_bytes = converted

        # GPS: image EXIF → doc fallback
        lat, lon = _read_gps_from_image(image_bytes)
        if lat is None and fallback_lat is not None:
            lat, lon = fallback_lat, fallback_lon
        if lat is None:
            return "no_gps", {}

        school, dist_km = _find_nearest(lat, lon, valid_schools, self._max_km)
        if school is None:
            return "no_match", {}

        site = str(school.get(self._site_field) or "").strip()
        loc_code = str(school.get(self._loc_field) or "").strip()
        if not site or not loc_code:
            return "no_match", {}

        dest_dir = self._dest_folder(site, loc_code)
        dest_path = dest_dir / filename
        info: dict = {
            "dest": str(dest_path),
            "school": school.get("School Name") or school.get("name") or site,
            "site": site,
            "loc_code": loc_code,
            "distance_km": round(dist_km, 4),
            "project_name": f"{site} ({loc_code})",
        }

        if dry_run:
            return "would_write", info

        if dest_path.exists():
            return "skipped", info

        # Restore EXIF: exif_b64 first, then GPS inject, then as-is
        restored: bytes | None = None
        if exif_b64:
            restored = _inject_exif_from_b64(image_bytes, exif_b64)
        if restored is None:
            existing_lat, _ = _read_gps_from_image(image_bytes)
            if existing_lat is None:
                restored = _inject_gps_exif(image_bytes, lat, lon)
        if restored is None:
            restored = image_bytes

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(restored)
        return "written", info

    def restore_and_place(
        self,
        schools: list[dict[str, Any]],
        image_docs: list[dict[str, Any]],
        skip_downloaded: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Download, restore EXIF, and place each Slack image into its matched project folder.

        Args:
            schools:          List of geocoded school records from r1_data.
            image_docs:       List of slack_files documents (need url_private / url_private_download,
                              gps fields, and optionally exif_b64).
            skip_downloaded:  Skip docs where download_status == "success".
            dry_run:          Report what would happen without writing any files.

        Returns:
            {
              "written": int, "skipped": int, "no_gps": int, "no_match": int,
              "bad_image": int, "download_fail": int, "skipped_downloaded": int,
              "dry_run": bool,
              "project_names": [...],   # deduplicated — pass to deploy_template_to_projects
              "placed": [{"filename", "dest", "school", "loc_code", "distance_km"}, ...]
            }
        """
        if not isinstance(schools, list):
            raise ValueError("schools must be a list")
        if not isinstance(image_docs, list):
            raise ValueError("image_docs must be a list")
        if not self._token:
            raise ValueError(
                "slack_bot_token is required — set in constructor_args or SLACK_BOT_TOKEN env var"
            )

        valid_schools = [
            s for s in schools
            if s.get("latitude") is not None and s.get("longitude") is not None
        ]
        print(f"[restore] {len(image_docs)} image doc(s) to process, {len(valid_schools)} geocoded school(s)", flush=True)

        counts: dict[str, int] = {
            "written": 0, "skipped": 0, "no_gps": 0, "no_match": 0,
            "bad_image": 0, "download_fail": 0, "skipped_downloaded": 0,
            "unsupported": 0,
        }
        placed: list[dict] = []
        project_name_set: set[str] = set()

        for _doc_index, doc in enumerate(image_docs, start=1):
            if not isinstance(doc, dict):
                continue

            if skip_downloaded and doc.get("download_status") == "success":
                counts["skipped_downloaded"] += 1
                continue

            url = (doc.get("url_private_download") or doc.get("url_private") or "").strip()
            filename = (
                doc.get("filename") or doc.get("name") or doc.get("title") or "image"
            )
            ext = Path(filename).suffix.lower()
            fallback_lat, fallback_lon = _get_doc_gps(doc)
            exif_b64 = doc.get("exif_b64")
            doc_id = doc.get("_id")

            print(f"[restore] [{_doc_index}/{len(image_docs)}] Processing: {filename}", flush=True)

            if not url:
                counts["download_fail"] += 1
                continue

            # ── ZIP ───────────────────────────────────────────────────────────
            if ext == ".zip":
                print(f"[restore]   Downloading ZIP...", flush=True)
                zip_bytes = _download_url(url, self._token)
                if not zip_bytes:
                    counts["download_fail"] += 1
                    print(f"[restore]   Download FAILED", flush=True)
                    continue
                staging_path = self._staging_dir / _clean_filename(filename)
                if not dry_run:
                    staging_path.write_bytes(zip_bytes)
                    print(f"[restore]   Staged: {staging_path}", flush=True)
                try:
                    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
                except zipfile.BadZipFile:
                    counts["download_fail"] += 1
                    print(f"[restore]   Bad ZIP file", flush=True)
                    continue

                all_entries = [
                    n for n in zf.namelist()
                    if not Path(n).name.startswith("._")
                ]
                img_entries = [n for n in all_entries if Path(n).suffix.lower() in _IMAGE_EXTS]
                vid_entries = [n for n in all_entries if Path(n).suffix.lower() in _VIDEO_EXTS]
                print(f"[restore]   ZIP contains {len(img_entries)} image(s), {len(vid_entries)} video(s)", flush=True)

                for entry in img_entries:
                    img_bytes = zf.read(entry)
                    img_name = _clean_filename(Path(entry).name)
                    if Path(img_name).suffix.lower() not in _JPEG_EXTS:
                        img_name = Path(img_name).stem + ".jpg"
                    status, info = self._process_image(
                        img_bytes, img_name, valid_schools,
                        fallback_lat, fallback_lon, exif_b64, dry_run,
                    )
                    counts[status if status in counts else "no_match"] += 1
                    if info.get("project_name"):
                        project_name_set.add(info["project_name"])
                    if status in ("written", "would_write"):
                        placed.append({"filename": img_name, **info})
                        print(f"[restore]     {status}: {img_name} → {info.get('project_name')}", flush=True)
                    elif status in ("skipped", "no_gps", "no_match", "bad_image"):
                        print(f"[restore]     {status}: {img_name}", flush=True)
                    if status == "written" and doc_id:
                        self._mongo_mark_success(doc_id, info)

                for entry in vid_entries:
                    vid_bytes = zf.read(entry)
                    vid_name = _clean_filename(Path(entry).name)
                    status, info = self._process_video(
                        vid_bytes, vid_name, valid_schools,
                        fallback_lat, fallback_lon, dry_run,
                    )
                    counts[status if status in counts else "no_match"] += 1
                    if info.get("project_name"):
                        project_name_set.add(info["project_name"])
                    if status in ("written", "would_write"):
                        placed.append({"filename": vid_name, **info})
                        print(f"[restore]     {status}: {vid_name} → {info.get('project_name')}", flush=True)
                    elif status in ("skipped", "no_gps", "no_match"):
                        print(f"[restore]     {status}: {vid_name}", flush=True)
                    if status == "written" and doc_id:
                        self._mongo_mark_success(doc_id, info)

            # ── Direct image ──────────────────────────────────────────────────
            elif ext in _IMAGE_EXTS:
                print(f"[restore]   Downloading image...", flush=True)
                img_bytes = _download_url(url, self._token)
                if not img_bytes:
                    counts["download_fail"] += 1
                    print(f"[restore]   Download FAILED", flush=True)
                    continue
                img_name = _clean_filename(Path(filename).stem + ".jpg")
                staging_path = self._staging_dir / img_name
                if not dry_run:
                    staging_path.write_bytes(img_bytes)
                    print(f"[restore]   Staged: {staging_path}", flush=True)
                status, info = self._process_image(
                    img_bytes, img_name, valid_schools,
                    fallback_lat, fallback_lon, exif_b64, dry_run,
                )
                counts[status if status in counts else "no_match"] += 1
                if info.get("project_name"):
                    project_name_set.add(info["project_name"])
                if status in ("written", "would_write"):
                    placed.append({"filename": img_name, **info})
                    print(f"[restore]   {status}: {img_name} → {info.get('project_name')}", flush=True)
                elif status in ("skipped", "no_gps", "no_match", "bad_image"):
                    print(f"[restore]   {status}: {img_name}", flush=True)
                if status == "written" and doc_id:
                    self._mongo_mark_success(doc_id, info)

            # ── Direct video ──────────────────────────────────────────────────
            elif ext in _VIDEO_EXTS:
                print(f"[restore]   Downloading video...", flush=True)
                vid_bytes = _download_url(url, self._token)
                if not vid_bytes:
                    counts["download_fail"] += 1
                    print(f"[restore]   Download FAILED", flush=True)
                    continue
                vid_name = _clean_filename(filename)
                staging_path = self._staging_dir / vid_name
                if not dry_run:
                    staging_path.write_bytes(vid_bytes)
                    print(f"[restore]   Staged: {staging_path}", flush=True)
                status, info = self._process_video(
                    vid_bytes, vid_name, valid_schools,
                    fallback_lat, fallback_lon, dry_run,
                )
                counts[status if status in counts else "no_match"] += 1
                if info.get("project_name"):
                    project_name_set.add(info["project_name"])
                if status in ("written", "would_write"):
                    placed.append({"filename": vid_name, **info})
                    print(f"[restore]   {status}: {vid_name} → {info.get('project_name')}", flush=True)
                elif status in ("skipped", "no_gps", "no_match"):
                    print(f"[restore]   {status}: {vid_name}", flush=True)
                if status == "written" and doc_id:
                    self._mongo_mark_success(doc_id, info)

            else:
                counts["unsupported"] += 1
                print(f"[restore]   unsupported type: {ext or '(no ext)'}", flush=True)

        print(
            f"[restore] Done — written={counts['written']} skipped={counts['skipped']} "
            f"no_gps={counts['no_gps']} no_match={counts['no_match']} "
            f"fail={counts['download_fail']} unsupported={counts['unsupported']}",
            flush=True,
        )
        return {
            **counts,
            "dry_run": dry_run,
            "project_names": sorted(project_name_set),
            "placed": placed,
        }
