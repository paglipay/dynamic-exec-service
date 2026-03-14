from __future__ import annotations

import app as app_module


def test_parse_tsv_rows_parses_header_and_values() -> None:
    tsv_text = (
        "Site\tLoc Code\tSchool Name\tAddress\tCity\tContractor\n"
        "MANN\tUCLA COMM SCH7574\tHorace Mann UCLA Community School\t"
        "7001 S ST ANDREWS PL\tLOS ANGELES, CA\tCSIB\n"
    )

    rows = app_module._parse_tsv_rows(tsv_text)

    assert rows == [
        {
            "Site": "MANN",
            "Loc Code": "UCLA COMM SCH7574",
            "School Name": "Horace Mann UCLA Community School",
            "Address": "7001 S ST ANDREWS PL",
            "City": "LOS ANGELES, CA",
            "Contractor": "CSIB",
        }
    ]


def test_extract_slack_file_context_parses_tsv_attachment(monkeypatch) -> None:
    tsv_text = (
        "Site\tLoc Code\tSchool Name\tAddress\tCity\tContractor\n"
        "MANN\tUCLA COMM SCH7574\tHorace Mann UCLA Community School\t"
        "7001 S ST ANDREWS PL\tLOS ANGELES, CA\tCSIB\n"
    )

    monkeypatch.setattr(app_module, "_download_slack_text_file", lambda *_args, **_kwargs: tsv_text)

    event = {
        "channel": "C123TEST",
        "files": [
            {
                "name": "schools.tsv",
                "filetype": "tsv",
                "mimetype": "text/tab-separated-values",
                "url_private_download": "https://example.com/schools.tsv",
            }
        ],
    }

    prompt_suffix, reply_suffix, image_data_urls = app_module._extract_slack_file_context(
        event,
        "xoxb-test-token",
    )

    assert "TSV 'schools.tsv' parsed rows" in prompt_suffix
    assert '"Loc Code": "UCLA COMM SCH7574"' in prompt_suffix
    assert "Attachments in your message: schools.tsv" in reply_suffix
    assert image_data_urls == []
