from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins._oauth.helpers import claude_code_cli
from plugins._oauth.helpers.providers.base import CLAUDE_CODE_PROVIDER_ID
from plugins._oauth.helpers.providers.claude_code import (
    CURATED_MODELS,
    NOT_DRIVEN_MESSAGE,
    ClaudeCodeOAuthProvider,
)


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_is_installed_true_when_version_exits_zero(monkeypatch):
    monkeypatch.setattr(
        claude_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(0, "2.1.0 (Claude Code)\n"),
    )
    assert claude_code_cli.is_installed() is True


def test_is_installed_false_when_binary_missing(monkeypatch):
    def raise_not_found(*a, **k):
        raise OSError("not found")

    monkeypatch.setattr(claude_code_cli.subprocess, "run", raise_not_found)
    assert claude_code_cli.is_installed() is False


def test_get_status_reports_not_installed_without_checking_credentials(monkeypatch):
    monkeypatch.setattr(claude_code_cli, "is_installed", lambda: False)
    status = claude_code_cli.get_status()
    assert status["installed"] is False
    assert status["authenticated"] is False
    assert "npm i -g @anthropic-ai/claude-code" in status["error"]


def test_get_status_authenticated_via_env_api_key(monkeypatch):
    monkeypatch.setattr(claude_code_cli, "is_installed", lambda: True)
    monkeypatch.setattr(claude_code_cli, "_version", lambda: "2.1.0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    status = claude_code_cli.get_status()
    assert status["authenticated"] is True
    assert "ANTHROPIC_API_KEY" in status["user"]


def test_get_status_authenticated_via_credentials_file(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_code_cli, "is_installed", lambda: True)
    monkeypatch.setattr(claude_code_cli, "_version", lambda: "2.1.0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    creds = tmp_path / ".credentials.json"
    creds.write_text('{"token":"fake"}')
    monkeypatch.setattr(claude_code_cli, "credentials_path", lambda: creds)

    status = claude_code_cli.get_status()
    assert status["authenticated"] is True
    assert status["version"] == "2.1.0"


def test_get_status_not_authenticated_when_no_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_code_cli, "is_installed", lambda: True)
    monkeypatch.setattr(claude_code_cli, "_version", lambda: "2.1.0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude_code_cli, "credentials_path", lambda: tmp_path / ".credentials.json")

    status = claude_code_cli.get_status()
    assert status["authenticated"] is False
    assert "claude auth login" in status["error"]


def test_list_models_is_always_empty_no_live_catalog():
    # Claude Code has no documented `--list-models` surface to introspect --
    # unlike Command Code's `--list-models` table, this always defers to the
    # provider's curated fallback list.
    assert claude_code_cli.list_models() == []


def test_run_prompt_flattens_messages_and_passes_them_as_single_arg(monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompletedProcess(
            0,
            '{"type":"result","subtype":"success","is_error":false,'
            '"result":"Hello there","usage":{"input_tokens":10,"output_tokens":5}}\n',
        )

    monkeypatch.setattr(claude_code_cli.subprocess, "run", fake_run)

    result = claude_code_cli.run_prompt(
        [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Say hi"},
        ],
        model="claude-sonnet-5",
    )

    assert result["ok"] is True
    assert result["text"] == "Hello there"
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 5}

    args = captured["args"]
    assert args[0] == "claude"
    assert args[1] == "-p"
    assert "[System]\nBe terse." in args[2]
    assert "[User]\nSay hi" in args[2]
    assert "--output-format" in args and "json" in args
    assert "--model" in args and "claude-sonnet-5" in args


def test_run_prompt_never_grants_tool_permissions(monkeypatch):
    # This provider is a text-completion backend, not an agentic coding
    # delegate (that role belongs to plugins/_orchestrator instead) -- it
    # must never pass --allowedTools/--permission-mode/--dangerously-*.
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompletedProcess(
            0, '{"type":"result","is_error":false,"result":"hi","usage":{}}\n'
        )

    monkeypatch.setattr(claude_code_cli.subprocess, "run", fake_run)
    claude_code_cli.run_prompt([{"role": "user", "content": "hi"}])

    args = captured["args"]
    assert not any("permission" in str(a).lower() for a in args)
    assert not any("allowedtools" in str(a).lower() for a in args)


def test_run_prompt_reports_error_result(monkeypatch):
    monkeypatch.setattr(
        claude_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(
            1,
            '{"type":"result","subtype":"error_during_execution","is_error":true,'
            '"result":"Not authenticated. Run claude auth login.","usage":{}}\n',
        ),
    )
    result = claude_code_cli.run_prompt([{"role": "user", "content": "hi"}])
    assert result["ok"] is False
    assert "Not authenticated" in result["error"]


def test_run_prompt_rejects_empty_message_content():
    result = claude_code_cli.run_prompt([{"role": "user", "content": "   "}])
    assert result["ok"] is False
    assert "No prompt content" in result["error"]


def test_run_prompt_reports_no_result_object(monkeypatch):
    monkeypatch.setattr(
        claude_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(1, "", "unexpected crash\n"),
    )
    result = claude_code_cli.run_prompt([{"role": "user", "content": "hi"}])
    assert result["ok"] is False
    assert "unexpected crash" in result["error"]


def test_run_prompt_times_out_gracefully(monkeypatch):
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=300)

    monkeypatch.setattr(claude_code_cli.subprocess, "run", raise_timeout)
    result = claude_code_cli.run_prompt([{"role": "user", "content": "hi"}])
    assert result["ok"] is False
    assert "timed out" in result["error"]


def test_provider_metadata_and_protocol_stubs():
    provider = ClaudeCodeOAuthProvider()
    assert provider.provider_id == CLAUDE_CODE_PROVIDER_ID

    metadata = provider.metadata()
    assert metadata.display_name == "Claude Code"
    assert metadata.auth_flow == "external_cli"
    assert metadata.default_models == CURATED_MODELS

    poll = provider.poll_login()
    assert poll.ok is False

    callback = provider.complete_callback({})
    assert callback.ok is False

    manual = provider.manual_callback({})
    assert manual.ok is False

    assert provider.api_key() == "oauth"


def test_start_login_when_already_installed_just_explains_it_cant_drive_login(monkeypatch):
    monkeypatch.setattr(claude_code_cli, "is_installed", lambda: True)

    def fail_if_called():
        raise AssertionError("install_latest() must not run when already installed")

    monkeypatch.setattr(claude_code_cli, "install_latest", fail_if_called)

    provider = ClaudeCodeOAuthProvider()
    result = provider.start_login()
    assert result.ok is False
    assert result.error == NOT_DRIVEN_MESSAGE


def test_start_login_auto_installs_cli_when_missing(monkeypatch):
    monkeypatch.setattr(claude_code_cli, "is_installed", lambda: False)
    monkeypatch.setattr(claude_code_cli, "install_latest", lambda: {"ok": True, "error": ""})

    provider = ClaudeCodeOAuthProvider()
    result = provider.start_login()
    assert result.ok is False
    assert "Installed the Claude Code CLI" in result.error
    assert "claude auth login" in result.error


def test_start_login_reports_install_failure_without_ok(monkeypatch):
    monkeypatch.setattr(claude_code_cli, "is_installed", lambda: False)
    monkeypatch.setattr(
        claude_code_cli,
        "install_latest",
        lambda: {"ok": False, "error": "npm is not available on PATH. Install Node.js first, then retry."},
    )

    provider = ClaudeCodeOAuthProvider()
    result = provider.start_login()
    assert result.ok is False
    assert "Automatic install failed" in result.error
    assert "npm is not available" in result.error


def test_install_latest_skips_npm_call_when_npm_missing(monkeypatch):
    monkeypatch.setattr(claude_code_cli, "npm_available", lambda: False)

    def fail_if_called(*a, **k):
        raise AssertionError("subprocess.run must not be called when npm is unavailable")

    monkeypatch.setattr(claude_code_cli.subprocess, "run", fail_if_called)

    result = claude_code_cli.install_latest()
    assert result["ok"] is False
    assert "Install Node.js first" in result["error"]


def test_install_latest_runs_npm_install_dash_g_latest(monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompletedProcess(0, "added 1 package\n")

    monkeypatch.setattr(claude_code_cli, "npm_available", lambda: True)
    monkeypatch.setattr(claude_code_cli.subprocess, "run", fake_run)

    result = claude_code_cli.install_latest()
    assert result["ok"] is True
    assert captured["args"] == ["npm", "install", "-g", "@anthropic-ai/claude-code"]


def test_provider_disconnect_removes_owned_credentials_file(monkeypatch, tmp_path):
    # Unlike Command Code's ~/.commandcode (never owned by this plugin), the
    # Claude Code credentials file lives under this plugin's own
    # provider_data_dir() -- so disconnect() may safely delete it.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    creds = tmp_path / ".credentials.json"
    creds.write_text('{"token":"fake"}')
    monkeypatch.setattr(claude_code_cli, "credentials_path", lambda: creds)

    provider = ClaudeCodeOAuthProvider()
    result = provider.disconnect()
    assert result["disconnected"] is True
    assert not creds.exists()


def test_provider_disconnect_refuses_when_env_api_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    provider = ClaudeCodeOAuthProvider()
    result = provider.disconnect()
    assert result["disconnected"] is False
    assert "ANTHROPIC_API_KEY" in result["note"]


def test_provider_status_reports_installed_and_authenticated(monkeypatch):
    monkeypatch.setattr(
        claude_code_cli,
        "get_status",
        lambda: {"installed": True, "authenticated": True, "version": "2.1.0", "user": "Authenticated"},
    )
    provider = ClaudeCodeOAuthProvider()
    status = provider.status()
    assert status["connected"] is True
    assert status["installed"] is True
    assert "error" not in status


def test_provider_status_surfaces_error_when_not_connected(monkeypatch):
    monkeypatch.setattr(
        claude_code_cli,
        "get_status",
        lambda: {"installed": True, "authenticated": False, "version": "2.1.0", "error": "Not authenticated."},
    )
    provider = ClaudeCodeOAuthProvider()
    status = provider.status()
    assert status["connected"] is False
    assert status["error"] == "Not authenticated."


def test_provider_models_falls_back_to_curated_list(monkeypatch):
    monkeypatch.setattr(claude_code_cli, "list_models", lambda: [])
    provider = ClaudeCodeOAuthProvider()
    assert provider.models() == CURATED_MODELS


def test_provider_registers_routes_without_duplicate(monkeypatch):
    import types

    fake_routes = types.ModuleType("plugins._oauth.helpers.routes")
    fake_routes.claude_code_health = lambda: None
    fake_routes.claude_code_models = lambda: None
    fake_routes.claude_code_chat_completions = lambda: None
    monkeypatch.setitem(sys.modules, "plugins._oauth.helpers.routes", fake_routes)

    class FakeApp:
        def __init__(self):
            self.view_functions: dict = {}
            self.rules: list = []

        def add_url_rule(self, rule, endpoint, view_func, methods=None):
            self.view_functions[endpoint] = view_func
            self.rules.append((rule, endpoint, tuple(methods or [])))

    app = FakeApp()
    provider = ClaudeCodeOAuthProvider()
    provider.register_routes(app)
    provider.register_routes(app)  # idempotent: must not register twice

    assert app.rules.count(
        ("/oauth/claude-code/v1/chat/completions", "oauth_claude_code_chat_completions", ("POST", "OPTIONS"))
    ) == 1
    assert "oauth_claude_code_health" in app.view_functions
    assert "oauth_claude_code_models" in app.view_functions
