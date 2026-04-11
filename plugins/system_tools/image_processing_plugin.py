"""Image processing plugin for GPS extraction, object detection, and auto-classification."""

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image
    from PIL.ExifTags import GPSTAGS
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    GPSTAGS = {}  # type: ignore[assignment]

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]

try:
    from pymongo import MongoClient
except ImportError:  # pragma: no cover
    MongoClient = None  # type: ignore[assignment]


_GPS_IFD_TAG = 34853  # ExifIFD pointer to GPS sub-IFD
_SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9_\-]")


class ImageProcessingPlugin:
    """Process images: extract GPS, detect objects via OpenAI Vision, classify, and store.

    Constructor args:
        mongo_uri (str): MongoDB connection URI. Falls back to MONGODB_URI env var.
        mongo_database (str): Database name. Falls back to MONGODB_DATABASE env var.
        mongo_collection_images (str): Collection for image records. Default "images".
        mongo_collection_sites (str): Collection for known site locations. Default "sites".
        base_dir (str): Root directory for storing processed images. Default "media_storage".
        openai_api_key (str): OpenAI API key. Falls back to OPENAI_API_KEY env var.
    """

    IMAGE_EXTS: frozenset[str] = frozenset(
        {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
    )

    _MIME_MAP: dict[str, str] = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }

    _CCTV_KEYWORDS: frozenset[str] = frozenset(
        {
            "dome camera",
            "bullet camera",
            "ptz camera",
            "ip camera",
            "cctv",
            "camera",
            "nvr",
            "dvr",
            "video",
            "surveillance",
        }
    )

    _INTRUSION_KEYWORDS: frozenset[str] = frozenset(
        {
            "pir sensor",
            "pir",
            "motion sensor",
            "alarm keypad",
            "keypad",
            "door sensor",
            "window sensor",
            "siren",
            "alarm",
            "detector",
            "beam sensor",
            "vibration sensor",
            "reed switch",
            "control panel",
            "alarm panel",
            "break glass detector",
        }
    )

    def __init__(
        self,
        mongo_uri: str | None = None,
        mongo_database: str | None = None,
        mongo_collection_images: str = "images",
        mongo_collection_sites: str = "sites",
        base_dir: str = "media_storage",
        openai_api_key: str | None = None,
    ) -> None:
        if not isinstance(base_dir, str) or not base_dir.strip():
            raise ValueError("base_dir must be a non-empty string")
        if not isinstance(mongo_collection_images, str) or not mongo_collection_images.strip():
            raise ValueError("mongo_collection_images must be a non-empty string")
        if not isinstance(mongo_collection_sites, str) or not mongo_collection_sites.strip():
            raise ValueError("mongo_collection_sites must be a non-empty string")

        self._base = Path(base_dir).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._col_images = mongo_collection_images.strip()
        self._col_sites = mongo_collection_sites.strip()

        # MongoDB — optional; methods degrade gracefully when unavailable
        self._mongo_db: Any = None
        resolved_uri = (mongo_uri or os.getenv("MONGODB_URI") or "").strip()
        resolved_db = (mongo_database or os.getenv("MONGODB_DATABASE") or "").strip()
        if resolved_uri and MongoClient is not None:
            client = MongoClient(resolved_uri, serverSelectionTimeoutMS=5000)
            if resolved_db:
                self._mongo_db = client[resolved_db]
            else:
                db_from_uri = resolved_uri.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
                if db_from_uri:
                    self._mongo_db = client[db_from_uri]

        # OpenAI — optional
        self._openai_client: Any = None
        resolved_key = (openai_api_key or os.getenv("OPENAI_API_KEY") or "").strip()
        if resolved_key and OpenAI is not None:
            self._openai_client = OpenAI(api_key=resolved_key)

    # ---------------------------------------------------------------------------
    # GPS helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def _dms_to_decimal(dms: Any, ref: str) -> float:
        """Convert a degrees/minutes/seconds sequence to decimal degrees."""
        degrees, minutes, seconds = dms
        decimal = float(degrees) + float(minutes) / 60.0 + float(seconds) / 3600.0
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal

    def get_lat_lon(self, image_path: str) -> dict:
        """Extract GPS latitude and longitude from image EXIF data.

        Args:
            image_path: Path to the image file.

        Returns:
            {"lat": float | None, "lon": float | None}
        """
        if Image is None:
            raise RuntimeError("Pillow must be installed to extract GPS data")

        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {image_path}")

        gps_ifd: dict[int, Any] = {}
        try:
            with Image.open(path) as img:
                exif = img.getexif()
                if exif:
                    gps_ifd = dict(exif.get_ifd(_GPS_IFD_TAG))
        except Exception:
            return {"lat": None, "lon": None}

        if not gps_ifd:
            return {"lat": None, "lon": None}

        # Map numeric sub-tag IDs to names
        gps_named: dict[str, Any] = {
            GPSTAGS.get(tag, str(tag)): value for tag, value in gps_ifd.items()
        }

        lat_dms = gps_named.get("GPSLatitude")
        lat_ref = gps_named.get("GPSLatitudeRef", "N")
        lon_dms = gps_named.get("GPSLongitude")
        lon_ref = gps_named.get("GPSLongitudeRef", "E")

        if lat_dms is None or lon_dms is None:
            return {"lat": None, "lon": None}

        return {
            "lat": round(self._dms_to_decimal(lat_dms, lat_ref), 7),
            "lon": round(self._dms_to_decimal(lon_dms, lon_ref), 7),
        }

    # ---------------------------------------------------------------------------
    # Site matching
    # ---------------------------------------------------------------------------

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Return great-circle distance between two GPS points in kilometres."""
        r = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (
            math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        )
        return 2 * r * math.asin(math.sqrt(a))

    def find_nearest_site(
        self,
        lat: float,
        lon: float,
        max_distance_km: float = 0.5,
    ) -> dict | None:
        """Find the nearest site from MongoDB within max_distance_km.

        Reads from the configured sites collection. Documents must contain
        at minimum: site_name (str), lat (float), lon (float).

        Args:
            lat: Latitude of the image.
            lon: Longitude of the image.
            max_distance_km: Match radius in kilometres. Default 0.5.

        Returns:
            Site document dict with an added "distance_km" key, or None.
        """
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            raise ValueError("lat and lon must be numeric")
        if not isinstance(max_distance_km, (int, float)) or max_distance_km <= 0:
            raise ValueError("max_distance_km must be a positive number")

        if self._mongo_db is None:
            return None

        sites = list(
            self._mongo_db[self._col_sites].find(
                {},
                {
                    "_id": 0,
                    "site_name": 1,
                    "lat": 1,
                    "lon": 1,
                    "project_type": 1,
                    "client": 1,
                    "address": 1,
                },
            )
        )

        best: dict | None = None
        best_dist = float("inf")
        for site in sites:
            site_lat = site.get("lat")
            site_lon = site.get("lon")
            if site_lat is None or site_lon is None:
                continue
            dist = self._haversine_km(lat, lon, float(site_lat), float(site_lon))
            if dist < best_dist:
                best_dist = dist
                best = {**site, "distance_km": round(dist, 4)}

        if best is None or best_dist > max_distance_km:
            return None

        return best

    # ---------------------------------------------------------------------------
    # Object detection
    # ---------------------------------------------------------------------------

    def detect_objects(self, image_path: str) -> list:
        """Detect security-relevant objects in the image using OpenAI Vision.

        Sends the image to GPT-4o with a structured prompt and parses the
        returned JSON array of label strings.

        Args:
            image_path: Path to the image file.

        Returns:
            List of detected object label strings (lower-case).
        """
        if self._openai_client is None:
            raise RuntimeError(
                "OpenAI client not configured; provide openai_api_key or set OPENAI_API_KEY"
            )

        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {image_path}")

        mime_type = self._MIME_MAP.get(path.suffix.lower(), "image/jpeg")

        with open(path, "rb") as fh:
            image_b64 = base64.b64encode(fh.read()).decode("utf-8")

        prompt = (
            "You are a security system surveyor. Examine this image and identify any "
            "security or surveillance equipment present. "
            "Return ONLY a JSON array of short lowercase label strings — for example: "
            '["dome camera", "pir sensor", "alarm keypad"]. '
            "If nothing security-related is visible, return an empty array []."
        )

        response = self._openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_b64}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            max_tokens=200,
        )

        raw = response.choices[0].message.content.strip()
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, list):
                    return [str(item).lower().strip() for item in result]
            except json.JSONDecodeError:
                pass
        return []

    # ---------------------------------------------------------------------------
    # Classification
    # ---------------------------------------------------------------------------

    def classify_project(self, objects_found: list) -> str:
        """Classify image as CCTV, Intrusion Alarm, or Unclassified.

        When both CCTV and Intrusion objects are detected, CCTV takes priority.

        Args:
            objects_found: List of object label strings from detect_objects().

        Returns:
            "CCTV", "Intrusion Alarm", or "Unclassified"
        """
        if not isinstance(objects_found, list):
            raise ValueError("objects_found must be a list")

        normalised = {
            label.lower().strip()
            for label in objects_found
            if isinstance(label, str)
        }

        has_cctv = any(
            any(kw in label for kw in self._CCTV_KEYWORDS) for label in normalised
        )
        has_intrusion = any(
            any(kw in label for kw in self._INTRUSION_KEYWORDS) for label in normalised
        )

        if has_cctv:
            return "CCTV"
        if has_intrusion:
            return "Intrusion Alarm"
        return "Unclassified"

    # ---------------------------------------------------------------------------
    # Storage and tagging
    # ---------------------------------------------------------------------------

    def _destination_path(self, source: Path, site_name: str, project_type: str) -> Path:
        """Build a destination path under base_dir/{site_name}/{project_type}/."""
        safe_site = _SAFE_PATH_RE.sub("_", site_name.strip()) or "unknown_site"
        safe_type = _SAFE_PATH_RE.sub("_", project_type.strip()) or "Unclassified"
        dest_dir = self._base / safe_site / safe_type
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir / source.name

    def tag_image(self, image_path: str, metadata: dict) -> dict:
        """Upsert an image document in MongoDB.

        Args:
            image_path: Absolute path to the image (used as the unique key).
            metadata: Fields to store alongside the path/filename.

        Returns:
            {"acknowledged": bool, "path": str}
        """
        if not isinstance(image_path, str) or not image_path.strip():
            raise ValueError("image_path must be a non-empty string")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")

        if self._mongo_db is None:
            return {
                "acknowledged": False,
                "path": image_path,
                "reason": "MongoDB not configured",
            }

        doc = {
            "path": image_path,
            "filename": Path(image_path).name,
            **metadata,
        }
        result = self._mongo_db[self._col_images].update_one(
            {"path": image_path},
            {"$set": doc},
            upsert=True,
        )
        return {"acknowledged": result.acknowledged, "path": image_path}

    # ---------------------------------------------------------------------------
    # Full pipeline
    # ---------------------------------------------------------------------------

    def process_and_store(
        self,
        image_path: str,
        max_distance_km: float = 0.5,
        move_file: bool = True,
    ) -> dict:
        """Run the full pipeline on a single image.

        Steps:
            1. Extract EXIF GPS coordinates.
            2. Match to nearest MongoDB site within max_distance_km.
            3. Detect objects via OpenAI Vision.
            4. Classify as CCTV / Intrusion Alarm / Unclassified.
            5. Optionally move file to media_storage/{site_name}/{project_type}/.
            6. Upsert MongoDB image record.

        Args:
            image_path: Path to the source image.
            max_distance_km: GPS site match radius in kilometres. Default 0.5.
            move_file: If True (default), move the file to the classified destination.

        Returns:
            Dict with: original_path, final_path, site_name, project_type,
            lat, lon, objects_found, tagged.
        """
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {image_path}")
        if path.suffix.lower() not in self.IMAGE_EXTS:
            raise ValueError(f"Unsupported image extension: {path.suffix}")

        # 1. GPS
        gps = self.get_lat_lon(image_path)
        lat, lon = gps.get("lat"), gps.get("lon")

        # 2. Site match
        site: dict | None = None
        if lat is not None and lon is not None:
            site = self.find_nearest_site(lat, lon, max_distance_km)
        site_name = site["site_name"] if site else "unmatched"

        # 3. Object detection
        objects_found: list = []
        if self._openai_client is not None:
            objects_found = self.detect_objects(image_path)

        # 4. Classify
        project_type = self.classify_project(objects_found)

        # 5. Move / copy
        dest = self._destination_path(path, site_name, project_type)
        if move_file:
            shutil.move(str(path), str(dest))
            final_path = str(dest)
        else:
            final_path = str(path)

        # 6. Tag
        metadata: dict = {
            "site_name": site_name,
            "project_type": project_type,
            "lat": lat,
            "lon": lon,
            "objects_found": objects_found,
            "distance_km": site.get("distance_km") if site else None,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        tag_result = self.tag_image(final_path, metadata)

        return {
            "original_path": str(path),
            "final_path": final_path,
            "site_name": site_name,
            "project_type": project_type,
            "lat": lat,
            "lon": lon,
            "objects_found": objects_found,
            "tagged": tag_result.get("acknowledged", False),
        }

    def scan_folder(
        self,
        folder_path: str,
        max_distance_km: float = 0.5,
        move_file: bool = True,
    ) -> list:
        """Process all supported images in a folder.

        Args:
            folder_path: Path to the folder containing images.
            max_distance_km: GPS site match radius passed to process_and_store().
            move_file: Whether to move files to their classified destination.

        Returns:
            List of result dicts from process_and_store(), including any per-file errors.
        """
        folder = Path(folder_path)
        if not folder.is_dir():
            raise NotADirectoryError(f"Not a directory: {folder_path}")

        results = []
        for child in sorted(folder.iterdir()):
            if child.is_file() and child.suffix.lower() in self.IMAGE_EXTS:
                try:
                    results.append(
                        self.process_and_store(
                            str(child),
                            max_distance_km=max_distance_km,
                            move_file=move_file,
                        )
                    )
                except Exception as exc:
                    results.append({"original_path": str(child), "error": str(exc)})

        return results
