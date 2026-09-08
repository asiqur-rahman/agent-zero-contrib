from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from plugins._oauth.helpers import cli_prompt, cli_runtime

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
NPM_PACKAGE = "@anthropic-ai/claude-code"
VERSION_TIMEOUT_SECONDS = 5
STATUS_TIMEOUT_SECONDS = 8
RUN_TIMEOUT_SECONDS = 300


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


def adoptable_credential_paths() -> list[Path]:
    """Non-persisted credential files _adopt_existing_credentials() may copy from.

    Exposed so disconnect() can clear them too -- see
    cli_runtime.purge_files() for why leaving them would make Disconnect a
    no-op.
    """
    candidates = [home / ".claude" / ".credentials.json" for home in cli_runtime.default_homes()]
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env_dir:
        candidates.insert(0, Path(env_dir).expanduser() / ".credentials.json")
    return candidates


def _adopt_existing_credentials() -> None:
    """Migrates a login left in a non-persisted config dir into the persisted one.

    CLAUDE_CONFIG_DIR is a container-wide ENV in the shipped image, so a
    `docker exec` login normally writes straight to the persisted
    directory. It does not when the container predates that ENV, when the
    image is not this repo's (a plain agent0ai/agent-zero with this
    checkout bind-mounted), or when the login ran with the variable unset
    -- in all of those the credential goes to `~/.claude`, which works
    when checked by hand but is invisible here and is destroyed by the
    next container recreate. Adopting it makes the persisted copy
    authoritative from then on.
    """
    try:
        target = credentials_path()
    except Exception:
        return
    cli_runtime.adopt_file(target, adoptable_credential_paths())


def _binary() -> str:
    return cli_runtime.resolve_binary(CLAUDE_BINARY)


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
    return cli_runtime.env_with_bin_path(env)


def npm_available() -> bool:
    return cli_runtime.npm_available()


def install_latest() -> dict[str, Any]:
    """Installs `@anthropic-ai/claude-code@latest` into the persisted npm prefix.

    This only ever installs the CLI binary itself -- it never touches
    authentication. A successful install still requires the user to run
    `claude auth login` (or set ANTHROPIC_API_KEY) themselves; nothing here
    attempts to automate that, and it never will (see the module docstring).

    The prefix is under usr/ (see cli_runtime.npm_prefix()) rather than
    npm's default /usr/local, so the install survives a container
    recreation -- otherwise every image update silently un-installs the CLI
    and this provider reports "not installed" even though the persisted
    login is still there.
    """
    if not npm_available():
        return {
            "ok": False,
            "error": "npm is not available on PATH. Install Node.js first, then retry.",
        }
    return cli_runtime.npm_install_global(NPM_PACKAGE)


def is_installed() -> bool:
    try:
        result = subprocess.run(
            [_binary(), "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            env=_cli_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _version() -> str:
    try:
        result = subprocess.run(
            [_binary(), "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            env=_cli_env(),
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

    _adopt_existing_credentials()
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
    json` mode. This behaves as a plain text completion the same way Command
    Code's `-p` does, not an agentic coding run: no --permission-mode is
    passed, and the only tool ever allowed is Read, only on turns that
    carry an image attachment (see cli_prompt.py -- the CLI has to open the
    image off disk, because its headless mode takes one text argument).

    Each call is a fresh, stateless prompt (no `--resume` / `--continue`):
    Agent Zero sends its full conversation state on every turn, the same
    reasoning already documented in command_code_cli.run_prompt().
    """
    prompt = cli_prompt.build_prompt(messages)
    if not prompt.has_content:
        return {"ok": False, "text": "", "error": "No prompt content to send.", "usage": {}}

    _adopt_existing_credentials()

    args = [_binary(), "-p", prompt.text, "--output-format", "json"]
    if model:
        args.extend(["--model", model])
    if prompt.image_paths:
        # The one tool this provider ever grants, and only when the request
        # actually carries an attachment: without it the CLI cannot open the
        # image files the prompt points at, and the turn degrades to the
        # same silent drop this replaced. Read is read-only and no
        # --permission-mode is passed (bypassPermissions is rejected under
        # root anyway, see the orchestrator's claude.md reference).
        args.extend(["--allowedTools", "Read"])

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
    finally:
        prompt.cleanup()

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
