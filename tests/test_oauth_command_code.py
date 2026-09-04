from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins._oauth.helpers import command_code_cli
from plugins._oauth.helpers.providers.base import COMMAND_CODE_PROVIDER_ID
from plugins._oauth.helpers.providers.command_code import (
    CURATED_MODELS,
    NOT_DRIVEN_MESSAGE,
    CommandCodeOAuthProvider,
)


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_is_installed_true_when_version_exits_zero(monkeypatch):
    monkeypatch.setattr(
        command_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(0, "1.47.0\n"),
    )
    assert command_code_cli.is_installed() is True


def test_is_installed_false_when_binary_missing(monkeypatch):
    def raise_not_found(*a, **k):
        raise OSError("not found")

    monkeypatch.setattr(command_code_cli.subprocess, "run", raise_not_found)
    assert command_code_cli.is_installed() is False


def test_get_status_reports_not_installed_without_shelling_to_status(monkeypatch):
    monkeypatch.setattr(command_code_cli, "is_installed", lambda: False)
    status = command_code_cli.get_status()
    assert status["installed"] is False
    assert status["authenticated"] is False
    assert "npm i -g command-code" in status["error"]


def test_get_status_reads_authenticated_body_even_with_nonzero_exit(monkeypatch):
    # Confirmed against the real CLI (v1.47.0): unauthenticated status exits 1
    # but still prints a valid JSON body -- authenticated:false in the body
    # is authoritative, not the exit code. This test exercises the mirror
    # case: a truthy authenticated body must win regardless of exit code.
    monkeypatch.setattr(command_code_cli, "is_installed", lambda: True)
    monkeypatch.setattr(
        command_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(
            0, '{"authenticated":true,"version":"1.47.0","user":"me@example.com"}\n'
        ),
    )
    status = command_code_cli.get_status()
    assert status["authenticated"] is True
    assert status["version"] == "1.47.0"
    assert status["user"] == "me@example.com"


def test_get_status_unauthenticated_matches_real_cli_shape(monkeypatch):
    # Verbatim output captured from the real CLI (v1.47.0) when signed out.
    monkeypatch.setattr(command_code_cli, "is_installed", lambda: True)
    monkeypatch.setattr(
        command_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(1, '{"authenticated":false,"version":"1.47.0"}\n'),
    )
    status = command_code_cli.get_status()
    assert status["installed"] is True
    assert status["authenticated"] is False
    assert status["version"] == "1.47.0"


def test_get_status_times_out_gracefully(monkeypatch):
    monkeypatch.setattr(command_code_cli, "is_installed", lambda: True)

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="command-code", timeout=8)

    monkeypatch.setattr(command_code_cli.subprocess, "run", raise_timeout)
    status = command_code_cli.get_status()
    assert status["authenticated"] is False
    assert "timed out" in status["error"]


def test_list_models_parses_vendor_slash_model_rows(monkeypatch):
    # Excerpt shaped like the real `--list-models` table output (v1.47.0):
    # a summary line, a section header, then "vendor/model  description" rows.
    table = (
        "Available models  \xb7  67 models\n"
        "\n"
        "Open Source\n"
        "\n"
        "deepseek/deepseek-v4-pro               hybrid-attention long-context reasoning\n"
        "moonshotai/kimi-k3                     long-horizon coding & knowledge work\n"
    )
    monkeypatch.setattr(
        command_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(0, table),
    )
    models = command_code_cli.list_models()
    assert models == ["deepseek/deepseek-v4-pro", "moonshotai/kimi-k3"]


def test_list_models_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr(
        command_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(1, ""),
    )
    assert command_code_cli.list_models() == []


