from __future__ import annotations

from pathlib import Path

import pytest

from plugins.system_tools.file_system_plugin import FileSystemPlugin


def test_create_list_move_and_delete_within_base_dir(tmp_path: Path) -> None:
    plugin = FileSystemPlugin(base_dir=str(tmp_path))

    create_result = plugin.create_directory("docs/inbox")
    assert create_result["status"] == "success"
    assert create_result["directory"] == "docs/inbox"

    source_file = tmp_path / "docs" / "inbox" / "note.txt"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("hello", encoding="utf-8")

    move_result = plugin.move_path("docs/inbox/note.txt", "docs/archive/note.txt")
    assert move_result["status"] == "success"
    assert (tmp_path / "docs" / "archive" / "note.txt").exists()

    list_result = plugin.list_directory("docs")
    assert list_result["status"] == "success"
    assert any(entry["name"] == "archive" and entry["type"] == "directory" for entry in list_result["entries"])

    delete_result = plugin.delete_path("docs/archive/note.txt")
    assert delete_result["status"] == "success"
    assert not (tmp_path / "docs" / "archive" / "note.txt").exists()


def test_rejects_path_traversal(tmp_path: Path) -> None:
    plugin = FileSystemPlugin(base_dir=str(tmp_path))

    with pytest.raises(ValueError, match="Invalid path"):
        plugin.create_directory("../outside")


def test_rejects_absolute_path(tmp_path: Path) -> None:
    plugin = FileSystemPlugin(base_dir=str(tmp_path))

    with pytest.raises(ValueError, match="Absolute paths are not allowed"):
        plugin.list_directory(str(tmp_path))
