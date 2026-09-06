from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins._oauth.helpers import cursor_cli
from plugins._oauth.helpers.providers.base import CURSOR_PROVIDER_ID
from plugins._oauth.helpers.providers.cursor import (
    CURATED_MODELS,
    INSTALL_HINT,
    NOT_DRIVEN_MESSAGE,
    CursorCliOAuthProvider,
)


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_is_installed_true_when_version_exits_zero(monkeypatch):
    monkeypatch.setattr(
        cursor_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(0, "2026.09.02-c22c1a3\n"),
    )
    assert cursor_cli.is_installed() is True


def test_is_installed_false_when_binary_missing(monkeypatch):
    def raise_not_found(*a, **k):
        raise OSError("not found")

    monkeypatch.setattr(cursor_cli.subprocess, "run", raise_not_found)
    assert cursor_cli.is_installed() is False


def test_get_status_reports_not_installed(monkeypatch):
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: False)
    status = cursor_cli.get_status()
    assert status["installed"] is False
    assert status["authenticated"] is False
    assert INSTALL_HINT in status["error"]


def test_get_status_authenticated_via_cursor_api_key(monkeypatch):
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: True)
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.02-c22c1a3")
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "fake-key")

    status = cursor_cli.get_status()
    assert status["authenticated"] is True
    assert "CURSOR_API_KEY" in status["user"]


def test_get_status_authenticated_via_api_key_cursor_fallback_var(monkeypatch):
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: True)
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.02-c22c1a3")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY_CURSOR", "fake-key")

    status = cursor_cli.get_status()
    assert status["authenticated"] is True
    assert "API_KEY_CURSOR" in status["user"]


def test_get_status_authenticated_via_real_cli_config_shape(monkeypatch, tmp_path):
    # Shape confirmed against the real installed CLI (v2026.09.02-c22c1a3):
    # a logged-in session populates authInfo in cli-config.json -- there is
    # no separate token file.
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: True)
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.02-c22c1a3")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setattr(cursor_cli, "credentials_home", lambda: tmp_path)

    config = tmp_path / "cli-config.json"
    config.write_text(
        '{"version":1,"authInfo":{"userId":325785915,"email":"user@example.com",'
        '"displayName":"Example User","authId":"auth0|user_fake"}}'
    )

    status = cursor_cli.get_status()
    assert status["authenticated"] is True
    assert status["version"] == "2026.09.02-c22c1a3"
    assert status["user"] == "user@example.com"


def test_get_status_not_authenticated_when_config_has_no_auth_info(monkeypatch, tmp_path):
    # Shape confirmed against the real CLI before login: cli-config.json
    # exists (default settings) but has no authInfo key at all.
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: True)
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.02-c22c1a3")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setattr(cursor_cli, "credentials_home", lambda: tmp_path)

    config = tmp_path / "cli-config.json"
    config.write_text('{"version":1,"notifications":true}')

    status = cursor_cli.get_status()
    assert status["authenticated"] is False
    assert "agent login" in status["error"]


def test_get_status_not_authenticated_when_no_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: True)
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.02-c22c1a3")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setattr(cursor_cli, "credentials_home", lambda: tmp_path)

    status = cursor_cli.get_status()
    assert status["authenticated"] is False
    assert "agent login" in status["error"]


def test_list_models_is_always_empty_no_selection_flag():
    assert cursor_cli.list_models() == []


def test_run_prompt_uses_plain_text_output_and_ignores_model(monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompletedProcess(0, "Hello there\n")

    monkeypatch.setattr(cursor_cli.subprocess, "run", fake_run)

    result = cursor_cli.run_prompt(
        [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Say hi"},
        ],
        model="gpt-5.1",
    )

    assert result["ok"] is True
    assert result["text"] == "Hello there"
    assert result["usage"] == {}

    args = captured["args"]
    assert args[0] == "agent"
    assert args[1] == "-p"
    assert "--output-format" in args and "text" in args
    assert "gpt-5.1" not in args
    assert "[System]\nBe terse." in args[-1]
    assert "[User]\nSay hi" in args[-1]


def test_run_prompt_reports_stderr_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        cursor_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(1, "", "Not authenticated. Run agent login.\n"),
    )
    result = cursor_cli.run_prompt([{"role": "user", "content": "hi"}])
    assert result["ok"] is False
    assert "Not authenticated" in result["error"]


def test_run_prompt_rejects_empty_message_content():
    result = cursor_cli.run_prompt([{"role": "user", "content": "   "}])
    assert result["ok"] is False
    assert "No prompt content" in result["error"]


