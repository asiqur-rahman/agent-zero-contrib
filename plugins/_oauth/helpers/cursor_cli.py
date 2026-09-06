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
# Unlike Command Code (npm) and Claude Code (npm), Cursor CLI's official
# installer is `curl https://cursor.com/install -fsS | bash` -- an
# unscoped remote shell script, not a package-manager install. This
# provider deliberately never runs that automatically, even on "Connect"
# (npm install -g <package> is a scoped, registry-mediated install this
# plugin is comfortable automating; piping an arbitrary downloaded script
# into a shell is not). Install and login are always manual here.
#
# Auth is detected the same way plugins/_orchestrator/helpers/adapters/
# cursor.py already does -- CURSOR_API_KEY/API_KEY_CURSOR env vars, or a
# secret-shaped credential file under the CLI's home -- because `agent
# status` has no documented machine-readable (--json) contract to parse.
#
# Cursor CLI natively supports CURSOR_HOME to relocate that home
# directory (confirmed by the same orchestrator adapter), so -- like
# Claude Code and unlike Command Code -- this provider can point it at a
# directory under usr/ from the start and have logins survive a container
# recreation without a workaround.
CURSOR_BINARY = "agent"
INSTALL_HINT = "curl https://cursor.com/install -fsS | bash"
VERSION_TIMEOUT_SECONDS = 5
RUN_TIMEOUT_SECONDS = 300

_SECRET_KEYS = {"access_token", "api_key", "id_token", "refresh_token", "token"}
AUTH_FILES = (
    "auth.json",
    "credentials.json",
    "token.json",
    "agent/auth.json",
    "agent/credentials.json",
    "agent/token.json",
)


def _persisted_home() -> Path:
    from plugins._oauth.helpers.providers.base import CURSOR_PROVIDER_ID, provider_data_dir

    home = provider_data_dir(CURSOR_PROVIDER_ID) / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _fallback_home() -> Path:
    # Same default plugins/_orchestrator/helpers/adapters/cursor.py falls
    # back to when no override is set -- used only if the persisted
    # provider_data_dir() path is unavailable (e.g. an isolated unit test
    # without the full Agent Zero `helpers.files` module loaded).
    configured = os.environ.get("CURSOR_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".cursor"


def credentials_home() -> Path:
    try:
        return _persisted_home()
    except Exception:
        return _fallback_home()


def _cli_env() -> dict[str, str]:
    env = {**os.environ, "NO_COLOR": "1"}
    try:
        env["CURSOR_HOME"] = str(_persisted_home())
    except Exception:
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

    home = credentials_home()
    for relative in AUTH_FILES:
        path = home / relative
        try:
            if _file_has_secret(path):
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
        "error": "Cursor CLI is not authenticated. Run `agent login` or set CURSOR_API_KEY.",
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
    messages array -- same reasoning as claude_code_cli/command_code_cli.
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


def _file_has_secret(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        return _contains_secret(json.loads(text))
    except ValueError:
        return _text_has_secret(text)


def _text_has_secret(text: str) -> bool:
    return any(f'"{key}"' in text or f"{key} =" in text for key in _SECRET_KEYS)


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in _SECRET_KEYS and isinstance(item, str) and item.strip():
                return True
            if _contains_secret(item):
                return True
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False
