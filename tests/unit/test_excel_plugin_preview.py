from __future__ import annotations

import pandas as pd

import app as app_module
from plugins.system_tools.excel_plugin import ExcelPlugin


def test_preview_sheet_returns_small_bounded_sample(tmp_path) -> None:
    workbook_path = tmp_path / "sample.xlsx"
    frame = pd.DataFrame(
        [
            {"Site": "A", "Code": "100"},
            {"Site": "B", "Code": "200"},
            {"Site": "C", "Code": "300"},
        ]
    )
    frame.to_excel(workbook_path, index=False, sheet_name="Sites")

    plugin = ExcelPlugin(base_dir=str(tmp_path))
    result = plugin.preview_sheet(
        {
            "file_path": str(workbook_path),
            "sheet": "Sites",
            "max_rows": 2,
            "start_row": 1,
        }
    )

    assert result["status"] == "success"
    assert result["sheet_name"] == "Sites"
    assert result["total_row_count"] == 3
    assert result["preview_row_count"] == 2
    assert result["preview_rows"] == [
        {"row": 3, "Site": "B", "Code": 200},
        {"row": 4, "Site": "C", "Code": 300},
    ]


def test_excel_to_json_honors_row_window(tmp_path) -> None:
    workbook_path = tmp_path / "sample.xlsx"
    frame = pd.DataFrame(
        [
            {"Site": "A", "Code": "100"},
            {"Site": "B", "Code": "200"},
            {"Site": "C", "Code": "300"},
        ]
    )
    frame.to_excel(workbook_path, index=False, sheet_name="Sites")

    plugin = ExcelPlugin(base_dir=str(tmp_path))
    result = plugin.excel_to_json(
        {
            "file_path": str(workbook_path),
            "sheet": "Sites",
            "max_rows": 1,
            "start_row": 1,
            "save_as": "output.json",
        }
    )

    assert result["status"] == "success"
    assert result["row_count"] == 1
    assert result["total_row_count"] == 3
    assert result["save_as_content"] == [
        {"row": 3, "Site": "B", "Code": 200},
    ]


def test_extract_slack_file_context_summarizes_excel_attachment(monkeypatch, tmp_path) -> None:
    saved_excel = tmp_path / "slack_downloads" / "report_123.xlsx"
    saved_excel.parent.mkdir(parents=True, exist_ok=True)
    saved_excel.write_bytes(b"fake")

    monkeypatch.setattr(app_module, "_download_slack_binary_file", lambda *_args, **_kwargs: (b"excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    monkeypatch.setattr(app_module, "_save_slack_excel_copy", lambda *_args, **_kwargs: str(saved_excel))
    monkeypatch.setattr(
        app_module,
        "_summarize_slack_excel_file",
        lambda *_args, **_kwargs: {
            "file_path": str(saved_excel),
            "sheet_count": 1,
            "sheet_names": ["Sites"],
            "first_sheet_preview": {
                "sheet_name": "Sites",
                "column_names": ["row", "Site", "Code"],
                "total_row_count": 3,
                "preview_row_count": 2,
                "preview_rows": [
                    {"row": 2, "Site": "A", "Code": "100"},
                    {"row": 3, "Site": "B", "Code": "200"},
                ],
            },
        },
    )

    event = {
        "channel": "C123TEST",
        "files": [
            {
                "name": "report.xlsx",
                "filetype": "xlsx",
                "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "url_private_download": "https://example.com/report.xlsx",
            }
        ],
    }

    prompt_suffix, reply_suffix, image_data_urls = app_module._extract_slack_file_context(
        event,
        "xoxb-test-token",
    )

    assert "Excel 'report.xlsx' workbook summary" in prompt_suffix
    assert '"sheet_names": ["Sites"]' in prompt_suffix
    assert "Saved local Excel copies:" in prompt_suffix
    assert str(saved_excel) in prompt_suffix
    assert "Attachments in your message: report.xlsx" in reply_suffix
    assert image_data_urls == []