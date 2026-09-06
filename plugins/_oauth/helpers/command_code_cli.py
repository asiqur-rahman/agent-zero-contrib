from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

# Command Code (https://commandcode.ai) has no published REST/OAuth API --
# unlike Codex/GitHub Copilot/Gemini/xAI Grok, which this plugin drives via a
# real OAuth handshake against the vendor's own servers, this provider shells
# out to the locally installed `command-code` CLI binary and drives its own
# supported headless contract (`status --json`, `-p --output-format json`).
# The user authenticates with `command-code login` themselves, outside Agent
# Zero; this module only ever reads that CLI's public status/output surface.
COMMAND_CODE_BINARY = "command-code"
NPM_BINARY = "npm"
VERSION_TIMEOUT_SECONDS = 5
STATUS_TIMEOUT_SECONDS = 8
RUN_TIMEOUT_SECONDS = 300
INSTALL_TIMEOUT_SECONDS = 180


def _persisted_home() -> Path:
    # command-code has no documented config-dir override (unlike Claude
    # Code's CLAUDE_CONFIG_DIR, see claude_code_cli.py) -- it always reads
    # its session from `~/.commandcode`, i.e. whatever $HOME resolves to.
    # The container's writable layer outside usr/ (this plugin's own
    # persisted directory, see provider_data_dir()) does not survive a
    # container recreation, so a login under the default $HOME (typically
    # /root) is lost on every image update/redeploy -- this is the confirmed
    # cause of Command Code repeatedly showing disconnected. Overriding HOME
    # to a path under usr/ for every command-code invocation fixes that,
    # but only once the user re-runs `command-code login` under the SAME
    # override (see README) -- a prior /root/.commandcode session is not
    # migrated automatically.
    from plugins._oauth.helpers.providers.base import COMMAND_CODE_PROVIDER_ID, provider_data_dir

    home = provider_data_dir(COMMAND_CODE_PROVIDER_ID) / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _cli_env() -> dict[str, str]:
    env = {**os.environ, "NO_COLOR": "1"}
    try:
        env["HOME"] = str(_persisted_home())
    except Exception:
        # provider_data_dir() imports the full Agent Zero `helpers.files`
        # module, which is not available in isolated unit-test runs -- fall
        # back to the unmodified environment (no persisted HOME override)
        # rather than failing the whole CLI call over it.
        pass
    return env


