"""Web search plugin using Google Custom Search JSON API."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any


_GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"


class WebSearchPlugin:
    """Search the web via Google Custom Search JSON API.

    Constructor args:
        api_key (str): Google API key with Custom Search API enabled.
                       Falls back to GOOGLE_CSE_API_KEY, then GOOGLE_MAPS_API_KEY env vars.
        cse_id (str):  Custom Search Engine ID (cx).
                       Falls back to GOOGLE_CSE_ID env var.

    Free tier: 100 queries/day. Paid: $5 per 1,000 queries after that.
    """

    def __init__(
        self,
        api_key: str | None = None,
        cse_id: str | None = None,
    ) -> None:
        resolved_key = (
            api_key
            or os.getenv("GOOGLE_CSE_API_KEY")
            or os.getenv("GOOGLE_MAPS_API_KEY")
            or ""
        ).strip()
        resolved_cse = (cse_id or os.getenv("GOOGLE_CSE_ID") or "").strip()

        if not resolved_key:
            raise ValueError(
                "api_key must be provided or set GOOGLE_CSE_API_KEY "
                "(or GOOGLE_MAPS_API_KEY as fallback)"
            )
        if not resolved_cse:
            raise ValueError(
                "cse_id must be provided or set GOOGLE_CSE_ID env var"
            )

        self._api_key = resolved_key
        self._cse_id = resolved_cse

    # ---------------------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------------------

    def _request(self, params: dict[str, Any]) -> dict:
        """Execute a Custom Search API request and return parsed JSON."""
        params = {**params, "key": self._api_key, "cx": self._cse_id}
        url = _GOOGLE_CSE_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310
            data = json.loads(response.read())
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"Google CSE API error {err.get('code')}: {err.get('message')}"
            )
        return data

    @staticmethod
    def _format_items(items: list[dict]) -> list[dict]:
        """Extract title, snippet, and URL from raw result items."""
        return [
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", "").replace("\n", " ").strip(),
                "url": item.get("link", ""),
                "display_url": item.get("displayLink", ""),
            }
            for item in items
        ]

    # ---------------------------------------------------------------------------
    # Public methods
    # ---------------------------------------------------------------------------

    def web_search(self, query: str, num_results: int = 5) -> list:
        """Search the web and return top results.

        Args:
            query: Search query string.
            num_results: Number of results to return (1-10, Google limit per request).

        Returns:
            List of dicts with keys: title, snippet, url, display_url.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        num_results = max(1, min(int(num_results), 10))

        data = self._request({"q": query.strip(), "num": num_results})
        return self._format_items(data.get("items", []))

    def search_near_address(
        self,
        address: str,
        query: str,
        num_results: int = 5,
    ) -> list:
        """Search for something near a specific address.

        Useful for finding businesses, contractors, or context about a site location.

        Args:
            address: Street address or place name to anchor the search.
            query: What to search for (e.g. "security company", "CCTV installer").
            num_results: Number of results to return (1-10).

        Returns:
            List of dicts with keys: title, snippet, url, display_url.
        """
        if not isinstance(address, str) or not address.strip():
            raise ValueError("address must be a non-empty string")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")

        combined = f"{query.strip()} near {address.strip()}"
        return self.web_search(combined, num_results=num_results)

    def search_image_context(
        self,
        formatted_address: str,
        objects_found: list,
        num_results: int = 5,
    ) -> list:
        """Search for business context based on image analysis results.

        Combines detected security objects and the reverse-geocoded address
        to find relevant context about a site.

        Args:
            formatted_address: Address string from reverse_geocode().
            objects_found: List of detected object labels from detect_objects().
            num_results: Number of results to return (1-10).

        Returns:
            List of dicts with keys: title, snippet, url, display_url.
        """
        if not isinstance(formatted_address, str) or not formatted_address.strip():
            raise ValueError("formatted_address must be a non-empty string")
        if not isinstance(objects_found, list):
            raise ValueError("objects_found must be a list")

        object_summary = (
            ", ".join(str(o) for o in objects_found[:5])
            if objects_found
            else "security equipment"
        )
        query = f"security system installation {object_summary} {formatted_address.strip()}"
        return self.web_search(query, num_results=num_results)
