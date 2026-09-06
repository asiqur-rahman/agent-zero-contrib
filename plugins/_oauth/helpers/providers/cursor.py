from __future__ import annotations

import os
from typing import Any

from plugins._oauth.helpers.providers.base import (
    CURSOR_PROVIDER_ID,
    DUMMY_API_KEY,
    CallbackResult,
    LoginPollResult,
    LoginStartResult,
    OAuthProviderMetadata,
)

# Cursor CLI selects models via its own interactive `/model` command, not a
# CLI flag this provider could drive per-request (see cursor_cli.list_models()
# / run_prompt()) -- so there is nothing meaningful to curate a multi-entry
# list from. This single placeholder entry documents that every request goes
# through whichever model Cursor CLI itself is currently configured to use.
CURATED_MODELS = ["auto"]

# Confirmed live against the real CLI: Cursor does NOT respect a
# CURSOR_HOME override, it always writes to $HOME/.cursor. This is the
# same shared, persisted HOME directory command_code_cli.py already uses
# (see cursor_cli.py's module docstring for why) -- not a cursor-specific
# path, despite the name.
PERSISTED_HOME_PATH = "/a0/usr/plugins/_oauth/command_code_cli/home"
INSTALL_HINT = "curl https://cursor.com/install -fsS | bash"

NOT_DRIVEN_MESSAGE = (
    "Cursor CLI sign-in is not driven from Agent Zero, and unlike Command "
    "Code/Claude Code this provider does not auto-install the CLI either -- "
    f"its installer ({INSTALL_HINT}) is a raw shell script piped from a "
    "URL, not a scoped package-manager install, so it is never run "
    f"automatically. On the machine running Agent Zero: install with "
    f"`{INSTALL_HINT}`, then sign in with `agent` or `NO_OPEN_BROWSER=1 "
    "agent login` (or set CURSOR_API_KEY/API_KEY_CURSOR), then click "
    "Refresh here. A `docker exec` shell already persists this correctly "
    "on its own (this container's shell exports HOME to a persisted path "
    "for exactly this reason, since Cursor CLI itself only honors $HOME, "
    f"not CURSOR_HOME) -- only export HOME=\"{PERSISTED_HOME_PATH}\" "
    "yourself if running natively outside Docker."
)


class CursorCliOAuthProvider:
    """Wraps the locally installed Cursor `agent` CLI as an account provider.

    Same external-CLI pattern as Command Code/Claude Code, with one
    deliberate difference: `start_login` never attempts to install
    anything. See helpers/cursor_cli.py's module docstring and
    NOT_DRIVEN_MESSAGE above for why (curl-piped-to-bash vs. a scoped `npm
    install -g`).
    """

    provider_id = CURSOR_PROVIDER_ID

    def metadata(self) -> OAuthProviderMetadata:
        return OAuthProviderMetadata(
            provider_id=CURSOR_PROVIDER_ID,
            display_name="Cursor CLI",
            short_name="Cursor CLI",
            model_provider_id=CURSOR_PROVIDER_ID,
            icon="cursor_cli",
            auth_flow="external_cli",
            default_model=CURATED_MODELS[0],
            default_models=list(CURATED_MODELS),
            proxy_base_path="/oauth/cursor-cli",
            note=NOT_DRIVEN_MESSAGE,
        )

    def status(self) -> dict[str, Any]:
        from plugins._oauth.helpers import cursor_cli

        info = cursor_cli.get_status()
        result: dict[str, Any] = {
            **self.metadata().to_dict(),
            "connected": bool(info.get("authenticated")),
            "account_label": info.get("user") or ("Authenticated" if info.get("authenticated") else ""),
            "installed": bool(info.get("installed")),
            "version": info.get("version") or "",
        }
        if not info.get("authenticated"):
            result["error"] = info.get("error") or NOT_DRIVEN_MESSAGE
        return result

    def start_login(self, input: dict[str, Any] | None = None, request: Any = None) -> LoginStartResult:
        # Neither installation nor login is ever automated here -- see the
        # module docstring / NOT_DRIVEN_MESSAGE for why this differs from
        # Command Code/Claude Code's auto-install-only behavior.
        del input, request
        return LoginStartResult(
            ok=False,
            provider_id=CURSOR_PROVIDER_ID,
            flow="external_cli",
            error=NOT_DRIVEN_MESSAGE,
            message=NOT_DRIVEN_MESSAGE,
        )

    def poll_login(self, input: dict[str, Any] | None = None, request: Any = None) -> LoginPollResult:
        del input, request
        return LoginPollResult(ok=False, provider_id=CURSOR_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def complete_callback(self, args: dict[str, Any], request: Any = None) -> CallbackResult:
        del args, request
        return CallbackResult(ok=False, provider_id=CURSOR_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def manual_callback(self, input: dict[str, Any], request: Any = None) -> LoginPollResult:
        del input, request
        return LoginPollResult(ok=False, provider_id=CURSOR_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def models(self) -> list[str]:
        from plugins._oauth.helpers import cursor_cli

        try:
            models = cursor_cli.list_models()
        except Exception:
            models = []
        return models or list(CURATED_MODELS)

    def disconnect(self) -> dict[str, Any]:
        # The shared HOME directory is not exclusively ours (Command Code
        # keeps its own dotfiles there too, see PERSISTED_HOME_PATH above),
        # but Cursor's own `.cursor/` subdirectory under it is -- safe to
        # remove just that.
        from plugins._oauth.helpers import cursor_cli

        env_var = next(
            (name for name in ("CURSOR_API_KEY", "API_KEY_CURSOR") if os.environ.get(name)),
            "",
        )
        if env_var:
            return {
                "disconnected": False,
                "note": f"{env_var} is set in the environment. Unset it to sign out.",
            }

        path = cursor_cli.config_path()
        if path.exists():
            path.unlink()
            return {"disconnected": True}
        return {"disconnected": False, "note": "No Cursor CLI credentials found."}

    def api_key(self) -> str:
        return DUMMY_API_KEY

    def register_routes(self, app: Any) -> None:
        from plugins._oauth.helpers import routes

        route_defs = [
            (
                "/oauth/cursor-cli/health",
                "oauth_cursor_cli_health",
                routes.cursor_cli_health,
                ["GET"],
            ),
            (
                "/oauth/cursor-cli/v1/models",
                "oauth_cursor_cli_models",
                routes.cursor_cli_models,
                ["GET", "OPTIONS"],
            ),
            (
                "/oauth/cursor-cli/v1/chat/completions",
                "oauth_cursor_cli_chat_completions",
                routes.cursor_cli_chat_completions,
                ["POST", "OPTIONS"],
            ),
        ]
        for rule, endpoint, view_func, methods in route_defs:
            if endpoint in app.view_functions:
                continue
            app.add_url_rule(rule, endpoint, view_func, methods=methods)
