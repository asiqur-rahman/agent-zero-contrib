from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

# Shared persistence plumbing for the three external-CLI providers
# (Command Code, Claude Code, Cursor CLI). Those providers do not hold a
# credential of their own -- they read whatever the locally installed CLI
# binary already has -- so "connected" survives a restart only if BOTH of
# these survive it:
#
#   1. the CLI binary itself, and
#   2. the CLI's own config/credential directory.
#
# Only `usr/` is bind-mounted out of this container (see
# casaos-agent-zero.yml / docker-compose.local.yml), so everything else --
# including `npm install -g`'s default /usr/local prefix and root's `$HOME`
# -- is lost the moment the container is recreated (an image update, a
# CasaOS app update, `docker compose up --force-recreate`). That is why
# every provider flipped back to disconnected with an "install the CLI"
# message even though the login had been done: the binary was gone, and
# any login written outside `usr/` was gone with it.
#
# This module fixes both halves for good:
#
#   * installs go to a persisted npm prefix under `usr/` (npm_prefix()),
#     and binaries are resolved from there first (resolve_binary()), so an
#     install done once is still there after a recreate;
#   * a login that landed in a non-persisted default location (root's
#     `$HOME`, e.g. from `docker exec <c> bash -c ...`, which is
#     non-interactive and therefore never sources /root/.bashrc's HOME
#     override) is adopted into the persisted directory the first time it
#     is seen (adopt_file() / adopt_tree()), after which the persisted copy
#     is the only one anything reads or refreshes.
CLI_RUNTIME_SLUG = "_cli"
NPM_BINARY = "npm"
NPM_TIMEOUT_SECONDS = 300


def persisted_root() -> Path | None:
    """Returns `usr/plugins/_oauth/_cli`, or None if `usr/` is unreachable.

    None happens in isolated unit-test runs, where the full Agent Zero
    `helpers.files` module provider_data_dir() needs is not importable.
    Every caller degrades to the unpersisted default in that case rather
    than failing the CLI call over it.
    """
    try:
        from plugins._oauth.helpers.providers.base import provider_data_dir

        return provider_data_dir(CLI_RUNTIME_SLUG)
    except Exception:
        return None


def npm_prefix() -> Path | None:
    """Persisted `npm install -g --prefix` target, created on demand."""
    root = persisted_root()
    if root is None:
        return None
    try:
        prefix = root / "npm"
        prefix.mkdir(parents=True, exist_ok=True)
        return prefix
    except OSError:
        return None


def bin_dirs(*, home: Path | None = None) -> list[Path]:
    """Directories to look for a CLI binary in, highest priority first.

    Covers the persisted npm prefix this module installs into, plus the
    two locations a `curl | bash`-style installer (Cursor CLI's only
    supported install path) writes to under whatever `$HOME` the
    installing shell had -- which, in a `docker exec` shell, is the
    relocated persisted HOME, a directory that is not on the run_ui
    service's own PATH.
    """
    dirs: list[Path] = []
    prefix = npm_prefix()
    if prefix is not None:
        dirs.append(prefix / "bin")
    for base in _dedupe(path for path in [home, *default_homes()] if path is not None):
        dirs.append(base / ".local" / "bin")
        dirs.append(base / ".cursor" / "bin")
    return _dedupe(dirs)


def resolve_binary(name: str, *, home: Path | None = None) -> str:
    """Absolute path to `name` if we can find it, else `name` unchanged.

    Returning the bare name as a fallback keeps behaviour identical to the
    plain PATH lookup this replaced, so a CLI installed by some other
    means still works.
    """
    for directory in bin_dirs(home=home):
        candidate = directory / name
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    found = shutil.which(name)
    return found or name


