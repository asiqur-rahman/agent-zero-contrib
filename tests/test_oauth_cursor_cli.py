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
        lambda *a, **k: FakeCompletedProcess(0, "2026.09.01\n"),
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
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.01")
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "fake-key")

    status = cursor_cli.get_status()
    assert status["authenticated"] is True
    assert "CURSOR_API_KEY" in status["user"]


def test_get_status_authenticated_via_api_key_cursor_fallback_var(monkeypatch):
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: True)
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.01")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY_CURSOR", "fake-key")

    status = cursor_cli.get_status()
    assert status["authenticated"] is True
    assert "API_KEY_CURSOR" in status["user"]


def test_get_status_authenticated_via_credentials_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: True)
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.01")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setattr(cursor_cli, "credentials_home", lambda: tmp_path)

    (tmp_path / "auth.json").write_text('{"access_token":"fake"}')

    status = cursor_cli.get_status()
    assert status["authenticated"] is True
    assert status["version"] == "2026.09.01"


def test_get_status_not_authenticated_when_no_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(cursor_cli, "is_installed", lambda: True)
    monkeypatch.setattr(cursor_cli, "_version", lambda: "2026.09.01")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setattr(cursor_cli, "credentials_home", lambda: tmp_path)

    status = cursor_cli.get_status()
    assert status["authenticated"] is False
    assert "agent login" in status["error"]


def test_list_models_is_always_empty_no_selection_flag():
    # Cursor CLI selects models via its own /model command, not a CLI flag
    # this provider can drive -- always empty, so the provider falls back
    # to its single-entry curated placeholder.
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
    # model is intentionally never turned into a CLI flag
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


def test_provider_disconnect_removes_owned_credential_files(monkeypatch, tmp_path):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_CURSOR", raising=False)
    monkeypatch.setattr(cursor_cli, "credentials_home", lambda: tmp_path)

    (tmp_path / "auth.json").write_text('{"access_token":"fake"}')
    (tmp_path / "token.json").write_text('{"token":"fake"}')

    provider = CursorCliOAuthProvider()
    result = provider.disconnect()
    assert result["disconnected"] is True
    assert set(result["removed"]) == {"auth.json", "token.json"}
    assert not (tmp_path / "auth.json").exists()
    assert not (tmp_path / "token.json").exists()


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
        lambda: {"installed": True, "authenticated": True, "version": "2026.09.01", "user": "Authenticated"},
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
        lambda: {"installed": True, "authenticated": False, "version": "2026.09.01", "error": "Not authenticated."},
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
