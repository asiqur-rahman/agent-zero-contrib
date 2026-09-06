from __future__ import annotations

import os
from typing import Any

from plugins._oauth.helpers.providers.base import (
    CLAUDE_CODE_PROVIDER_ID,
    DUMMY_API_KEY,
    CallbackResult,
    LoginPollResult,
    LoginStartResult,
    OAuthProviderMetadata,
)

# Fallback only -- models() always prefers a live catalog when one becomes
# available and uses this solely when the CLI has no listing surface (which
# is currently always, see claude_code_cli.list_models()) or is not
# installed yet.
CURATED_MODELS = [
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-4-5",
]

PERSISTED_CONFIG_DIR_PATH = "/a0/usr/plugins/_oauth/claude_code_cli/config"

NOT_DRIVEN_MESSAGE = (
    "Claude Code sign-in is not driven from Agent Zero. On the machine "
    "running Agent Zero, install the CLI (npm i -g @anthropic-ai/claude-code) "
    "and run `claude auth login` yourself (or set ANTHROPIC_API_KEY), then "
    "click Refresh here. The shipped Docker image already sets "
    f"CLAUDE_CONFIG_DIR={PERSISTED_CONFIG_DIR_PATH}, so a plain `claude auth "
    "login` in a `docker exec` shell persists correctly on its own -- only "
    "export CLAUDE_CONFIG_DIR yourself if running natively outside Docker."
)


class ClaudeCodeOAuthProvider:
    """Wraps the locally installed `claude` CLI as an account provider.

    Same pattern as CommandCodeOAuthProvider: Anthropic's Claude Code CLI
    publishes no third-party OAuth/REST API, so this provider drives no
    handshake at all -- it uses the CLI's own public headless contract
    (`-p --output-format json`) the same way
    plugins/_orchestrator/helpers/adapters/claude.py already detects auth
    state for the (architecturally separate) task-delegation plugin. See
    helpers/claude_code_cli.py for the subprocess mechanics and for why this
    provider, unlike Command Code, can point the CLI's own config dir at a
    directory this container actually persists across restarts.
    """

    provider_id = CLAUDE_CODE_PROVIDER_ID

    def metadata(self) -> OAuthProviderMetadata:
        return OAuthProviderMetadata(
            provider_id=CLAUDE_CODE_PROVIDER_ID,
            display_name="Claude Code",
            short_name="Claude Code",
            model_provider_id=CLAUDE_CODE_PROVIDER_ID,
            icon="claude_code",
            auth_flow="external_cli",
            default_model=CURATED_MODELS[0],
            default_models=list(CURATED_MODELS),
            proxy_base_path="/oauth/claude-code",
            note=NOT_DRIVEN_MESSAGE,
        )

    def status(self) -> dict[str, Any]:
        from plugins._oauth.helpers import claude_code_cli

        info = claude_code_cli.get_status()
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
        # There is no login flow to start -- Claude Code sign-in always
        # happens outside Agent Zero (see NOT_DRIVEN_MESSAGE / the module
        # docstring). What this DOES do on a "Connect" click is the one part
        # that is safe to automate: installing the CLI itself. `npm install
        # -g` touches no credentials and requires no account. If it's
        # already installed, or npm isn't available, or the install fails,
        # this still always returns ok=False with an explanatory message;
        # the actual authentication step is never attempted here.
        del input, request
        from plugins._oauth.helpers import claude_code_cli

        if claude_code_cli.is_installed():
            return LoginStartResult(
                ok=False,
                provider_id=CLAUDE_CODE_PROVIDER_ID,
                flow="external_cli",
                error=NOT_DRIVEN_MESSAGE,
                message=NOT_DRIVEN_MESSAGE,
            )

        install = claude_code_cli.install_latest()
        if install.get("ok"):
            message = "Installed the Claude Code CLI. " + NOT_DRIVEN_MESSAGE
        else:
            message = f"{NOT_DRIVEN_MESSAGE} Automatic install failed: {install.get('error')}"

        return LoginStartResult(
            ok=False,
            provider_id=CLAUDE_CODE_PROVIDER_ID,
            flow="external_cli",
            error=message,
            message=message,
        )

    def poll_login(self, input: dict[str, Any] | None = None, request: Any = None) -> LoginPollResult:
        del input, request
        return LoginPollResult(ok=False, provider_id=CLAUDE_CODE_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def complete_callback(self, args: dict[str, Any], request: Any = None) -> CallbackResult:
        del args, request
        return CallbackResult(ok=False, provider_id=CLAUDE_CODE_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def manual_callback(self, input: dict[str, Any], request: Any = None) -> LoginPollResult:
        del input, request
        return LoginPollResult(ok=False, provider_id=CLAUDE_CODE_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def models(self) -> list[str]:
        from plugins._oauth.helpers import claude_code_cli

        try:
            models = claude_code_cli.list_models()
        except Exception:
            models = []
        return models or list(CURATED_MODELS)

    def disconnect(self) -> dict[str, Any]:
        # Unlike Command Code's ~/.commandcode (a system-wide, shared
        # directory this plugin never owned), this provider's own
        # CLAUDE_CONFIG_DIR points at a directory this plugin created and
        # owns exclusively (usr/plugins/_oauth/claude_code_cli/config), so
        # it is safe to remove the credential file directly -- matching
        # plugins/_orchestrator/helpers/adapters/claude.py's disconnect().
        from plugins._oauth.helpers import claude_code_cli

        if os.environ.get("ANTHROPIC_API_KEY"):
            return {
                "disconnected": False,
                "note": "ANTHROPIC_API_KEY is set in the environment. Unset it to sign out.",
            }
        path = claude_code_cli.credentials_path()
        if path.exists():
            path.unlink()
            return {"disconnected": True}
        return {"disconnected": False, "note": "No Claude Code credentials found."}

    def api_key(self) -> str:
        return DUMMY_API_KEY

    def register_routes(self, app: Any) -> None:
        from plugins._oauth.helpers import routes

        route_defs = [
            (
                "/oauth/claude-code/health",
                "oauth_claude_code_health",
                routes.claude_code_health,
                ["GET"],
            ),
            (
                "/oauth/claude-code/v1/models",
                "oauth_claude_code_models",
                routes.claude_code_models,
                ["GET", "OPTIONS"],
            ),
            (
                "/oauth/claude-code/v1/chat/completions",
                "oauth_claude_code_chat_completions",
                routes.claude_code_chat_completions,
                ["POST", "OPTIONS"],
            ),
        ]
        for rule, endpoint, view_func, methods in route_defs:
            if endpoint in app.view_functions:
                continue
            app.add_url_rule(rule, endpoint, view_func, methods=methods)
