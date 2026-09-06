from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

# Cursor CLI (https://cursor.com/cli) also publishes no third-party
# OAuth/REST API -- this provider shells out to the locally installed
# `agent` binary the same way Command Code/Claude Code do, using the
# headless contract confirmed against
# plugins/_orchestrator/skills/orchestrator/references/cursor.md:
# `agent -p --output-format text "<prompt>"`.
#
# IMPORTANT, confirmed live against the real CLI (v2026.09.02-c22c1a3):
# Cursor CLI does NOT respect a CURSOR_HOME override -- it always writes
# its config to `$HOME/.cursor/cli-config.json`, regardless of any
# CURSOR_HOME env var (this contradicts the assumption this module
# originally copied from plugins/_orchestrator/helpers/adapters/cursor.py,
# which itself has not been verified against a real CLI session). Since
# only $HOME actually relocates Cursor's config, and Command Code (see
# command_code_cli.py) already needs the exact same kind of $HOME
# relocation for the exact same reason (no scoped override exists for it
# either), this module shares Command Code's persisted HOME directory
# rather than maintaining a second one -- both tools' dotfiles
# (~/.commandcode, ~/.cursor) simply live side by side under it, the same
# way a real $HOME holds many tools' dotfiles at once. /root/.bashrc and
# /root/.profile export HOME to this same shared path for interactive
# `docker exec` logins (see docker/run/fs/per/root/).
#
# The real cli-config.json has no separate token/secret field to scan for
# -- a logged-in session populates a top-level `authInfo` object with
# `userId`/`email`/`displayName`/`authId`. That is the only confirmed,
# reliable "are we logged in" signal for this CLI.
CURSOR_BINARY = "agent"
INSTALL_HINT = "curl https://cursor.com/install -fsS | bash"
VERSION_TIMEOUT_SECONDS = 5
RUN_TIMEOUT_SECONDS = 300


def _shared_cli_home() -> Path:
    # Same directory command_code_cli._persisted_home() computes -- see
    # the module docstring for why this is intentionally shared, not a
    # cursor-specific path.
    from plugins._oauth.helpers.providers.base import COMMAND_CODE_PROVIDER_ID, provider_data_dir

    home = provider_data_dir(COMMAND_CODE_PROVIDER_ID) / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _fallback_home() -> Path:
    # Used only if the persisted provider_data_dir() path is unavailable
    # (e.g. an isolated unit test without the full Agent Zero
    # `helpers.files` module loaded) -- falls back to whatever $HOME
    # already resolves to for this process.
    return Path.home()


def credentials_home() -> Path:
    try:
        home = _shared_cli_home()
    except Exception:
        home = _fallback_home()
    return home / ".cursor"


def config_path() -> Path:
    return credentials_home() / "cli-config.json"


def _cli_env() -> dict[str, str]:
    env = {**os.environ, "NO_COLOR": "1"}
    try:
        env["HOME"] = str(_shared_cli_home())
    except Exception:
        # provider_data_dir() imports the full Agent Zero `helpers.files`
        # module, which is not available in isolated unit-test runs -- fall
        # back to the unmodified environment rather than failing the whole
        # CLI call over it.
        pass
    return env


def is_installed() -> bool:
    try:
        result = subprocess.run(
            [CURSOR_BINARY, "--version"],
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
            [CURSOR_BINARY, "--version"],
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
    """Reports install/auth state by reading cli-config.json's authInfo.

    Confirmed live against the real CLI: a logged-in session populates
    authInfo.userId/email/displayName/authId in this file -- there is no
    separate token file to check, and no documented `agent status --json`
    to shell out to.
    """
    if not is_installed():
        return {
            "installed": False,
            "authenticated": False,
            "version": "",
            "error": f"Cursor CLI is not installed. Install it with: {INSTALL_HINT}",
        }

    version = _version()

    env_var = next(
        (name for name in ("CURSOR_API_KEY", "API_KEY_CURSOR") if os.environ.get(name)),
        "",
    )
    if env_var:
        return {
            "installed": True,
            "authenticated": True,
            "version": version,
            "user": f"Authenticated ({env_var})",
        }

    path = config_path()
    try:
        if not (path.is_file() and path.stat().st_size > 0):
            return {
                "installed": True,
                "authenticated": False,
                "version": version,
                "error": "Cursor CLI is not authenticated. Run `agent login` or set CURSOR_API_KEY.",
            }
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "installed": True,
            "authenticated": False,
            "version": version,
            "error": str(exc),
        }

    auth_info = payload.get("authInfo") if isinstance(payload, dict) else None
    auth_info = auth_info if isinstance(auth_info, dict) else {}
    user_id = str(auth_info.get("userId") or "").strip()
    email = str(auth_info.get("email") or "").strip()
    display_name = str(auth_info.get("displayName") or "").strip()

    if not user_id and not email:
        return {
            "installed": True,
            "authenticated": False,
            "version": version,
            "error": "Cursor CLI is not authenticated. Run `agent login` or set CURSOR_API_KEY.",
        }

    return {
        "installed": True,
        "authenticated": True,
        "version": version,
        "user": email or display_name or "Authenticated",
    }


def list_models() -> list[str]:
    # Cursor CLI selects models via its own interactive `/model` command,
    # not a CLI flag (confirmed in
    # plugins/_orchestrator/skills/orchestrator/references/cursor.md: "not
    # with an invented flag") -- there is no listing surface to introspect
    # or override here, so this always defers to the provider's curated
    # single-entry fallback.
    return []


def run_prompt(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: int = RUN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Runs one stateless Cursor CLI headless turn and returns its result.

    Spawns `agent -p --output-format text <prompt>` and returns stdout
    verbatim as the completion text. `model` is accepted for interface
    parity with the other providers but intentionally unused -- Cursor CLI
    has no confirmed per-call model-selection flag (see list_models()).
    Plain text output is used rather than --output-format json because
    this provider has no confirmed JSON result schema to parse against
    (unlike Command Code and Claude Code, both verified against real CLI
    output); usage stats are therefore always empty.
    """
    del model
    prompt = _flatten_messages(messages)
    if not prompt:
        return {"ok": False, "text": "", "error": "No prompt content to send.", "usage": {}}

    args = [CURSOR_BINARY, "-p", "--output-format", "text", prompt]

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
            "error": f"agent timed out after {timeout}s.",
            "usage": {},
        }
    except OSError as exc:
        return {"ok": False, "text": "", "error": f"Unable to run agent: {exc}", "usage": {}}

    if result.returncode == 0:
        return {"ok": True, "text": (result.stdout or "").strip(), "error": "", "usage": {}}

    detail = (result.stderr or result.stdout or "").strip() or "agent reported an error."
    return {"ok": False, "text": "", "error": detail, "usage": {}}


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Flattens an OpenAI-style messages array into one prompt string.

    Cursor CLI's headless `-p` mode takes a single prompt argument, not a
    messages array -- same reasoning as command_code_cli/claude_code_cli.
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
