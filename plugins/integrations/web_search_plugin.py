"""Web search plugin using SerpApi (Google Search results)."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any


_SERPAPI_URL = "https://serpapi.com/search.json"


class WebSearchPlugin:
    """Search the web via SerpApi (Google Search results).

    Constructor args:
        api_key (str): SerpApi API key.
                       Falls back to SERPAPI_KEY env var.

    Free tier: 100 searches/month, no credit card required.
    Sign up at https://serpapi.com
    """

    def __init__(self, api_key: str | None = None) -> None:
        resolved_key = (api_key or os.getenv("SERPAPI_KEY") or "").strip()
        if not resolved_key:
            raise ValueError(
                "api_key must be provided or set SERPAPI_KEY env var"
            )
        self._api_key = resolved_key

    # ---------------------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------------------

    def _request(self, params: dict[str, Any]) -> dict:
        """Execute a SerpApi request and return parsed JSON."""
        params = {**params, "api_key": self._api_key, "engine": "google"}
        url = _SERPAPI_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310
            data = json.loads(response.read())
        if "error" in data:
            raise RuntimeError(f"SerpApi error: {data['error']}")
        return data

    @staticmethod
    def _format_items(items: list[dict]) -> list[dict]:
        """Extract title, snippet, and URL from raw result items."""
        return [
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", "").replace("\n", " ").strip(),
                "url": item.get("link", ""),
                "display_url": item.get("displayed_link", ""),
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
        return self._format_items(data.get("organic_results", []))

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
