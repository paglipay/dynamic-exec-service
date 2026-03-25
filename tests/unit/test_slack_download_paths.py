from __future__ import annotations

from pathlib import Path

import app as app_module


def test_save_slack_image_copy_uses_flat_slack_downloads_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "SLACK_IMAGE_SAVE_BASE_DIR", str(tmp_path))

    saved_path = app_module._save_slack_image_copy(
        binary_data=b"fake-image-bytes",
        original_name="photo.png",
        content_type="image/png",
        channel="C123TEST",
    )

    assert isinstance(saved_path, str)
    path = Path(saved_path)
    assert path.parent == (tmp_path / "slack_downloads")
    assert path.name == "photo.png"
    assert path.exists()


def test_save_slack_pdf_copy_uses_flat_slack_downloads_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "SLACK_IMAGE_SAVE_BASE_DIR", str(tmp_path))

    saved_path = app_module._save_slack_pdf_copy(
        binary_data=b"%PDF-1.4 fake",
        original_name="report.pdf",
        channel="C123TEST",
    )

    assert isinstance(saved_path, str)
    path = Path(saved_path)
    assert path.parent == (tmp_path / "slack_downloads")
    assert path.exists()
    assert path.name == "report.pdf"
    assert path.suffix == ".pdf"


def test_save_slack_excel_copy_uses_flat_slack_downloads_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "SLACK_IMAGE_SAVE_BASE_DIR", str(tmp_path))

    saved_path = app_module._save_slack_excel_copy(
        binary_data=b"fake-excel-bytes",
        original_name="report.xlsx",
        channel="C123TEST",
    )

    assert isinstance(saved_path, str)
    path = Path(saved_path)
    assert path.parent == (tmp_path / "slack_downloads")
    assert path.exists()
    assert path.name == "report.xlsx"
    assert path.suffix == ".xlsx"


def test_save_slack_image_copy_overwrites_same_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "SLACK_IMAGE_SAVE_BASE_DIR", str(tmp_path))

    first_path = app_module._save_slack_image_copy(
        binary_data=b"first",
        original_name="photo.png",
        content_type="image/png",
        channel="C123TEST",
    )
    second_path = app_module._save_slack_image_copy(
        binary_data=b"second",
        original_name="photo.png",
        content_type="image/png",
        channel="C123TEST",
    )

    assert first_path == second_path
    assert isinstance(second_path, str)
    assert Path(second_path).read_bytes() == b"second"
