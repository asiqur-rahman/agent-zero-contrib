from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

# Anthropic's Claude Code CLI (https://docs.claude.com/claude-code), like
# Command Code, has no third-party OAuth/REST API -- this provider shells out
# to the locally installed `claude` binary and drives its own headless print
# contract (`-p --output-format json`), confirmed against the CLI's public
# documentation and mirrored from this repo's own
# plugins/_orchestrator/skills/orchestrator/references/claude.md. The user
# authenticates with `claude auth login` (or an ANTHROPIC_API_KEY env var)
# themselves, outside Agent Zero; this module only ever reads that state.
#
# Command Code's credential lives in the CLI's own hardcoded ~/.commandcode,
# which sits outside the one directory this container persists across
# restarts/recreates (usr/, see provider_data_dir()) -- that mismatch is why
# a Command Code login stops surviving a redeploy. Claude Code's CLI
# natively supports CLAUDE_CONFIG_DIR to relocate its config/credentials, so
# this module points it at a directory under usr/ from the start.
CLAUDE_BINARY = "claude"
NPM_BINARY = "npm"
NPM_PACKAGE = "@anthropic-ai/claude-code"
VERSION_TIMEOUT_SECONDS = 5
STATUS_TIMEOUT_SECONDS = 8
RUN_TIMEOUT_SECONDS = 300
INSTALL_TIMEOUT_SECONDS = 180


def _persisted_config_dir() -> Path:
    from plugins._oauth.helpers.providers.base import CLAUDE_CODE_PROVIDER_ID, provider_data_dir

    config_dir = provider_data_dir(CLAUDE_CODE_PROVIDER_ID) / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def _fallback_config_dir() -> Path:
    # Same default plugins/_orchestrator/helpers/adapters/claude.py falls
    # back to when no override is set -- used here only if the persisted
    # provider_data_dir() path is unavailable (e.g. an isolated unit test
    # without the full Agent Zero `helpers.files` module loaded).
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"


def credentials_path() -> Path:
    try:
        return _persisted_config_dir() / ".credentials.json"
    except Exception:
        return _fallback_config_dir() / ".credentials.json"


def _cli_env() -> dict[str, str]:
    env = {**os.environ, "NO_COLOR": "1"}
    try:
        env["CLAUDE_CONFIG_DIR"] = str(_persisted_config_dir())
    except Exception:
        # provider_data_dir() imports the full Agent Zero `helpers.files`
        # module, which is not available in isolated unit-test runs -- fall
        # back to the unmodified environment (no persisted config override)
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
    """Runs `npm install -g @anthropic-ai/claude-code`.

    This only ever installs the CLI binary itself -- it never touches
    authentication. A successful install still requires the user to run
    `claude auth login` (or set ANTHROPIC_API_KEY) themselves; nothing here
    attempts to automate that, and it never will (see the module docstring).
    """
    if not npm_available():
        return {
            "ok": False,
            "error": "npm is not available on PATH. Install Node.js first, then retry.",
        }

    try:
        result = subprocess.run(
            [NPM_BINARY, "install", "-g", NPM_PACKAGE],
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
        detail = detail[-500:] if detail else "npm install failed."
        return {"ok": False, "error": detail}

    return {"ok": True, "error": ""}


def is_installed() -> bool:
    try:
        result = subprocess.run(
            [CLAUDE_BINARY, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _version() -> str:
    try:
        result = subprocess.run(
            [CLAUDE_BINARY, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def get_status() -> dict[str, Any]:
    """Reports install/auth state without ever printing a credential.

    Claude Code has no documented `status --json` contract to shell out to
    (unlike Command Code), so auth is detected the same way
    plugins/_orchestrator/helpers/adapters/claude.py already does: an
    ANTHROPIC_API_KEY env var, or a non-empty credentials file under the
    CLI's config dir -- here, the persisted one this module points
    CLAUDE_CONFIG_DIR at.
    """
    if not is_installed():
        return {
            "installed": False,
            "authenticated": False,
            "version": "",
            "error": (
                "Claude Code CLI is not installed. "
                f"Install it with: npm i -g {NPM_PACKAGE}"
            ),
        }

    version = _version()

    if os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "installed": True,
            "authenticated": True,
            "version": version,
            "user": "Authenticated (ANTHROPIC_API_KEY)",
        }

    path = credentials_path()
    try:
        if path.is_file() and path.stat().st_size > 0:
            return {
                "installed": True,
                "authenticated": True,
                "version": version,
                "user": "Authenticated",
            }
    except OSError as exc:
        return {
            "installed": True,
            "authenticated": False,
            "version": version,
            "error": str(exc),
        }

    return {
        "installed": True,
        "authenticated": False,
        "version": version,
        "error": "Claude Code is not authenticated. Run `claude auth login` or set ANTHROPIC_API_KEY.",
    }


def list_models() -> list[str]:
    """Claude Code has no `--list-models`-style catalog to introspect.

    Always returns empty so callers fall back to a curated static list --
    unlike Command Code's `--list-models` table, there is no documented
    live-listing surface for this CLI to parse here.
    """
    return []


def run_prompt(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: int = RUN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Runs one stateless Claude Code headless turn and returns its result.

    Spawns `claude -p <prompt> --output-format json [--model <model>]` and
    reads the terminal result object's `result`/`is_error`/`usage` fields --
    the documented shape of Claude Code's non-streaming `--output-format
    json` mode. No tool permissions are granted (no --allowedTools /
    --permission-mode flags), so this behaves as a plain text completion
    the same way Command Code's `-p` does, not an agentic coding run.

    Each call is a fresh, stateless prompt (no `--resume` / `--continue`):
    Agent Zero sends its full conversation state on every turn, the same
    reasoning already documented in command_code_cli.run_prompt().
    """
    prompt = _flatten_messages(messages)
    if not prompt:
        return {"ok": False, "text": "", "error": "No prompt content to send.", "usage": {}}

    args = [CLAUDE_BINARY, "-p", prompt, "--output-format", "json"]
    if model:
        args.extend(["--model", model])

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_cli_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "text": "",
            "error": f"claude timed out after {timeout}s.",
            "usage": {},
        }
    except OSError as exc:
        return {"ok": False, "text": "", "error": f"Unable to run claude: {exc}", "usage": {}}

    payload = _parse_result_json(result.stdout)
    if payload is None:
        detail = (result.stderr or "").strip() or "claude produced no parseable result."
        return {"ok": False, "text": "", "error": detail, "usage": {}}

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    text = str(payload.get("result") or "")

    if not payload.get("is_error"):
        return {"ok": True, "text": text, "error": "", "usage": usage}

    error = text or "claude reported an error."
    return {"ok": False, "text": "", "error": error, "usage": usage}


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Flattens an OpenAI-style messages array into one prompt string.

    Claude Code's headless `-p` mode takes a single prompt argument, not a
    messages array -- same reasoning as command_code_cli._flatten_messages().
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


def _parse_result_json(stdout: str) -> dict[str, Any] | None:
    text = (stdout or "").strip()
    if not text:
        return None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        return payload

    # Defensive fallback: scan line by line for a `type: result` object, in
    # case a future CLI version emits NDJSON (e.g. under stream-json) instead
    # of the single-object shape --output-format json documents today.
    frame: dict[str, Any] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "result":
            frame = parsed
    return frame
