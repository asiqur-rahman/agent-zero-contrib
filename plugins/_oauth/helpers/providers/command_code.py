from __future__ import annotations

from typing import Any

from plugins._oauth.helpers.providers.base import (
    COMMAND_CODE_PROVIDER_ID,
    DUMMY_API_KEY,
    CallbackResult,
    LoginPollResult,
    LoginStartResult,
    OAuthProviderMetadata,
)

# Fallback only -- models() always prefers the live `--list-models` catalog
# and uses this solely when that call fails or the CLI is not yet installed.
CURATED_MODELS = [
    "anthropic/claude-sonnet-5",
    "openai/gpt-5.1",
    "google/gemini-3-pro",
]

NOT_DRIVEN_MESSAGE = (
    "Command Code sign-in is not driven from Agent Zero. On the machine "
    "running Agent Zero, install the CLI (npm i -g command-code) and run "
    "`command-code login` yourself, then click Refresh here."
)


class CommandCodeOAuthProvider:
    """Wraps the locally installed `command-code` CLI as an account provider.

    Unlike the other providers in this plugin, this one drives no OAuth
    handshake at all -- Command Code (https://commandcode.ai) publishes no
    REST/OAuth API for third parties. It instead uses the CLI's own public
    contract (`status --json`, `-p --output-format json`) the same way the
    reference claudecodeui-contrib integration does. See
    helpers/command_code_cli.py for the subprocess mechanics.
    """

    provider_id = COMMAND_CODE_PROVIDER_ID

    def metadata(self) -> OAuthProviderMetadata:
        return OAuthProviderMetadata(
            provider_id=COMMAND_CODE_PROVIDER_ID,
            display_name="Command Code",
            short_name="Command Code",
            model_provider_id=COMMAND_CODE_PROVIDER_ID,
            icon="command_code",
            auth_flow="external_cli",
            default_model=CURATED_MODELS[0],
            default_models=list(CURATED_MODELS),
            proxy_base_path="/oauth/command-code",
            note=NOT_DRIVEN_MESSAGE,
        )

    def status(self) -> dict[str, Any]:
        from plugins._oauth.helpers import command_code_cli

        info = command_code_cli.get_status()
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
        # There is no login flow to start -- Command Code sign-in always
        # happens outside Agent Zero (see NOT_DRIVEN_MESSAGE / the module
        # docstring). What this DOES do on a "Connect" click is the one part
        # that is safe to automate: installing the CLI itself. `npm install
        # -g` touches no credentials and requires no account -- it is pure
        # software installation, not the login step. If it's already
        # installed, or npm isn't available, or the install fails, this
        # still always returns ok=False with an explanatory message; the
        # actual authentication step is never attempted here.
        del input, request
        from plugins._oauth.helpers import command_code_cli

        if command_code_cli.is_installed():
            return LoginStartResult(
                ok=False,
                provider_id=COMMAND_CODE_PROVIDER_ID,
                flow="external_cli",
                error=NOT_DRIVEN_MESSAGE,
                message=NOT_DRIVEN_MESSAGE,
            )

        install = command_code_cli.install_latest()
        if install.get("ok"):
            message = (
                "Installed the Command Code CLI. "
                + NOT_DRIVEN_MESSAGE
            )
        else:
            message = f"{NOT_DRIVEN_MESSAGE} Automatic install failed: {install.get('error')}"

        return LoginStartResult(
            ok=False,
            provider_id=COMMAND_CODE_PROVIDER_ID,
            flow="external_cli",
            error=message,
            message=message,
        )

    def poll_login(self, input: dict[str, Any] | None = None, request: Any = None) -> LoginPollResult:
        del input, request
        return LoginPollResult(ok=False, provider_id=COMMAND_CODE_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def complete_callback(self, args: dict[str, Any], request: Any = None) -> CallbackResult:
        del args, request
        return CallbackResult(ok=False, provider_id=COMMAND_CODE_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def manual_callback(self, input: dict[str, Any], request: Any = None) -> LoginPollResult:
        del input, request
        return LoginPollResult(ok=False, provider_id=COMMAND_CODE_PROVIDER_ID, error=NOT_DRIVEN_MESSAGE)

    def models(self) -> list[str]:
        from plugins._oauth.helpers import command_code_cli

        try:
            models = command_code_cli.list_models()
        except Exception:
            models = []
        return models or list(CURATED_MODELS)

    def disconnect(self) -> dict[str, Any]:
        # Agent Zero never held this credential -- it lives in the CLI's own
        # ~/.commandcode directory, shared with any other tool using it on
        # this machine. Signing it out from here would be a surprising,
        # system-wide side effect, so this deliberately does not shell out
        # to `command-code logout`.
        return {
            "disconnected": False,
            "note": (
                "Agent Zero cannot sign Command Code out remotely -- it never "
                "held the credential. Run `command-code logout` in a terminal "
                "on the machine running Agent Zero to sign out."
            ),
        }

    def api_key(self) -> str:
        return DUMMY_API_KEY

    def register_routes(self, app: Any) -> None:
        from plugins._oauth.helpers import routes

        route_defs = [
            (
                "/oauth/command-code/health",
                "oauth_command_code_health",
                routes.command_code_health,
                ["GET"],
            ),
            (
                "/oauth/command-code/v1/models",
                "oauth_command_code_models",
                routes.command_code_models,
                ["GET", "OPTIONS"],
            ),
            (
                "/oauth/command-code/v1/chat/completions",
                "oauth_command_code_chat_completions",
                routes.command_code_chat_completions,
                ["POST", "OPTIONS"],
            ),
        ]
        for rule, endpoint, view_func, methods in route_defs:
            if endpoint in app.view_functions:
                continue
            app.add_url_rule(rule, endpoint, view_func, methods=methods)