def npm_available() -> bool:
    try:
        result = subprocess.run(
            [NPM_BINARY, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def install_latest() -> dict[str, Any]:
    """Runs `npm install -g command-code@latest`.

    This only ever installs the CLI binary itself -- it never touches
    authentication. A successful install still requires the user to run
    `command-code login` themselves; nothing here attempts to automate that,
    and it never will (see the module docstring for why).
    """
    if not npm_available():
        return {
            "ok": False,
            "error": "npm is not available on PATH. Install Node.js first, then retry.",
        }

    try:
        result = subprocess.run(
            [NPM_BINARY, "install", "-g", "command-code@latest"],
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"npm install timed out after {INSTALL_TIMEOUT_SECONDS}s.",
        }
    except OSError as exc:
        return {"ok": False, "error": f"Unable to run npm install: {exc}"}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        # npm's own output can be long (deprecation warnings, audit noise);
        # keep only the tail, which is where the actual failure reason is.
        detail = detail[-500:] if detail else "npm install failed."
        return {"ok": False, "error": detail}

    return {"ok": True, "error": ""}


def is_installed() -> bool:
    try:
        result = subprocess.run(
            [COMMAND_CODE_BINARY, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def get_status() -> dict[str, Any]:
    """Runs `command-code status --json` and returns its parsed contract.

    Verified directly against the installed CLI (v1.47.0): the unauthenticated
    shape is `{"authenticated":false,"version":"..."}` with exit code 1 --
    the JSON body's `authenticated` field is the authoritative answer, not
    the exit code, so it is read even when the process exits non-zero.
    """
    if not is_installed():
        return {
            "installed": False,
            "authenticated": False,
            "version": "",
            "error": "Command Code CLI is not installed. Install it with: npm i -g command-code",
        }

    try:
        result = subprocess.run(
            [COMMAND_CODE_BINARY, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=STATUS_TIMEOUT_SECONDS,
            env=_cli_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "installed": True,
            "authenticated": False,
            "version": "",
            "error": "command-code status timed out.",
        }
    except OSError as exc:
        return {
            "installed": True,
            "authenticated": False,
            "version": "",
            "error": f"Unable to run command-code status: {exc}",
        }

    payload = _parse_json_line(result.stdout)
    authenticated = bool(payload and payload.get("authenticated") is True)
    version = str(payload.get("version") or "") if payload else ""
    user = str(payload.get("user") or "").strip() if payload else ""

    if authenticated:
        return {
            "installed": True,
            "authenticated": True,
            "version": version,
            "user": user or "Authenticated",
        }

    detail = (result.stderr or "").strip() or (
        "Command Code is not authenticated. Run `command-code login`."
        if payload
        else "command-code status returned an unreadable response."
    )
    return {
        "installed": True,
        "authenticated": False,
        "version": version,
        "error": detail,
    }


def list_models() -> list[str]:
    """Best-effort parse of `command-code --list-models`'s table output.

    The CLI has no `--json` form of this listing (confirmed against v1.47.0).
    Each model row starts with a `vendor/model-id` token followed by
    whitespace-separated description text; section headers (e.g. "Open
    Source") and the summary line have no such token and are skipped.
    """
    try:
        result = subprocess.run(
            [COMMAND_CODE_BINARY, "--list-models"],
            capture_output=True,
            text=True,
            timeout=STATUS_TIMEOUT_SECONDS,
            env=_cli_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    models: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        token = stripped.split(None, 1)[0]
        if "/" in token and not token.startswith("/") and not token.endswith("/"):
            models.append(token)
    return models


def run_prompt(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: int = RUN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Runs one stateless Command Code headless turn and returns its result.

    Spawns `command-code -p <prompt> --output-format json --no-auto-update
    --skip-onboarding [-m <model>]` and reads the terminal NDJSON
    `{"type":"result",...}` frame's `finalText`/`error`/`usage` -- shape
    confirmed directly against the installed CLI (v1.47.0):
    `{"type":"result","subtype":"error"|"success","usage":{"inputTokens":N,
    "outputTokens":N,...},"finalText":"...","error":"..."}`.

    Each call is a fresh, stateless prompt (no `--resume`): Agent Zero, like
    every model backend it talks to, sends its full conversation state on
    every turn, so session continuity is unnecessary here.
    """
    prompt = _flatten_messages(messages)
    if not prompt:
        return {"ok": False, "text": "", "error": "No prompt content to send.", "usage": {}}

    args = [
        COMMAND_CODE_BINARY,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--no-auto-update",
        "--skip-onboarding",
    ]
    if model:
        args.extend(["-m", model])

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=_cli_env()
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "text": "",
            "error": f"command-code timed out after {timeout}s.",
            "usage": {},
        }
    except OSError as exc:
        return {"ok": False, "text": "", "error": f"Unable to run command-code: {exc}", "usage": {}}

    frame = _last_result_frame(result.stdout)
    if frame is None:
        detail = (result.stderr or "").strip() or "command-code produced no result."
        return {"ok": False, "text": "", "error": detail, "usage": {}}

    usage = frame.get("usage") if isinstance(frame.get("usage"), dict) else {}
    final_text = str(frame.get("finalText") or "")
    if frame.get("subtype") == "success":
        return {"ok": True, "text": final_text, "error": "", "usage": usage}

    error = str(frame.get("error") or "command-code reported an error.")
    return {"ok": False, "text": final_text, "error": error, "usage": usage}


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Flattens an OpenAI-style messages array into one prompt string.

    Command Code's headless `-p` mode takes a single prompt argument, not a
    messages array.
    """
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict)
            )
        else:
            text = str(content or "")
        text = text.strip()
        if not text:
            continue
        label = {"system": "System", "assistant": "Assistant"}.get(role, "User")
        parts.append(f"[{label}]\n{text}")
    return "\n\n".join(parts)


def _last_result_frame(stdout: str) -> dict[str, Any] | None:
    frame: dict[str, Any] | None = None
    for line in stdout.splitlines():
        parsed = _parse_json_line(line)
        if parsed and parsed.get("type") == "result":
            frame = parsed
    return frame


def _parse_json_line(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
        for line in reversed(text.splitlines()):
            try:
                parsed = json.loads(line.strip())
                break
            except json.JSONDecodeError:
                continue
        if parsed is None:
            return None
    return parsed if isinstance(parsed, dict) else None
