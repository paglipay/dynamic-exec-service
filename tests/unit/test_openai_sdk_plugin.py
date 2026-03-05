from __future__ import annotations

import base64
from pathlib import Path

from plugins.integrations.openai_sdk_plugin import OpenAISDKPlugin


def test_generate_image_saves_b64_payload(tmp_path: Path) -> None:
    plugin = OpenAISDKPlugin.__new__(OpenAISDKPlugin)
    plugin._default_image_output_dir = tmp_path / "images"

    payload = b"fake-png-bytes"

    class FakeImageData:
        b64_json = base64.b64encode(payload).decode("ascii")
        url = None
        revised_prompt = "A revised prompt"

    class FakeImageResponse:
        data = [FakeImageData()]
        model = "gpt-image-1"

    class FakeImagesClient:
        @staticmethod
        def generate(**_kwargs):
            return FakeImageResponse()

    class FakeClient:
        images = FakeImagesClient()

    plugin.client = FakeClient()

    result = plugin.generate_image("Draw a small blue bird", file_name="bird_art", output_format="png")

    assert result["status"] == "success"
    assert result["image_format"] == "png"
    assert result["revised_prompt"] == "A revised prompt"
    output_path = Path(result["image_path"])
    assert output_path.exists()
    assert output_path.read_bytes() == payload


def test_generate_image_returns_url_when_b64_not_provided() -> None:
    plugin = OpenAISDKPlugin.__new__(OpenAISDKPlugin)

    class FakeImageData:
        b64_json = None
        url = "https://example.com/generated.png"
        revised_prompt = None

    class FakeImageResponse:
        data = [FakeImageData()]
        model = "gpt-image-1"

    class FakeImagesClient:
        @staticmethod
        def generate(**_kwargs):
            return FakeImageResponse()

    class FakeClient:
        images = FakeImagesClient()

    plugin.client = FakeClient()

    result = plugin.generate_image("Generate abstract geometry")

    assert result["status"] == "success"
    assert result["image_url"] == "https://example.com/generated.png"


def test_generate_image_rejects_empty_prompt() -> None:
    plugin = OpenAISDKPlugin.__new__(OpenAISDKPlugin)

    try:
        plugin.generate_image("   ")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "prompt must be a non-empty string" in str(exc)
