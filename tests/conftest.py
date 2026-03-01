from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

import app as app_module
from executor.engine import JSONExecutor


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client() -> pytest.Generator:
    app_module.app.config["TESTING"] = True
    app_module.executor = JSONExecutor()
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def load_json_request() -> Callable[[str], dict[str, Any]]:
    def _loader(relative_path: str) -> dict[str, Any]:
        target = (WORKSPACE_ROOT / relative_path).resolve()
        if not target.exists() or not target.is_file():
            raise ValueError(f"Request JSON not found: {relative_path}")
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"Request JSON must be an object: {relative_path}")
        return payload

    return _loader
