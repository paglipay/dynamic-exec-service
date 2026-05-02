"""NearestSchoolMatcherPlugin — matches slack_files docs to the nearest geocoded school.

Given:
  - a list of school records (from r1_data, each with latitude/longitude/Site/Loc Code)
  - a list of image/file docs (from slack_files, each with GPS in one of several fallback fields)

Returns a deduplicated list of project name strings ("{Site} ({Loc Code})") for schools
that have at least one matched image, plus match detail for diagnostics.

GPS fallback priority for slack_files docs (mirrors restore_exif.py):
  1. doc.gps.lat / doc.gps.lon
  2. doc.gps.latitude / doc.gps.longitude
  3. doc.latitude / doc.lon
  4. doc.location.lat / doc.location.lng (or .longitude)
"""

from __future__ import annotations

import math
from typing import Any


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _get_doc_gps(doc: dict) -> tuple[float | None, float | None]:
    """Extract lat/lon from a slack_files document using all fallback paths."""
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
    lat: float,
    lon: float,
    schools: list[dict],
    max_km: float,
) -> tuple[dict | None, float]:
    best: dict | None = None
    best_dist = float("inf")
    for school in schools:
        try:
            slat = float(school["latitude"])
            slon = float(school["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        d = _haversine_km(lat, lon, slat, slon)
        if d < best_dist:
            best_dist = d
            best = school
    if best is not None and best_dist <= max_km:
        return best, best_dist
    return None, best_dist


class NearestSchoolMatcherPlugin:
    """Match slack_files image docs to the nearest geocoded school.

    Constructor args:
        max_distance_km (float): Haversine threshold in km. Default 2.0.
        site_field (str):        Field name for site label in school records. Default "Site".
        loc_code_field (str):    Field name for location code in school records. Default "Loc Code".

    Method:
        match_to_schools(schools, image_docs, skip_downloaded)
    """

    def __init__(
        self,
        max_distance_km: float = 2.0,
        site_field: str = "Site",
        loc_code_field: str = "Loc Code",
    ) -> None:
        try:
            self._max_km = float(max_distance_km)
        except (TypeError, ValueError):
            raise ValueError("max_distance_km must be a number")
        if self._max_km <= 0:
            raise ValueError("max_distance_km must be positive")
        self._site_field = site_field if isinstance(site_field, str) and site_field else "Site"
        self._loc_field = loc_code_field if isinstance(loc_code_field, str) and loc_code_field else "Loc Code"

    def match_to_schools(
        self,
        schools: list[dict[str, Any]],
        image_docs: list[dict[str, Any]],
        skip_downloaded: bool = False,
    ) -> dict[str, Any]:
        """Match each image doc to its nearest school and return deduplicated project names.

        Args:
            schools:          List of school records from r1_data (need latitude, longitude,
                              Site, and Loc Code fields).
            image_docs:       List of slack_files documents.
            skip_downloaded:  When True, skip docs where download_status == "success".

        Returns:
            {
                "project_names":   ["Site A (1234)", ...],   # deduplicated, sorted
                "matched_count":   int,
                "no_gps_count":    int,
                "no_match_count":  int,
                "skipped_count":   int,
                "match_details":   [{"filename": ..., "school_name": ...,
                                     "loc_code": ..., "distance_km": ...}, ...]
            }
        """
        if not isinstance(schools, list):
            raise ValueError("schools must be a list")
        if not isinstance(image_docs, list):
            raise ValueError("image_docs must be a list")

        # Filter out schools missing GPS
        valid_schools = [
            s for s in schools
            if s.get("latitude") is not None and s.get("longitude") is not None
        ]

        project_name_set: set[str] = set()
        match_details: list[dict[str, Any]] = []
        matched = no_gps = no_match = skipped = 0

        for doc in image_docs:
            if not isinstance(doc, dict):
                continue

            # --skip-downloaded equivalent
            if skip_downloaded and doc.get("download_status") == "success":
                skipped += 1
                continue

            filename = (
                doc.get("filename") or doc.get("name") or doc.get("title") or "(unknown)"
            )

            lat, lon = _get_doc_gps(doc)
            if lat is None:
                no_gps += 1
                continue

            school, dist_km = _find_nearest(lat, lon, valid_schools, self._max_km)
            if school is None:
                no_match += 1
                continue

            site = str(school.get(self._site_field) or "").strip()
            loc_code = str(school.get(self._loc_field) or "").strip()
            if not site or not loc_code:
                no_match += 1
                continue

            project_name = f"{site} ({loc_code})"
            project_name_set.add(project_name)
            matched += 1
            match_details.append({
                "filename": filename,
                "school_name": school.get("School Name") or school.get("name") or site,
                "loc_code": loc_code,
                "site": site,
                "distance_km": round(dist_km, 4),
                "project_name": project_name,
            })

        return {
            "project_names": sorted(project_name_set),
            "matched_count": matched,
            "no_gps_count": no_gps,
            "no_match_count": no_match,
            "skipped_count": skipped,
        }
