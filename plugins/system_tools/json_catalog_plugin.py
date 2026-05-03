"""JSON catalog plugin — list and read pre-bundled JSON request/workflow templates.

Exposes a tiny, scoped surface so the Streamlit UI can populate
"pick a template" dropdowns without granting generic file-read access. Categories
map to fixed subdirectories of the dynamic-exec-service repo:

    workflows  -> jsons/workflows/
    execute    -> jsons/system_tools/

All paths are resolved server-side so the same plugin works in dev and on
Heroku regardless of where the page is running.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Default category -> directory mapping (relative to the service root).
_DEFAULT_CATEGORIES: dict[str, str] = {
    "workflows": "jsons/workflows",
    "execute": "jsons/system_tools",
}


def _default_root_dir() -> Path:
    """Return the dynamic-exec-service repo root.

    This file lives at <root>/plugins/system_tools/json_catalog_plugin.py, so the
    root is the parent of `plugins/`.
    """
    return Path(__file__).resolve().parents[2]


class JsonCatalogPlugin:
    """List and read JSON templates from a fixed set of repo-relative directories."""

    def __init__(
        self,
        root_dir: str | None = None,
        categories: dict[str, str] | None = None,
    ) -> None:
        if root_dir is not None and (not isinstance(root_dir, str) or not root_dir.strip()):
            raise ValueError("root_dir must be a non-empty string when provided")
        if categories is not None and not isinstance(categories, dict):
            raise ValueError("categories must be an object when provided")

        self.root_dir: Path = Path(root_dir).resolve() if root_dir else _default_root_dir()
        if not self.root_dir.exists() or not self.root_dir.is_dir():
            raise ValueError(f"root_dir does not exist: {self.root_dir}")

        if categories:
            for k, v in categories.items():
                if not isinstance(k, str) or not k.strip():
                    raise ValueError("category keys must be non-empty strings")
                if not isinstance(v, str) or not v.strip():
                    raise ValueError("category values must be non-empty strings")
            self.categories: dict[str, str] = dict(categories)
        else:
            self.categories = dict(_DEFAULT_CATEGORIES)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_category_dir(self, category: str) -> Path:
        if not isinstance(category, str) or not category.strip():
            raise ValueError("category must be a non-empty string")
        cat = category.strip()
        if cat not in self.categories:
            allowed = ", ".join(sorted(self.categories))
            raise ValueError(f"unknown category '{cat}'. allowed: {allowed}")
        target = (self.root_dir / self.categories[cat]).resolve()
        # Ensure the target is still under root_dir (guard against config tricks).
        try:
            target.relative_to(self.root_dir)
        except ValueError as exc:
            raise ValueError("category directory escapes root_dir") from exc
        if not target.exists() or not target.is_dir():
            raise ValueError(f"category directory does not exist: {target}")
        return target

    def _resolve_template_path(self, category: str, name: str) -> Path:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        cat_dir = self._resolve_category_dir(category)

        # Allow either "foo" or "foo.json"; reject anything containing path separators
        # so we cannot escape the category directory.
        cleaned = name.strip()
        if "/" in cleaned or "\\" in cleaned or cleaned.startswith("."):
            raise ValueError("name must not contain path separators or start with a dot")
        if not cleaned.endswith(".json"):
            cleaned = f"{cleaned}.json"

        candidate = (cat_dir / cleaned).resolve()
        try:
            candidate.relative_to(cat_dir)
        except ValueError as exc:
            raise ValueError("name escapes category directory") from exc
        if not candidate.exists() or not candidate.is_file():
            raise ValueError(f"template not found: {cleaned}")
        return candidate

    # ------------------------------------------------------------------
    # Public methods (whitelisted)
    # ------------------------------------------------------------------

    def list_templates(
        self,
        category: str | dict[str, Any] = "workflows",
        recursive: bool = False,
    ) -> dict[str, Any]:
        """List JSON templates in the given category directory.

        Returns a list of `{name, relative_path, size_bytes, modified_at}` entries
        sorted by name. Set `recursive=True` to descend into subdirectories.
        """
        if isinstance(category, dict):
            payload = category
            resolved_category = payload.get("category", "workflows")
            resolved_recursive = payload.get("recursive", recursive)
        else:
            resolved_category = category
            resolved_recursive = recursive

        if not isinstance(resolved_recursive, bool):
            raise ValueError("recursive must be a boolean")

        cat_dir = self._resolve_category_dir(resolved_category)
        glob_pattern = "**/*.json" if resolved_recursive else "*.json"

        entries: list[dict[str, Any]] = []
        for path in sorted(cat_dir.glob(glob_pattern)):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append({
                "name": path.name,
                "relative_path": str(path.relative_to(cat_dir)).replace("\\", "/"),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

        return {
            "status": "success",
            "action": "list_templates",
            "category": resolved_category,
            "category_dir": str(cat_dir),
            "count": len(entries),
            "templates": entries,
        }

    def read_template(
        self,
        category: str | dict[str, Any] = "workflows",
        name: str | None = None,
    ) -> dict[str, Any]:
        """Read a single JSON template and return its parsed content."""
        if isinstance(category, dict):
            payload = category
            resolved_category = payload.get("category", "workflows")
            resolved_name = payload.get("name", name)
        else:
            resolved_category = category
            resolved_name = name

        if resolved_name is None:
            raise ValueError("name is required")

        path = self._resolve_template_path(resolved_category, resolved_name)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise ValueError(f"failed to read template: {exc}") from exc
        try:
            content = json.loads(text)
        except Exception as exc:
            raise ValueError(f"template is not valid JSON: {exc}") from exc

        return {
            "status": "success",
            "action": "read_template",
            "category": resolved_category,
            "name": path.name,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "content": content,
        }