def test_run_prompt_times_out_gracefully(monkeypatch):
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="agent", timeout=300)

    monkeypatch.setattr(cursor_cli.subprocess, "run", raise_timeout)
    result = cursor_cli.run_prompt([{"role": "user", "content": "hi"}])
    assert result["ok"] is False
    assert "timed out" in result["error"]


def test_provider_metadata_and_protocol_stubs():
    provider = CursorCliOAuthProvider()
    assert provider.provider_id == CURSOR_PROVIDER_ID

    metadata = provider.metadata()
    assert metadata.display_name == "Cursor CLI"
    assert metadata.auth_flow == "external_cli"
    assert metadata.default_models == CURATED_MODELS

    poll = provider.poll_login()
    assert poll.ok is False

    callback = provider.complete_callback({})
    assert callback.ok is False

    manual = provider.manual_callback({})
    assert manual.ok is False

    assert provider.api_key() == "oauth"


def test_start_login_never_installs_or_authenticates(monkeypatch):
    # Unlike Command Code/Claude Code, Cursor CLI's installer is a raw
    # curl-piped shell script, not a scoped package-manager install -- this
    # provider must never attempt it, installed or not.
    def fail_if_called(*a, **k):
        raise AssertionError("Cursor CLI install/login must never be automated")

    monkeypatch.setattr(cursor_cli, "is_installed", fail_if_called)

    provider = CursorCliOAuthProvider()
    result = provider.start_login()
    assert result.ok is False
    assert result.error == NOT_DRIVEN_MESSAGE
    assert "curl https://cursor.com/install" in result.error


def test_provider_disconnect_removes_owned_config_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setattr(cursor_cli, "credentials_home", lambda: tmp_path)

    config = tmp_path / "cli-config.json"
    config.write_text('{"authInfo":{"userId":1,"email":"user@example.com"}}')

    provider = CursorCliOAuthProvider()
    result = provider.disconnect()
    assert result["disconnected"] is True
    assert not config.exists()


def test_provider_disconnect_refuses_when_env_api_key_set(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "fake-key")
    provider = CursorCliOAuthProvider()
    result = provider.disconnect()
    assert result["disconnected"] is False
    assert "CURSOR_API_KEY" in result["note"]


def test_provider_status_reports_installed_and_authenticated(monkeypatch):
    monkeypatch.setattr(
        cursor_cli,
        "get_status",
        lambda: {
            "installed": True,
            "authenticated": True,
            "version": "2026.09.02-c22c1a3",
            "user": "user@example.com",
        },
    )
    provider = CursorCliOAuthProvider()
    status = provider.status()
    assert status["connected"] is True
    assert status["installed"] is True
    assert "error" not in status


def test_provider_status_surfaces_error_when_not_connected(monkeypatch):
    monkeypatch.setattr(
        cursor_cli,
        "get_status",
        lambda: {
            "installed": True,
            "authenticated": False,
            "version": "2026.09.02-c22c1a3",
            "error": "Not authenticated.",
        },
    )
    provider = CursorCliOAuthProvider()
    status = provider.status()
    assert status["connected"] is False
    assert status["error"] == "Not authenticated."


def test_provider_models_falls_back_to_curated_list(monkeypatch):
    monkeypatch.setattr(cursor_cli, "list_models", lambda: [])
    provider = CursorCliOAuthProvider()
    assert provider.models() == CURATED_MODELS


def test_provider_registers_routes_without_duplicate(monkeypatch):
    import types

    fake_routes = types.ModuleType("plugins._oauth.helpers.routes")
    fake_routes.cursor_cli_health = lambda: None
    fake_routes.cursor_cli_models = lambda: None
    fake_routes.cursor_cli_chat_completions = lambda: None
    monkeypatch.setitem(sys.modules, "plugins._oauth.helpers.routes", fake_routes)

    class FakeApp:
        def __init__(self):
            self.view_functions: dict = {}
            self.rules: list = []

        def add_url_rule(self, rule, endpoint, view_func, methods=None):
            self.view_functions[endpoint] = view_func
            self.rules.append((rule, endpoint, tuple(methods or [])))

    app = FakeApp()
    provider = CursorCliOAuthProvider()
    provider.register_routes(app)
    provider.register_routes(app)  # idempotent: must not register twice

    assert app.rules.count(
        ("/oauth/cursor-cli/v1/chat/completions", "oauth_cursor_cli_chat_completions", ("POST", "OPTIONS"))
    ) == 1
    assert "oauth_cursor_cli_health" in app.view_functions
    assert "oauth_cursor_cli_models" in app.view_functions
