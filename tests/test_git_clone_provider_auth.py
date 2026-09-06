from __future__ import annotations

import base64
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import git


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _decode_auth_header(cmd: list[str]) -> str:
    """Extracts and base64-decodes the `Authorization: Basic ...` header from a clone_repo cmd."""
    for arg in cmd:
        if arg.startswith("http.extraHeader=Authorization: Basic "):
            encoded = arg.split("Basic ", 1)[1]
            return base64.b64decode(encoded).decode()
    raise AssertionError(f"No Authorization header found in {cmd}")


def test_github_uses_x_access_token_username(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess(0)

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    monkeypatch.setattr(git, "Repo", lambda dest: dest)

    git.clone_repo("https://github.com/user/repo.git", str(tmp_path), token="tok123", provider="github")
    assert _decode_auth_header(captured["cmd"]) == "x-access-token:tok123"


def test_gitlab_uses_oauth2_username(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess(0)

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    monkeypatch.setattr(git, "Repo", lambda dest: dest)

    git.clone_repo("https://gitlab.com/user/repo.git", str(tmp_path), token="tok123", provider="gitlab")
    assert _decode_auth_header(captured["cmd"]) == "oauth2:tok123"


def test_bitbucket_uses_x_token_auth_username(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess(0)

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    monkeypatch.setattr(git, "Repo", lambda dest: dest)

    git.clone_repo("https://bitbucket.org/user/repo.git", str(tmp_path), token="tok123", provider="bitbucket")
    assert _decode_auth_header(captured["cmd"]) == "x-token-auth:tok123"


def test_custom_provider_falls_back_to_github_convention(monkeypatch, tmp_path):
    # "Custom Git" (self-hosted/other hosts) keeps today's pre-existing
    # behavior -- no new logic, just a named option in the UI.
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess(0)

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    monkeypatch.setattr(git, "Repo", lambda dest: dest)

    git.clone_repo("https://git.example.com/user/repo.git", str(tmp_path), token="tok123", provider="custom")
    assert _decode_auth_header(captured["cmd"]) == "x-access-token:tok123"


def test_unknown_provider_falls_back_to_github_convention(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess(0)

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    monkeypatch.setattr(git, "Repo", lambda dest: dest)

    git.clone_repo("https://example.com/user/repo.git", str(tmp_path), token="tok123", provider="")
    assert _decode_auth_header(captured["cmd"]) == "x-access-token:tok123"


def test_no_token_omits_auth_header_entirely(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess(0)

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    monkeypatch.setattr(git, "Repo", lambda dest: dest)

    git.clone_repo("https://github.com/user/public-repo.git", str(tmp_path), token=None, provider="gitlab")
    assert not any("extraHeader" in arg for arg in captured["cmd"])


def test_provider_is_case_and_whitespace_insensitive(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeCompletedProcess(0)

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    monkeypatch.setattr(git, "Repo", lambda dest: dest)

    git.clone_repo("https://gitlab.com/user/repo.git", str(tmp_path), token="tok123", provider="  GitLab  ")
    assert _decode_auth_header(captured["cmd"]) == "oauth2:tok123"


def test_clone_failure_raises_with_git_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(
        git.subprocess,
        "run",
        lambda *a, **k: FakeCompletedProcess(128, "", "fatal: repository not found"),
    )

    try:
        git.clone_repo("https://gitlab.com/user/missing.git", str(tmp_path), token="tok123", provider="gitlab")
        raise AssertionError("Expected clone_repo to raise on non-zero exit")
    except Exception as exc:
        assert "repository not found" in str(exc)