def test_run_prompt_flattens_messages_and_passes_them_as_single_arg(monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompletedProcess(
            0,
            '{"type":"result","subtype":"success","usage":{"inputTokens":10,"outputTokens":5},'
            '"finalText":"Hello there"}\n',
        )

    monkeypatch.setattr(command_code_cli.subprocess, "run", fake_run)

    result = command_code_cli.run_prompt(
        [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Say hi"},
        ],
        model="anthropic/claude-sonnet-5",
    )

    assert result["ok"] is True
    assert result["text"] == "Hello there"
    assert result["usage"] == {"inputTokens": 10, "outputTokens": 5}

    args = captured["args"]
    assert args[0] == "command-code"
    assert args[1] == "-p"
    assert "[System]\nBe terse." in args[2]
    assert "[User]\nSay hi" in args[2]
    assert "--output-format" in args and "json" in args
    assert "-m" in args and "anthropic/claude-sonnet-5" in args


def test_run_prompt_matches_real_cli_error_shape(monkeypatch):
    # Verbatim output captured from the real CLI (v1.47.0) when signed out:
    # `command-code -p hello --output-format json` exits 3.
    monkeypatch.setattr(
        command_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(
            3,
            '{"type":"result","subtype":"error","usage":{"inputTokens":0,"outputTokens":0,'
            '"cacheReadTokens":0,"cacheWriteTokens":0},"durationMs":1,"finalText":"",'
            '"error":"Error: Not authenticated. Please run \\"cmd login\\" first."}\n',
            'Error: Not authenticated. Please run "cmd login" first.\n',
        ),
    )
    result = command_code_cli.run_prompt([{"role": "user", "content": "hi"}])
    assert result["ok"] is False
    assert "Not authenticated" in result["error"]


def test_run_prompt_rejects_empty_message_content():
    result = command_code_cli.run_prompt([{"role": "user", "content": "   "}])
    assert result["ok"] is False
    assert "No prompt content" in result["error"]


def test_run_prompt_reports_no_result_frame(monkeypatch):
    monkeypatch.setattr(
        command_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(1, "", "unexpected crash\n"),
    )
    result = command_code_cli.run_prompt([{"role": "user", "content": "hi"}])
    assert result["ok"] is False
    assert "unexpected crash" in result["error"]


def test_provider_metadata_and_protocol_stubs():
    provider = CommandCodeOAuthProvider()
    assert provider.provider_id == COMMAND_CODE_PROVIDER_ID

    metadata = provider.metadata()
    assert metadata.display_name == "Command Code"
    assert metadata.auth_flow == "external_cli"
    assert metadata.default_models == CURATED_MODELS

    # This provider drives no OAuth flow -- every login-flow method (other
    # than start_login, which has its own dedicated tests below covering the
    # auto-install branch) must fail closed with an explanatory message, not
    # silently no-op.
    poll = provider.poll_login()
    assert poll.ok is False

    callback = provider.complete_callback({})
    assert callback.ok is False

    manual = provider.manual_callback({})
    assert manual.ok is False

    assert provider.api_key() == "oauth"


def test_start_login_when_already_installed_just_explains_it_cant_drive_login(monkeypatch):
    monkeypatch.setattr(command_code_cli, "is_installed", lambda: True)

    def fail_if_called():
        raise AssertionError("install_latest() must not run when already installed")

    monkeypatch.setattr(command_code_cli, "install_latest", fail_if_called)

    provider = CommandCodeOAuthProvider()
    result = provider.start_login()
    assert result.ok is False
    assert result.error == NOT_DRIVEN_MESSAGE


def test_start_login_auto_installs_cli_when_missing(monkeypatch):
    # Installing the CLI is the one thing safe to automate here -- it is
    # pure software installation, no credentials involved. Actual sign-in
    # must still never be attempted.
    monkeypatch.setattr(command_code_cli, "is_installed", lambda: False)
    monkeypatch.setattr(command_code_cli, "install_latest", lambda: {"ok": True, "error": ""})

    provider = CommandCodeOAuthProvider()
    result = provider.start_login()
    assert result.ok is False
    assert "Installed the Command Code CLI" in result.error
    assert "command-code login" in result.error


def test_start_login_reports_install_failure_without_ok(monkeypatch):
    monkeypatch.setattr(command_code_cli, "is_installed", lambda: False)
    monkeypatch.setattr(
        command_code_cli,
        "install_latest",
        lambda: {"ok": False, "error": "npm is not available on PATH. Install Node.js first, then retry."},
    )

    provider = CommandCodeOAuthProvider()
    result = provider.start_login()
    assert result.ok is False
    assert "Automatic install failed" in result.error
    assert "npm is not available" in result.error


def test_install_latest_skips_npm_call_when_npm_missing(monkeypatch):
    monkeypatch.setattr(command_code_cli, "npm_available", lambda: False)

    def fail_if_called(*a, **k):
        raise AssertionError("subprocess.run must not be called when npm is unavailable")

    monkeypatch.setattr(command_code_cli.subprocess, "run", fail_if_called)

    result = command_code_cli.install_latest()
    assert result["ok"] is False
    assert "Install Node.js first" in result["error"]


def test_install_latest_runs_npm_install_dash_g_latest(monkeypatch):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompletedProcess(0, "added 1 package\n")

    monkeypatch.setattr(command_code_cli, "npm_available", lambda: True)
    monkeypatch.setattr(command_code_cli.subprocess, "run", fake_run)

    result = command_code_cli.install_latest()
    assert result["ok"] is True
    assert captured["args"] == ["npm", "install", "-g", "command-code@latest"]


def test_install_latest_surfaces_npm_failure(monkeypatch):
    monkeypatch.setattr(command_code_cli, "npm_available", lambda: True)
    monkeypatch.setattr(
        command_code_cli.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(1, "", "EACCES: permission denied\n"),
    )

    result = command_code_cli.install_latest()
    assert result["ok"] is False
    assert "EACCES" in result["error"]


def test_provider_disconnect_never_shells_out_to_logout():
    # disconnect() must not perform a real, system-wide `command-code
    # logout` -- Agent Zero never held this credential.
    provider = CommandCodeOAuthProvider()
    result = provider.disconnect()
    assert result["disconnected"] is False
    assert "command-code logout" in result["note"]


def test_provider_status_reports_installed_and_authenticated(monkeypatch):
    monkeypatch.setattr(
        command_code_cli,
        "get_status",
        lambda: {"installed": True, "authenticated": True, "version": "1.47.0", "user": "me@example.com"},
    )
    provider = CommandCodeOAuthProvider()
    status = provider.status()
    assert status["connected"] is True
    assert status["account_label"] == "me@example.com"
    assert status["installed"] is True
    assert "error" not in status


def test_provider_status_surfaces_error_when_not_connected(monkeypatch):
    monkeypatch.setattr(
        command_code_cli,
        "get_status",
        lambda: {"installed": True, "authenticated": False, "version": "1.47.0", "error": "Not authenticated."},
    )
    provider = CommandCodeOAuthProvider()
    status = provider.status()
    assert status["connected"] is False
    assert status["error"] == "Not authenticated."


def test_provider_models_falls_back_to_curated_list(monkeypatch):
    monkeypatch.setattr(command_code_cli, "list_models", lambda: [])
    provider = CommandCodeOAuthProvider()
    assert provider.models() == CURATED_MODELS


def test_provider_registers_routes_without_duplicate(monkeypatch):
    # Faking plugins._oauth.helpers.routes avoids pulling in its real import
    # chain (routes -> codex -> helpers.files -> simpleeval, ...), which
    # needs Agent Zero's full runtime deps installed -- register_routes()
    # only needs the three named callables to exist on that module.
    import types

    fake_routes = types.ModuleType("plugins._oauth.helpers.routes")
    fake_routes.command_code_health = lambda: None
    fake_routes.command_code_models = lambda: None
    fake_routes.command_code_chat_completions = lambda: None
    monkeypatch.setitem(sys.modules, "plugins._oauth.helpers.routes", fake_routes)

    class FakeApp:
        def __init__(self):
            self.view_functions: dict = {}
            self.rules: list = []

        def add_url_rule(self, rule, endpoint, view_func, methods=None):
            self.view_functions[endpoint] = view_func
            self.rules.append((rule, endpoint, tuple(methods or [])))

    app = FakeApp()
    provider = CommandCodeOAuthProvider()
    provider.register_routes(app)
    provider.register_routes(app)  # idempotent: must not register twice

    assert app.rules.count(
        ("/oauth/command-code/v1/chat/completions", "oauth_command_code_chat_completions", ("POST", "OPTIONS"))
    ) == 1
    assert "oauth_command_code_health" in app.view_functions
    assert "oauth_command_code_models" in app.view_functions
