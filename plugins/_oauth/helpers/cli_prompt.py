from __future__ import annotations

import base64
import binascii
import mimetypes
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Shared message -> prompt conversion for the three external-CLI providers
# (Command Code, Claude Code, Cursor CLI).
#
# All three take a single text prompt argument in headless mode (`-p
# "<prompt>"`), not an OpenAI-style messages array, so a conversation has to
# be flattened into one string. Each provider used to do that with its own
# copy of a loop that read `part["text"]` and nothing else -- which silently
# dropped every image part Agent Zero sends.
#
# That drop had two visible failure modes, not one:
#
#   * with the provider selected as the chat model, an attached image simply
#     vanished and the model answered as if it had never been sent;
#   * with it selected as the *vision* model, tools/vision_load.py sends the
#     image and then raises "Vision Model returned an empty response." when
#     nothing comes back -- and vision_load's own after_execution() appends a
#     history message whose content is image parts with no text at all, which
#     flattened to "" and could make run_prompt() fail outright with the
#     opaque "No prompt content to send."
#
# These CLIs are agentic coding tools: they cannot take pixels on the command
# line, but they can read files. So images are materialised into a temporary
# directory and referenced by absolute path in the prompt, and the caller
# grants the CLI whatever read permission it needs (see
# claude_code_cli.run_prompt()'s --allowedTools). An image that the CLI
# declines to open produces a normal answer saying so, instead of a dropped
# attachment or a confusing error.
DEFAULT_IMAGE_EXTENSION = ".png"
TEMP_DIR_PREFIX = "a0-oauth-cli-images-"

_ROLE_LABELS = {"system": "System", "assistant": "Assistant"}


@dataclass
class Prompt:
    """One flattened turn: the prompt string plus any materialised images."""

    text: str
    image_paths: list[Path] = field(default_factory=list)
    temp_dir: Path | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.text or self.image_paths)

    def cleanup(self) -> None:
        """Removes the temp directory this prompt's images were written to.

        Only ever removes a directory this module created -- images that
        came from a path Agent Zero already had on disk are copied in, never
        referenced in place, so cleanup can never delete a user's file.
        """
        if self.temp_dir is None:
            return
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir = None


def build_prompt(messages: list[dict[str, Any]]) -> Prompt:
    """Flattens an OpenAI-style messages array into one prompt plus images."""
    parts: list[str] = []
    images: list[tuple[bytes, str]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        text, message_images = _split_content(message.get("content"))
        images.extend(message_images)
        text = text.strip()
        if not text:
            continue
        parts.append(f"[{_ROLE_LABELS.get(role, 'User')}]\n{text}")

    prompt = Prompt(text="\n\n".join(parts))
    if images:
        _attach_images(prompt, images)
    return prompt


def _split_content(content: Any) -> tuple[str, list[tuple[bytes, str]]]:
    if not isinstance(content, list):
        return str(content or ""), []

    texts: list[str] = []
    images: list[tuple[bytes, str]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image_url" or "image_url" in part:
            image = _decode_image(part.get("image_url"))
            if image is not None:
                images.append(image)
            continue
        texts.append(str(part.get("text") or ""))
    return "".join(texts), images


def _decode_image(image_url: Any) -> tuple[bytes, str] | None:
    """Returns (bytes, extension) for one `image_url` part, or None.

    Handles both shapes Agent Zero produces: a `data:` URL (what
    helpers/images.py's to_data_url() emits) and a local path or `file://`
    ref (what tools/vision_load.py passes straight through). Remote http(s)
    URLs are deliberately not fetched -- that would be an outbound request
    on the user's behalf, made from a URL this plugin did not choose.
    """
    if isinstance(image_url, dict):
        url = str(image_url.get("url") or "").strip()
    else:
        url = str(image_url or "").strip()
    if not url:
        return None

    lowered = url.lower()
    if lowered.startswith("data:"):
        return _decode_data_url(url)
    if lowered.startswith(("http://", "https://")):
        return None

    for path in _candidate_paths(url):
        try:
            if not path.is_file():
                continue
            return path.read_bytes(), path.suffix or DEFAULT_IMAGE_EXTENSION
        except OSError:
            continue
    return None


def _candidate_paths(url: str) -> list[Path]:
    """Filesystem paths `url` might name, in resolution order.

    helpers.images.resolve_ref() is preferred because it understands the
    forms Agent Zero itself produces (`file://`, a relative ref, and the
    `/a0/...` container path rewritten for a native dev checkout), but it
    pulls in the full runtime (PIL, the files helper) and rejects path
    shapes it was not written for -- so the raw value is always tried too.
    """
    candidates: list[Path] = []
    try:
        from helpers import images as image_helpers

        candidates.append(image_helpers.resolve_ref(url))
    except Exception:
        pass
    try:
        candidates.append(Path(url).expanduser())
    except (OSError, ValueError):
        pass
    return candidates


def _decode_data_url(url: str) -> tuple[bytes, str] | None:
    header, _, encoded = url.partition(",")
    if not encoded or ";base64" not in header.lower():
        return None
    mime = header[len("data:"):].split(";", 1)[0].strip()
    try:
        payload = base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not payload:
        return None
    return payload, mimetypes.guess_extension(mime) or DEFAULT_IMAGE_EXTENSION


def _attach_images(prompt: Prompt, images: list[tuple[bytes, str]]) -> None:
    try:
        temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX))
    except OSError:
        return

    written: list[Path] = []
    for index, (payload, extension) in enumerate(images, start=1):
        path = temp_dir / f"image-{index}{extension}"
        try:
            path.write_bytes(payload)
        except OSError:
            continue
        written.append(path)

    if not written:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return

    prompt.temp_dir = temp_dir
    prompt.image_paths = written
    prompt.text = "\n\n".join(filter(None, [prompt.text, _image_section(written)]))


def _image_section(paths: list[Path]) -> str:
    count = len(paths)
    noun = "image" if count == 1 else "images"
    listing = "\n".join(f"{index}. {path}" for index, path in enumerate(paths, start=1))
    return (
        f"[Attached {noun}]\n"
        f"This message includes {count} {noun}, saved as local file"
        f"{'' if count == 1 else 's'}. Read {'it' if count == 1 else 'them'} "
        "from disk to answer:\n"
        f"{listing}"
    )
