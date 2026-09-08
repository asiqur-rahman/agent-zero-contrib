from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins._oauth.helpers import cli_prompt

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-pixels"
PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()


def test_text_only_messages_flatten_with_role_labels():
    prompt = cli_prompt.build_prompt(
        [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Say hi"},
            {"role": "assistant", "content": "hi"},
        ]
    )
    assert prompt.text == "[System]\nBe terse.\n\n[User]\nSay hi\n\n[Assistant]\nhi"
    assert prompt.image_paths == []
    assert prompt.temp_dir is None


def test_data_url_image_is_written_to_disk_and_referenced_in_prompt():
    # The regression this fixes: the image part used to contribute nothing,
    # so an attachment silently disappeared before the CLI ever ran.
    prompt = cli_prompt.build_prompt(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
                ],
            }
        ]
    )
    try:
        assert len(prompt.image_paths) == 1
        image = prompt.image_paths[0]
        assert image.read_bytes() == PNG_BYTES
        assert image.suffix == ".png"
        assert "What is in this image?" in prompt.text
        assert str(image) in prompt.text
        assert "[Attached image]" in prompt.text
    finally:
        prompt.cleanup()
    assert not image.exists()


def test_image_only_message_still_has_content():
    # tools/vision_load.py's after_execution() appends a history message whose
    # content is image parts with no text at all. That used to flatten to ""
    # and could fail the whole turn with "No prompt content to send."
    prompt = cli_prompt.build_prompt(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": PNG_DATA_URL}}]}]
    )
    try:
        assert prompt.has_content is True
        assert len(prompt.image_paths) == 1
        assert prompt.text.startswith("[Attached image]")
    finally:
        prompt.cleanup()


def test_multiple_images_are_numbered_and_all_listed(tmp_path):
    prompt = cli_prompt.build_prompt(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Compare these."},
                    {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
                    {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
                ],
            }
        ]
    )
    try:
        assert len(prompt.image_paths) == 2
        assert [path.name for path in prompt.image_paths] == ["image-1.png", "image-2.png"]
        assert "2 images" in prompt.text
        for path in prompt.image_paths:
            assert str(path) in prompt.text
    finally:
        prompt.cleanup()


def test_local_path_image_is_copied_not_referenced_in_place(tmp_path):
    # tools/vision_load.py passes real filesystem paths straight through, so
    # those have to work too -- and cleanup() must never touch the original.
    original = tmp_path / "screenshot.jpg"
    original.write_bytes(PNG_BYTES)

    prompt = cli_prompt.build_prompt(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": str(original)}}]}]
    )
    assert len(prompt.image_paths) == 1
    copied = prompt.image_paths[0]
    assert copied != original
    assert copied.read_bytes() == PNG_BYTES
    assert copied.suffix == ".jpg"

    prompt.cleanup()
    assert not copied.exists()
    assert original.is_file()


def test_remote_urls_are_never_fetched():
    prompt = cli_prompt.build_prompt(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this."},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            }
        ]
    )
    assert prompt.image_paths == []
    assert prompt.text == "[User]\nLook at this."


def test_image_url_as_bare_string_is_accepted():
    prompt = cli_prompt.build_prompt(
        [{"role": "user", "content": [{"type": "image_url", "image_url": PNG_DATA_URL}]}]
    )
    try:
        assert len(prompt.image_paths) == 1
    finally:
        prompt.cleanup()


def test_malformed_and_missing_images_are_skipped():
    prompt = cli_prompt.build_prompt(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,"}},
                    {"type": "image_url", "image_url": {"url": "/no/such/file.png"}},
                    {"type": "image_url", "image_url": {}},
                ],
            }
        ]
    )
    assert prompt.image_paths == []
    assert prompt.text == "[User]\nhi"


def test_empty_messages_have_no_content():
    prompt = cli_prompt.build_prompt([])
    assert prompt.has_content is False
    assert cli_prompt.build_prompt([{"role": "user", "content": ""}]).has_content is False


def test_cleanup_is_idempotent():
    prompt = cli_prompt.build_prompt(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": PNG_DATA_URL}}]}]
    )
    temp_dir = prompt.temp_dir
    assert temp_dir is not None
    prompt.cleanup()
    prompt.cleanup()
    assert not temp_dir.exists()
