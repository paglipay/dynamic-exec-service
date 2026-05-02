"""Nominatim/OpenStreetMap geocoding plugin.

Provides a bulk geocoder that annotates records with lat/lon and a parsed civic
address. Mirrors the four-query fallback ladder + civic-address parser used by
paramiko/excel_to_mongodb.py. Uses urllib (no geopy dependency).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any


_STATE_ABBREV = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}


def _parse_nominatim_address(raw):
    """Build a civic address string from a Nominatim address dict."""
    if not isinstance(raw, dict):
        return ""
    house = (raw.get("house_number") or "").strip()
    road = (raw.get("road") or "").strip()
    street = (f"{house} {road}".strip() if house else road)
    city = (raw.get("city") or raw.get("town") or
            raw.get("village") or raw.get("suburb") or "").strip()
    state_full = raw.get("state", "")
    state = _STATE_ABBREV.get(state_full, state_full)
    zipcode = (raw.get("postcode") or "").strip()
    parts = [p for p in [street, city] if p]
    if state and zipcode:
        parts.append(f"{state} {zipcode}")
    elif state:
        parts.append(state)
    elif zipcode:
        parts.append(zipcode)
    return ", ".join(parts)


class NominatimGeocodingPlugin:
    """Bulk geocode records via Nominatim (OpenStreetMap)."""

    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self, user_agent: str = "dynamic-exec-service-geocoder/1.0"):
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("user_agent must be a non-empty string")
        self.user_agent = user_agent.strip()

    # Hookable for testing — override in tests to avoid HTTP.
    def _http_get_json(self, url, timeout):
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
        return json.loads(body)

    def _geocode_one(self, queries, timeout, max_retries):
        """Try the query ladder; return (lat, lon, full_addr, parsed_addr) or Nones."""
        for q in queries:
            if not q:
                continue
            params = urllib.parse.urlencode({"q": q, "format": "json", "addressdetails": "1", "limit": "1"})
            url = f"{self.NOMINATIM_URL}?{params}"
            for attempt in range(max_retries):
                try:
                    data = self._http_get_json(url, timeout=timeout)
                    if isinstance(data, list) and data:
                        first = data[0]
                        try:
                            lat = float(first.get("lat"))
                            lon = float(first.get("lon"))
                        except (TypeError, ValueError):
                            break
                        full_addr = first.get("display_name") or ""
                        parsed = _parse_nominatim_address(first.get("address") or {})
                        return lat, lon, full_addr, parsed
                    break  # no result for this query, try next
                except urllib.error.URLError:
                    time.sleep(2)
                except Exception:
                    break
        return None, None, None, None

    def bulk_geocode_records(
        self,
        records,
        school_field=None,
        address_field=None,
        city_field=None,
        out_lat_field=None,
        out_lon_field=None,
        out_full_address_field=None,
        out_parsed_address_field=None,
        out_timestamp_field=None,
        rate_limit_seconds=None,
        skip_if_lat_set=None,
        timeout_seconds=None,
        max_retries=None,
        max_records=None,
    ):
        """Annotate each record in `records` with lat/lon + parsed address.

        Tries multiple query forms per record (most→least specific):
          1. address + city
          2. school + address + city
          3. school + city
          4. school
        Records already carrying `out_lat_field` are skipped when `skip_if_lat_set=True`.
        """
        # Defaults (with single-payload-dict support like the other plugins).
        defaults = {
            "school_field": "School Name",
            "address_field": "Address",
            "city_field": "City",
            "out_lat_field": "latitude",
            "out_lon_field": "longitude",
            "out_full_address_field": "geo_address",
            "out_parsed_address_field": "Address",
            "out_timestamp_field": "geocoded_at",
            "rate_limit_seconds": 1.1,
            "skip_if_lat_set": True,
            "timeout_seconds": 10,
            "max_retries": 3,
            "max_records": None,
        }

        if isinstance(records, dict):
            payload = records
            recs = payload.get("records")
            for k in defaults:
                v = payload.get(k, locals().get(k))
                if v is None:
                    v = defaults[k]
                defaults[k] = v
        else:
            recs = records
            for k in list(defaults.keys()):
                arg_val = locals().get(k)
                if arg_val is not None:
                    defaults[k] = arg_val

        # Validate
        if not isinstance(recs, list):
            raise ValueError("records must be a list of objects")
        if not isinstance(defaults["rate_limit_seconds"], (int, float)) or defaults["rate_limit_seconds"] < 0:
            raise ValueError("rate_limit_seconds must be a non-negative number")
        if not isinstance(defaults["timeout_seconds"], (int, float)) or defaults["timeout_seconds"] <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        if not isinstance(defaults["max_retries"], int) or defaults["max_retries"] < 1:
            raise ValueError("max_retries must be a positive integer")
        if not isinstance(defaults["skip_if_lat_set"], bool):
            raise ValueError("skip_if_lat_set must be a boolean")
        if defaults["max_records"] is not None and (
            not isinstance(defaults["max_records"], int) or defaults["max_records"] < 1
        ):
            raise ValueError("max_records must be a positive integer when provided")
        for fld in ("school_field", "address_field", "city_field",
                    "out_lat_field", "out_lon_field", "out_full_address_field",
                    "out_parsed_address_field", "out_timestamp_field"):
            v = defaults[fld]
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"{fld} must be a non-empty string")

        out = []
        geocoded = skipped = failed = 0
        now_utc = datetime.now(timezone.utc)
        budget = defaults["max_records"] if defaults["max_records"] is not None else len(recs)

        for i, raw in enumerate(recs):
            if not isinstance(raw, dict):
                out.append(raw)
                continue
            rec = dict(raw)

            # Skip if already has lat
            if defaults["skip_if_lat_set"] and rec.get(defaults["out_lat_field"]) not in (None, "", 0):
                skipped += 1
                out.append(rec)
                continue

            if i >= budget:
                out.append(rec)
                continue

            school = (rec.get(defaults["school_field"]) or "").strip() if isinstance(rec.get(defaults["school_field"]), str) else (rec.get(defaults["school_field"]) or "")
            address = (rec.get(defaults["address_field"]) or "").strip() if isinstance(rec.get(defaults["address_field"]), str) else (rec.get(defaults["address_field"]) or "")
            city = (rec.get(defaults["city_field"]) or "").strip() if isinstance(rec.get(defaults["city_field"]), str) else (rec.get(defaults["city_field"]) or "")
            school = str(school) if school else ""
            address = str(address) if address else ""
            city = str(city) if city else ""

            queries = []
            if address and city:
                queries.append(f"{address}, {city}")
                if school:
                    queries.append(f"{school}, {address}, {city}")
            elif address and school:
                queries.append(f"{school}, {address}")
            if school and city:
                queries.append(f"{school}, {city}")
            if school:
                queries.append(school)

            if not queries:
                failed += 1
                out.append(rec)
                continue

            lat, lon, full_addr, parsed_addr = self._geocode_one(
                queries,
                timeout=defaults["timeout_seconds"],
                max_retries=defaults["max_retries"],
            )

            # rate-limit between requests regardless of success
            if defaults["rate_limit_seconds"] > 0:
                time.sleep(defaults["rate_limit_seconds"])

            if lat is not None:
                rec[defaults["out_lat_field"]] = lat
                rec[defaults["out_lon_field"]] = lon
                rec[defaults["out_full_address_field"]] = full_addr
                if parsed_addr:
                    rec[defaults["out_parsed_address_field"]] = parsed_addr
                rec[defaults["out_timestamp_field"]] = now_utc
                geocoded += 1
            else:
                failed += 1

            out.append(rec)

        return {
            "status": "success",
            "action": "bulk_geocode_records",
            "total": len(recs),
            "geocoded": geocoded,
            "skipped_already_set": skipped,
            "failed": failed,
            "records": out,
        }
