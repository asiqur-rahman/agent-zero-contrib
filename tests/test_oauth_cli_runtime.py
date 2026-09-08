from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins._oauth.helpers import cli_runtime


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_resolve_binary_prefers_persisted_npm_prefix(monkeypatch, tmp_path):
    # The whole point of the persisted prefix: after a container recreate
    # this copy is the only one left, and it must be the one that is found.
    prefix = tmp_path / "npm"
    binary = _make_executable(prefix / "bin" / "command-code")
    monkeypatch.setattr(cli_runtime, "npm_prefix", lambda: prefix)
    monkeypatch.setattr(cli_runtime, "default_homes", lambda: [])
    monkeypatch.setattr(cli_runtime.shutil, "which", lambda name: "/usr/local/bin/command-code")

    assert cli_runtime.resolve_binary("command-code") == str(binary)


def test_resolve_binary_finds_installer_target_under_persisted_home(monkeypatch, tmp_path):
    # Cursor CLI's only supported installer writes into $HOME/.local/bin,
    # which in a docker exec shell is the relocated persisted HOME -- a
    # directory the run_ui service's own PATH knows nothing about.
    home = tmp_path / "home"
    binary = _make_executable(home / ".local" / "bin" / "agent")
    monkeypatch.setattr(cli_runtime, "npm_prefix", lambda: None)
    monkeypatch.setattr(cli_runtime, "default_homes", lambda: [])
    monkeypatch.setattr(cli_runtime.shutil, "which", lambda name: None)

    assert cli_runtime.resolve_binary("agent", home=home) == str(binary)


def test_resolve_binary_falls_back_to_path_then_bare_name(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_runtime, "npm_prefix", lambda: tmp_path / "empty")
    monkeypatch.setattr(cli_runtime, "default_homes", lambda: [])

    monkeypatch.setattr(cli_runtime.shutil, "which", lambda name: "/usr/bin/claude")
    assert cli_runtime.resolve_binary("claude") == "/usr/bin/claude"

    monkeypatch.setattr(cli_runtime.shutil, "which", lambda name: None)
    assert cli_runtime.resolve_binary("claude") == "claude"


def test_env_with_bin_path_prepends_persisted_dirs(monkeypatch, tmp_path):
    prefix = tmp_path / "npm"
    (prefix / "bin").mkdir(parents=True)
    monkeypatch.setattr(cli_runtime, "npm_prefix", lambda: prefix)
    monkeypatch.setattr(cli_runtime, "default_homes", lambda: [])

    env = cli_runtime.env_with_bin_path({"PATH": "/usr/bin"})
    entries = env["PATH"].split(os.pathsep)
    assert entries[0] == str(prefix / "bin")
    assert entries[-1] == "/usr/bin"


def test_npm_install_global_uses_persisted_prefix(monkeypatch, tmp_path):
    captured: dict = {}

    class Result:
        returncode = 0
        stdout = "added 1 package"
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return Result()

    monkeypatch.setattr(cli_runtime, "npm_available", lambda: True)
    monkeypatch.setattr(cli_runtime, "npm_prefix", lambda: tmp_path / "npm")
    monkeypatch.setattr(cli_runtime.subprocess, "run", fake_run)

    assert cli_runtime.npm_install_global("command-code")["ok"] is True
    assert captured["args"] == [
        "npm",
        "install",
        "-g",
        "--prefix",
        str(tmp_path / "npm"),
        "command-code@latest",
    ]


def test_adopt_file_copies_only_when_target_is_missing_or_empty(tmp_path):
    stray = tmp_path / "stray.json"
    stray.write_text('{"token":"stray"}')
    target = tmp_path / "persisted" / "creds.json"

    assert cli_runtime.adopt_file(target, [stray]) == stray
    assert target.read_text() == '{"token":"stray"}'

    # A second pass must leave the persisted copy alone -- it is the one the
    # CLI refreshes its token into, so re-adopting would roll it backwards.
    target.write_text('{"token":"refreshed"}')
    assert cli_runtime.adopt_file(target, [stray]) is None
    assert target.read_text() == '{"token":"refreshed"}'


def test_adopt_file_ignores_empty_and_missing_candidates(tmp_path):
    empty = tmp_path / "empty.json"
    empty.touch()
    missing = tmp_path / "missing.json"
    target = tmp_path / "creds.json"

    assert cli_runtime.adopt_file(target, [missing, empty]) is None
    assert not target.exists()


def test_adopt_tree_copies_whole_session_directory(tmp_path):
    stray = tmp_path / "stray" / ".commandcode"
    (stray / "nested").mkdir(parents=True)
    (stray / "session.json").write_text('{"token":"stray"}')
    (stray / "nested" / "extra.json").write_text("{}")
    target = tmp_path / "persisted" / ".commandcode"

    assert cli_runtime.adopt_tree(target, [stray]) == stray
    assert (target / "session.json").read_text() == '{"token":"stray"}'
    assert (target / "nested" / "extra.json").is_file()

    assert cli_runtime.adopt_tree(target, [stray]) is None


def test_purge_files_reports_only_what_it_removed(tmp_path):
    present = tmp_path / "creds.json"
    present.write_text("{}")
    missing = tmp_path / "missing.json"

    assert cli_runtime.purge_files([present, missing, present]) == [present]
    assert not present.exists()


def test_default_homes_includes_process_home_and_root(monkeypatch):
    monkeypatch.setenv("HOME", "/relocated")
    homes = [str(home) for home in cli_runtime.default_homes()]
    assert homes[0] == str(Path("/relocated"))
    assert str(Path("/root")) in homes
