from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import projects
from plugins._source_control.api import source_control as sc_api


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _handler(monkeypatch, *, project_name="demo-project"):
    handler = sc_api.SourceControl.__new__(sc_api.SourceControl)
    monkeypatch.setattr(handler, "use_context", lambda ctxid: object())
    monkeypatch.setattr(projects, "get_context_project_name", lambda context: project_name)
    monkeypatch.setattr(projects, "get_project_folder", lambda name: f"/fake/usr/projects/{name}")
    return handler


def test_status_requires_context_id(monkeypatch):
    handler = _handler(monkeypatch)
    result = _run(handler.process({"action": "status"}, request=None))
    assert result["ok"] is False
    assert "context_id" in result["error"]


def test_status_requires_active_project(monkeypatch):
    handler = _handler(monkeypatch, project_name=None)
    result = _run(handler.process({"action": "status", "context_id": "ctx1"}, request=None))
    assert result["ok"] is False
    assert "No project is active" in result["error"]


def test_status_reports_non_git_repo(monkeypatch):
    handler = _handler(monkeypatch)
    monkeypatch.setattr(
        sc_api.git, "get_repo_status", lambda path: {"is_git_repo": False, "error": "not a repo"}
    )
    result = _run(handler.process({"action": "status", "context_id": "ctx1"}, request=None))
    assert result == {"ok": True, "is_git_repo": False, "error": "not a repo"}


def test_status_reports_changes_for_git_repo(monkeypatch):
    handler = _handler(monkeypatch)
    monkeypatch.setattr(
        sc_api.git,
        "get_repo_status",
        lambda path: {"is_git_repo": True, "current_branch": "main", "remote_url": "", "last_commit": None},
    )
    monkeypatch.setattr(
        sc_api.git,
        "list_changed_files",
        lambda path: {"staged": [], "unstaged": [{"path": "a.py", "status": "modified"}], "untracked": []},
    )
    result = _run(handler.process({"action": "status", "context_id": "ctx1"}, request=None))
    assert result["ok"] is True
    assert result["is_git_repo"] is True
    assert result["current_branch"] == "main"
    assert result["unstaged"] == [{"path": "a.py", "status": "modified"}]


def test_diff_requires_path(monkeypatch):
    handler = _handler(monkeypatch)
    result = _run(handler.process({"action": "diff", "context_id": "ctx1"}, request=None))
    assert result["ok"] is False
    assert "path is required" in result["error"]


def test_diff_untracked_uses_preview(monkeypatch):
    handler = _handler(monkeypatch)
    captured = {}

    def fake_preview(path, file_path):
        captured["preview"] = (path, file_path)
        return "whole file"

    monkeypatch.setattr(sc_api.git, "get_untracked_file_preview", fake_preview)
    result = _run(
        handler.process(
            {"action": "diff", "context_id": "ctx1", "path": "new.txt", "untracked": True}, request=None
        )
    )
    assert result == {"ok": True, "path": "new.txt", "diff": "whole file"}
    assert captured["preview"] == ("/fake/usr/projects/demo-project", "new.txt")


def test_diff_tracked_uses_get_file_diff(monkeypatch):
    handler = _handler(monkeypatch)
    captured = {}

    def fake_diff(path, file_path, *, staged):
        captured["args"] = (path, file_path, staged)
        return "diff text"

    monkeypatch.setattr(sc_api.git, "get_file_diff", fake_diff)
    result = _run(
        handler.process(
            {"action": "diff", "context_id": "ctx1", "path": "a.py", "staged": True}, request=None
        )
    )
    assert result == {"ok": True, "path": "a.py", "diff": "diff text"}
    assert captured["args"] == ("/fake/usr/projects/demo-project", "a.py", True)


def test_stage_passes_single_path(monkeypatch):
    handler = _handler(monkeypatch)
    captured = {}
    monkeypatch.setattr(sc_api.git, "stage_files", lambda path, paths: captured.setdefault("call", (path, paths)))
    result = _run(handler.process({"action": "stage", "context_id": "ctx1", "path": "a.py"}, request=None))
    assert result == {"ok": True}
    assert captured["call"] == ("/fake/usr/projects/demo-project", ["a.py"])


def test_stage_passes_multiple_paths(monkeypatch):
    handler = _handler(monkeypatch)
    captured = {}
    monkeypatch.setattr(sc_api.git, "stage_files", lambda path, paths: captured.setdefault("call", (path, paths)))
    result = _run(
        handler.process({"action": "stage", "context_id": "ctx1", "paths": ["a.py", "b.py"]}, request=None)
    )
    assert result == {"ok": True}
    assert captured["call"] == ("/fake/usr/projects/demo-project", ["a.py", "b.py"])


def test_unstage_passes_paths(monkeypatch):
    handler = _handler(monkeypatch)
    captured = {}
    monkeypatch.setattr(sc_api.git, "unstage_files", lambda path, paths: captured.setdefault("call", (path, paths)))
    result = _run(handler.process({"action": "unstage", "context_id": "ctx1", "path": "a.py"}, request=None))
    assert result == {"ok": True}
    assert captured["call"] == ("/fake/usr/projects/demo-project", ["a.py"])


def test_commit_returns_sha(monkeypatch):
    handler = _handler(monkeypatch)
    monkeypatch.setattr(sc_api.git, "commit_staged", lambda path, message: "abc123")
    result = _run(
        handler.process({"action": "commit", "context_id": "ctx1", "message": "fix bug"}, request=None)
    )
    assert result == {"ok": True, "commit": "abc123"}


def test_commit_error_surfaces_as_ok_false(monkeypatch):
    handler = _handler(monkeypatch)

    def raise_error(path, message):
        raise ValueError("Nothing staged to commit.")

    monkeypatch.setattr(sc_api.git, "commit_staged", raise_error)
    result = _run(handler.process({"action": "commit", "context_id": "ctx1", "message": "x"}, request=None))
    assert result == {"ok": False, "error": "Nothing staged to commit."}


def test_unsupported_action_returns_error(monkeypatch):
    handler = _handler(monkeypatch)
    result = _run(handler.process({"action": "push", "context_id": "ctx1"}, request=None))
    assert result["ok"] is False
    assert "Unsupported source control action" in result["error"]