def env_with_bin_path(env: dict[str, str], *, home: Path | None = None) -> dict[str, str]:
    """Prepends bin_dirs() to `env`'s PATH.

    Needed even when resolve_binary() already returned an absolute path:
    these CLIs re-invoke their own helper binaries (and, for npm-installed
    ones, `node`) by bare name through PATH.
    """
    entries = [str(directory) for directory in bin_dirs(home=home)]
    existing = env.get("PATH") or os.defpath
    env["PATH"] = os.pathsep.join([*entries, existing]) if entries else existing
    return env


def default_homes() -> list[Path]:
    """Non-persisted home directories a login may have landed in.

    In order of likelihood: whatever `$HOME` this process actually has
    (run_ui is started by supervisord, which never sees the relocated
    HOME), then root's home explicitly, for the case where a login ran
    under a different account's `$HOME` than the web service.
    """
    homes: list[Path] = []
    raw = (os.environ.get("HOME") or "").strip()
    if raw:
        homes.append(Path(raw))
    homes.append(Path("/root"))
    try:
        homes.append(Path.home())
    except (OSError, RuntimeError):
        pass
    return _dedupe(homes)


def adopt_file(target: Path, candidates: Iterable[Path]) -> Path | None:
    """Copies the first non-empty candidate file to `target` if it is missing.

    Returns the path actually copied from, or None if nothing was adopted
    (either `target` already holds a session, or no candidate does). Once
    adopted, the persisted copy is what every later call reads and what
    the CLI refreshes its token into, so this runs at most once per login.
    """
    if _non_empty_file(target):
        return None
    for candidate in _dedupe(candidates):
        if candidate == target or not _non_empty_file(candidate):
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            os.chmod(target, 0o600)
        except OSError:
            continue
        return candidate
    return None


def adopt_tree(target: Path, candidates: Iterable[Path]) -> Path | None:
    """Directory form of adopt_file(), for CLIs whose session is a whole dir.

    Used for Command Code, whose `~/.commandcode` layout is not documented
    file by file -- copying the directory wholesale is the only way to
    adopt it without guessing at filenames.
    """
    if _non_empty_dir(target):
        return None
    for candidate in _dedupe(candidates):
        if candidate == target or not _non_empty_dir(candidate):
            continue
        try:
            shutil.copytree(candidate, target, dirs_exist_ok=True)
        except (OSError, shutil.Error):
            continue
        return candidate
    return None


def purge_files(paths: Iterable[Path]) -> list[Path]:
    """Deletes each existing path, returning the ones actually removed.

    Disconnect has to clear the adoptable stray copies as well as the
    persisted one -- otherwise adopt_file() would immediately re-adopt the
    stray credential the next time the status page is read, and Disconnect
    would appear to do nothing.
    """
    removed: list[Path] = []
    for path in _dedupe(paths):
        try:
            if not path.is_file():
                continue
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed


def npm_available() -> bool:
    try:
        result = subprocess.run(
            [NPM_BINARY, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def npm_install_global(package: str) -> dict[str, Any]:
    """Installs `package@latest` into the persisted npm prefix.

    Only ever installs the binary -- it never touches authentication; that
    step stays with the user, outside Agent Zero (see each provider's
    NOT_DRIVEN_MESSAGE). Falls back to npm's own default prefix when
    `usr/` is unreachable, which is no worse than the previous behaviour.
    """
    if not npm_available():
        return {
            "ok": False,
            "error": "npm is not available on PATH. Install Node.js first, then retry.",
        }

    args = [NPM_BINARY, "install", "-g"]
    prefix = npm_prefix()
    if prefix is not None:
        args.extend(["--prefix", str(prefix)])
    args.append(f"{package}@latest")

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=NPM_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"npm install timed out after {NPM_TIMEOUT_SECONDS}s."}
    except OSError as exc:
        return {"ok": False, "error": f"Unable to run npm install: {exc}"}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        # npm's own output can be long (deprecation warnings, audit noise);
        # keep only the tail, which is where the actual failure reason is.
        detail = detail[-500:] if detail else "npm install failed."
        return {"ok": False, "error": detail}

    return {"ok": True, "error": ""}


def _non_empty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _non_empty_dir(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result
