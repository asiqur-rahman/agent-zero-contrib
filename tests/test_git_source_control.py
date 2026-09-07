from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from git import Repo

from helpers import git as git_helper


def _init_repo_with_commit(path: Path) -> Repo:
    repo = Repo.init(str(path))
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    (path / "tracked.txt").write_text("original content\n")
    repo.index.add(["tracked.txt"])
    repo.index.commit("initial commit")
    return repo


def test_list_changed_files_reports_staged_unstaged_and_untracked(tmp_path):
    repo = _init_repo_with_commit(tmp_path)

    # Unstaged modification to a tracked file.
    (tmp_path / "tracked.txt").write_text("modified content\n")

    # Staged new file.
    (tmp_path / "staged_new.txt").write_text("new file\n")
    repo.index.add(["staged_new.txt"])

    # Untracked file.
    (tmp_path / "untracked.txt").write_text("untracked\n")

    result = git_helper.list_changed_files(str(tmp_path))

    assert result["staged"] == [{"path": "staged_new.txt", "status": "added"}]
    assert result["unstaged"] == [{"path": "tracked.txt", "status": "modified"}]
    assert result["untracked"] == ["untracked.txt"]


def test_list_changed_files_excludes_a0proj_metadata(tmp_path):
    repo = _init_repo_with_commit(tmp_path)
    (tmp_path / ".a0proj").mkdir()
    (tmp_path / ".a0proj" / "project.json").write_text("{}")
    (tmp_path / "real_change.txt").write_text("real\n")

    result = git_helper.list_changed_files(str(tmp_path))

    assert result["untracked"] == ["real_change.txt"]
    assert not any(".a0proj" in path for path in result["untracked"])


def test_list_changed_files_before_first_commit(tmp_path):
    repo = Repo.init(str(tmp_path))
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    (tmp_path / "new_file.txt").write_text("brand new\n")
    repo.index.add(["new_file.txt"])

    result = git_helper.list_changed_files(str(tmp_path))

    assert result["staged"] == [{"path": "new_file.txt", "status": "added"}]


def test_get_file_diff_unstaged_shows_modification(tmp_path):
    _init_repo_with_commit(tmp_path)
    (tmp_path / "tracked.txt").write_text("modified content\n")

    diff = git_helper.get_file_diff(str(tmp_path), "tracked.txt", staged=False)

    assert "-original content" in diff
    assert "+modified content" in diff


def test_get_file_diff_staged_shows_addition(tmp_path):
    repo = _init_repo_with_commit(tmp_path)
    (tmp_path / "new.txt").write_text("hello\n")
    repo.index.add(["new.txt"])

    diff = git_helper.get_file_diff(str(tmp_path), "new.txt", staged=True)

    assert "+hello" in diff


def test_get_untracked_file_preview_returns_whole_file(tmp_path):
    _init_repo_with_commit(tmp_path)
    (tmp_path / "scratch.txt").write_text("line one\nline two\n")

    preview = git_helper.get_untracked_file_preview(str(tmp_path), "scratch.txt")

    assert preview == "line one\nline two\n"


def test_get_untracked_file_preview_truncates_large_files(tmp_path):
    _init_repo_with_commit(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 1000)

    preview = git_helper.get_untracked_file_preview(str(tmp_path), "big.txt", max_bytes=100)

    assert len(preview) < 1000
    assert preview.endswith("... (truncated)")


def test_stage_files_moves_unstaged_change_to_staged(tmp_path):
    _init_repo_with_commit(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed\n")

    git_helper.stage_files(str(tmp_path), ["tracked.txt"])
    result = git_helper.list_changed_files(str(tmp_path))

    assert result["staged"] == [{"path": "tracked.txt", "status": "modified"}]
    assert result["unstaged"] == []


def test_unstage_files_moves_staged_change_back_to_unstaged(tmp_path):
    _init_repo_with_commit(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed\n")
    git_helper.stage_files(str(tmp_path), ["tracked.txt"])

    git_helper.unstage_files(str(tmp_path), ["tracked.txt"])
    result = git_helper.list_changed_files(str(tmp_path))

    assert result["staged"] == []
    assert result["unstaged"] == [{"path": "tracked.txt", "status": "modified"}]


def test_unstage_files_before_first_commit_removes_from_index(tmp_path):
    repo = Repo.init(str(tmp_path))
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    (tmp_path / "new_file.txt").write_text("brand new\n")
    repo.index.add(["new_file.txt"])

    git_helper.unstage_files(str(tmp_path), ["new_file.txt"])
    result = git_helper.list_changed_files(str(tmp_path))

    assert result["staged"] == []
    assert "new_file.txt" in result["untracked"]


def test_commit_staged_creates_commit_and_clears_staged_list(tmp_path):
    _init_repo_with_commit(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed\n")
    git_helper.stage_files(str(tmp_path), ["tracked.txt"])

    sha = git_helper.commit_staged(str(tmp_path), "Update tracked.txt")

    assert len(sha) == 40
    result = git_helper.list_changed_files(str(tmp_path))
    assert result["staged"] == []
    repo = Repo(str(tmp_path))
    assert repo.head.commit.hexsha == sha
    assert repo.head.commit.message == "Update tracked.txt"


def test_commit_staged_rejects_blank_message(tmp_path):
    _init_repo_with_commit(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed\n")
    git_helper.stage_files(str(tmp_path), ["tracked.txt"])

    with pytest.raises(ValueError, match="Commit message is required"):
        git_helper.commit_staged(str(tmp_path), "   ")


def test_commit_staged_rejects_when_nothing_staged(tmp_path):
    _init_repo_with_commit(tmp_path)

    with pytest.raises(ValueError, match="Nothing staged to commit"):
        git_helper.commit_staged(str(tmp_path), "empty commit attempt")
