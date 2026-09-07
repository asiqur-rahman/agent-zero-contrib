from git import Git, Repo
from giturlparse import parse
from datetime import datetime, timezone
from dataclasses import dataclass
import os
import subprocess
import base64
import re
import time
from urllib.parse import urlparse, urlunparse
from helpers import files
from helpers.localization import Localization


def strip_auth_from_url(url: str) -> str:
    """Remove any authentication info from URL."""
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.hostname:
        return url
    clean_netloc = parsed.hostname
    if parsed.port:
        clean_netloc += f":{parsed.port}"
    return urlunparse((parsed.scheme, clean_netloc, parsed.path, '', '', ''))


def extract_author_repo(url: str) -> tuple[str, str]:
    parsed = parse(strip_auth_from_url(url.strip()))
    author = (parsed.owner or "").strip()
    repo = (parsed.repo or parsed.name or "").strip()
    if not parsed.valid or not author or not repo:
        raise ValueError("Could not derive plugin name from URL")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not author or not repo:
        raise ValueError("Could not derive plugin name from URL")
    return author, repo


@dataclass
class GitHeadInfo:
    hash: str
    short_hash: str
    message: str
    author: str
    committed_at: str
    authored_at: str


@dataclass
class GitReleaseInfo:
    tag: str
    short_tag: str
    version: str
    released_at: str


@dataclass
class GitRemoteReleaseInfo:
    tag: str
    commit_hash: str
    short_commit_hash: str
    released_at: str


@dataclass
class GitRemoteReleasesResult:
    is_git_repo: bool
    is_remote: bool
    author: str
    repo: str
    releases: list[GitRemoteReleaseInfo]
    error: str = ""


@dataclass
class GitRemoteCommitsInfo:
    is_git_repo: bool
    is_remote: bool
    path: str
    branch: str
    remote_branch: str
    commits_since_local: int
    last_remote_commit_at: str
    error: str = ""


@dataclass
class GitRepoReleaseInfo:
    is_git_repo: bool
    is_remote: bool
    path: str
    author: str
    repo: str
    branch: str
    head: GitHeadInfo | None
    release: GitReleaseInfo | None
    error: str = ""


def _format_git_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).strftime('%Y-%m-%d %H:%M:%S')


def _split_describe_version(describe: str) -> tuple[str, int]:
    normalized = describe.strip()
    match = re.fullmatch(r"(.+)-(\d+)-g[0-9a-f]+", normalized)
    if not match:
        return normalized, 0
    return match.group(1), int(match.group(2))


def _format_release_version(
    branch: str,
    short_tag: str,
    commits_since_tag: int,
    commit_hash: str,
) -> str:
    version_prefix = branch[0].upper() if branch else "D"
    version_core = short_tag or commit_hash[:7]

    if (
        short_tag
        and commits_since_tag > 0
        and branch.strip().lower() != "main"
    ):
        version_core = f"{short_tag}+{commits_since_tag}"

    return f"{version_prefix} {version_core}"


def get_remote_releases(author: str, repo: str) -> GitRemoteReleasesResult:
    try:
        author = author.strip()
        repo = repo.strip()

        if not author or not repo:
            return GitRemoteReleasesResult(
                is_remote=False,
                is_git_repo=False,
                author=author,
                repo=repo,
                releases=[],
                error="Both author and repo are required.",
            )

        remote_url = f"https://github.com/{author}/{repo}.git"

        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'

        try:
            output = Git().ls_remote('--tags', '--refs', '--', remote_url, with_extended_output=False, env=env)
        except Exception as e:
            return GitRemoteReleasesResult(
                is_remote=True,
                is_git_repo=False,
                author=author,
                repo=repo,
                releases=[],
                error=f"Git remote query failed: {str(e)}",
            )

        releases: list[GitRemoteReleaseInfo] = []

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) != 2:
                continue

            commit_hash, ref_name = parts
            prefix = 'refs/tags/'
            if not ref_name.startswith(prefix):
                continue

            tag_name = ref_name[len(prefix):]
            releases.append(GitRemoteReleaseInfo(
                tag=tag_name,
                commit_hash=commit_hash,
                short_commit_hash=commit_hash[:7],
                released_at="",
            ))

        releases.sort(key=lambda release: release.tag, reverse=True)

        return GitRemoteReleasesResult(
            is_git_repo=True,
            is_remote=True,
            author=author,
            repo=repo,
            releases=releases,
        )
    except Exception as e:
        return GitRemoteReleasesResult(
            is_git_repo=False,
            is_remote=False,
            author=author,
            repo=repo,
            releases=[],
            error=str(e),
        )


def get_remote_commits_since_local(repo_path: str) -> GitRemoteCommitsInfo:
    try:
        repo = Repo(repo_path)
        if repo.bare:
            return GitRemoteCommitsInfo(
                is_git_repo=False,
                is_remote=False,
                path=repo_path,
                branch="",
                remote_branch="",
                commits_since_local=0,
                last_remote_commit_at="",
                error=f"Repository at {repo_path} is bare and cannot be used.",
            )

        if repo.head.is_detached:
            return GitRemoteCommitsInfo(
                is_git_repo=True,
                is_remote=False,
                path=repo_path,
                branch="",
                remote_branch="",
                commits_since_local=0,
                last_remote_commit_at="",
                error="Repository HEAD is detached.",
            )

        branch = repo.active_branch.name

        tracking_branch = repo.active_branch.tracking_branch()
        if tracking_branch is None:
            return GitRemoteCommitsInfo(
                is_git_repo=True,
                is_remote=False,
                path=repo_path,
                branch=branch,
                remote_branch="",
                commits_since_local=0,
                last_remote_commit_at="",
                error="Current branch has no tracking remote branch.",
            )

        remote_name = tracking_branch.remote_name
        remote = repo.remotes[remote_name]
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        with repo.git.custom_environment(**env):
            remote.fetch(repo.active_branch.name)

        remote_commit = tracking_branch.commit
        commits = list(repo.iter_commits(f"{repo.head.commit.hexsha}..{tracking_branch.path}"))

        return GitRemoteCommitsInfo(
            is_git_repo=True,
            is_remote=True,
            path=repo_path,
            branch=branch,
            remote_branch=tracking_branch.path,
            commits_since_local=len(commits),
            last_remote_commit_at=_format_git_timestamp(remote_commit.committed_date) if commits else "",
        )
    except Exception as e:
        return GitRemoteCommitsInfo(
            is_git_repo=False,
            is_remote=False,
            path=repo_path,
            branch="",
            remote_branch="",
            commits_since_local=0,
            last_remote_commit_at="",
            error=str(e),
        )


def get_repo_release_info(repo_path: str) -> GitRepoReleaseInfo:
    try:
        repo = Repo(repo_path)
        if repo.bare:
            return GitRepoReleaseInfo(
                is_git_repo=False,
                is_remote=False,
                path=repo_path,
                author="",
                repo="",
                branch="",
                head=None,
                release=None,
                error=f"Repository at {repo_path} is bare and cannot be used.",
            )

        commit = repo.head.commit
        author = ""
        repo_name = ""
        is_remote = False

        try:
            if repo.remotes:
                author, repo_name = extract_author_repo(repo.remotes.origin.url)
                is_remote = bool(author and repo_name)
        except Exception:
            author = ""
            repo_name = ""
            is_remote = False

        branch = ""
        try:
            branch = repo.active_branch.name if repo.head.is_detached is False else ""
        except Exception:
            branch = ""

        tag = ""
        short_tag = ""
        release_time = ""
        commits_since_tag = 0
        try:
            tag = repo.git.describe(tags=True, always=True)
            short_tag, commits_since_tag = _split_describe_version(tag)

            tag_ref = next((t for t in repo.tags if t.name == short_tag), None)
            if tag_ref:
                release_commit = tag_ref.commit
                release_time = _format_git_timestamp(release_commit.committed_date)
        except Exception:
            tag = ""
            short_tag = ""
            release_time = ""
            commits_since_tag = 0

        version = _format_release_version(
            branch,
            short_tag,
            commits_since_tag,
            commit.hexsha,
        )

        return GitRepoReleaseInfo(
            is_git_repo=True,
            is_remote=is_remote,
            path=repo_path,
            author=author,
            repo=repo_name,
            branch=branch,
            head=GitHeadInfo(
                hash=commit.hexsha,
                short_hash=commit.hexsha[:7],
                message=str(commit.message).split("\n")[0][:200],
                author=str(commit.author),
                committed_at=_format_git_timestamp(commit.committed_date),
                authored_at=_format_git_timestamp(commit.authored_date),
            ),
            release=GitReleaseInfo(
                tag=tag,
                short_tag=short_tag,
                version=version,
                released_at=release_time,
            ),
        )
    except Exception as e:
        return GitRepoReleaseInfo(
            is_git_repo=False,
            is_remote=False,
            path=repo_path,
            author="",
            repo="",
            branch="",
            head=None,
            release=None,
            error=str(e),
        )


def get_git_info():
    # Get the current working directory (assuming the repo is in the same folder as the script)
    repo_path = files.get_base_dir()

    state = get_repo_release_info(repo_path)
    if not state.is_git_repo:
        raise ValueError(state.error or f"Repository at {repo_path} is not usable.")

    return {
        "branch": state.branch,
        "commit_hash": state.head.hash if state.head else "",
        "commit_time": state.head.committed_at if state.head else "",
        "tag": state.release.tag if state.release else "",
        "short_tag": state.release.short_tag if state.release else "",
        "version": state.release.version if state.release else "",
    }

def get_version():
    try:
        git_info = get_git_info()
        return str(git_info.get("short_tag", "")).strip() or "unknown"
    except Exception:
        return "unknown"


def is_official_agent_zero_repo() -> bool:
    """Return True when origin points to agent0ai/agent-zero."""
    try:
        repo = Repo(files.get_base_dir())
        if not repo.remotes:
            return False

        remote_url = strip_auth_from_url(repo.remotes.origin.url).lower().rstrip("/")

        if remote_url.endswith(".git"):
            remote_url = remote_url[:-4]

        allowed_repos = [
            "agent0ai/agent-zero",
            "frdel/agent-zero",
        ]
        return any(
            remote_url.endswith(f"github.com/{repo_name}")
            or remote_url.endswith(f"github.com:{repo_name}")
            for repo_name in allowed_repos
        )
    except Exception:
        return False


# HTTP Basic Auth username each host expects for a personal-access-token
# style credential. Using the wrong one causes an authentication failure
# even with a valid token -- e.g. GitLab rejects `x-access-token:<token>`.
# Unknown/custom hosts fall back to GitHub's convention, matching this
# function's own behavior before per-provider auth existed.
GIT_PROVIDER_AUTH_USERNAMES = {
    "github": "x-access-token",
    "gitlab": "oauth2",
    "bitbucket": "x-token-auth",
}


def clone_repo(url: str, dest: str, token: str | None = None, provider: str = "github"):
    """Clone a git repository. Uses http.extraHeader for token auth (never stored in URL/config)."""
    cmd = ['git']

    if token:
        auth_username = GIT_PROVIDER_AUTH_USERNAMES.get(
            (provider or "").strip().lower(), "x-access-token"
        )
        auth_string = f"{auth_username}:{token}"
        auth_base64 = base64.b64encode(auth_string.encode()).decode()
        cmd.extend(['-c', f'http.extraHeader=Authorization: Basic {auth_base64}'])

    cmd.extend(['clone', '--progress', '--', url, dest])
    
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    
    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or 'Unknown error'
        raise Exception(f"Git clone failed: {error_msg}")
    
    return Repo(dest)


class DirtyTreeConflictError(Exception):
    """Raised when a dirty plugin cannot be updated without overwriting local edits."""

    def __init__(self, conflicting_files: list[str]):
        super().__init__(
            "Local changes conflict with the update. "
            "Your plugin was restored without applying the update."
        )
        self.conflicting_files = conflicting_files


def _list_dirty_tracked_files(repo: "Repo") -> list[str]:
    """Return tracked files with uncommitted modifications, excluding A0 metadata."""
    def _is_a0_file(path: str) -> bool:
        return path.startswith(".a0proj") or path == ".a0proj"

    changed = {d.a_path for d in repo.index.diff(None)}
    changed.update(d.a_path for d in repo.index.diff("HEAD"))
    return sorted(p for p in changed if p and not _is_a0_file(p))


def update_repo(repo_path: str, auto_stash: bool = True) -> Repo:
    """Fast-forward the repo to its tracking branch.

    When `auto_stash` is True (default) and the working tree has uncommitted
    changes to tracked files, those changes are stashed before the pull and
    reapplied afterwards. If they conflict with the update, the repo and local
    edits are restored to their original state before `DirtyTreeConflictError`
    is raised.
    """
    repo = Repo(repo_path)
    if repo.bare:
        raise ValueError(f"Repository at {repo_path} is bare and cannot be updated.")

    if repo.head.is_detached:
        raise ValueError("Repository HEAD is detached.")

    branch = repo.active_branch.name
    tracking_branch = repo.active_branch.tracking_branch()
    if tracking_branch is None:
        raise ValueError("Current branch has no tracking remote branch.")

    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'

    dirty_files = _list_dirty_tracked_files(repo) if auto_stash else []
    original_head = repo.head.commit.hexsha
    if dirty_files:
        stash_msg = f"a0-auto-stash-{int(time.time())}"
        repo.git.stash("push", "-m", stash_msg, "--", *dirty_files)

    def restore_original_state():
        repo.git.reset("--hard", original_head)
        if dirty_files:
            repo.git.stash("pop")

    try:
        with repo.git.custom_environment(**env):
            repo.remotes[tracking_branch.remote_name].pull(branch)
    except Exception:
        if dirty_files:
            restore_original_state()
        raise

    if dirty_files:
        try:
            repo.git.stash("pop")
        except Exception:
            restore_original_state()
            raise DirtyTreeConflictError(dirty_files)

    return repo


def push_repo(repo_path: str) -> str:
    """Pushes the current branch to its tracking remote (or sets one up on the
    first push if none exists yet). Returns git's own output text.

    Uses whatever git credentials are already configured in the environment
    (SSH agent, a configured credential.helper, etc.) -- no token handling
    here, matching how the active project's own supplied git_token is
    already transient/clone-only by design (see helpers/projects.py's
    _PROJECT_TRANSIENT_INPUT_KEYS). GIT_TERMINAL_PROMPT=0 so a missing or
    invalid credential fails fast with a clear error instead of hanging on
    an interactive prompt that can never be answered here.
    """
    repo = Repo(repo_path)
    if repo.bare:
        raise ValueError(f"Repository at {repo_path} is bare and cannot be pushed.")
    if repo.head.is_detached:
        raise ValueError("Repository HEAD is detached; cannot push.")

    branch = repo.active_branch.name
    tracking_branch = repo.active_branch.tracking_branch()

    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'

    with repo.git.custom_environment(**env):
        if tracking_branch is not None:
            output = repo.git.push()
        else:
            if not repo.remotes:
                raise ValueError("No remote configured for this repository.")
            output = repo.git.push("--set-upstream", repo.remotes[0].name, branch)

    return output.strip() if output else "Everything up-to-date."


# Files to ignore when checking dirty status (A0 project metadata)
A0_IGNORE_PATTERNS = {".a0proj", ".a0proj/"}


def get_repo_status(repo_path: str) -> dict:
    """Get Git repository status, ignoring A0 project metadata files."""
    try:
        repo = Repo(repo_path)
        if repo.bare:
            return {"is_git_repo": False, "error": "Repository is bare"}
        
        # Remote URL (always strip auth info for security)
        remote_url = ""
        try:
            if repo.remotes:
                remote_url = strip_auth_from_url(repo.remotes.origin.url)
        except Exception:
            pass
        
        # Current branch
        try:
            current_branch = repo.active_branch.name if not repo.head.is_detached else f"HEAD@{repo.head.commit.hexsha[:7]}"
        except Exception:
            current_branch = "unknown"
        
        # Check dirty status, excluding A0 metadata
        def is_a0_file(path: str) -> bool:
            return path.startswith(".a0proj") or path == ".a0proj"
        
        # Filter out A0 files from diff and untracked
        changed_files = [d.a_path for d in repo.index.diff(None)] + [d.a_path for d in repo.index.diff("HEAD")]
        untracked = repo.untracked_files
        
        real_changes = [f for f in changed_files if not is_a0_file(f)]
        real_untracked = [f for f in untracked if not is_a0_file(f)]
        
        is_dirty = len(real_changes) > 0 or len(real_untracked) > 0
        untracked_count = len(real_untracked)
        
        last_commit = None
        try:
            commit = repo.head.commit
            last_commit = {
                "hash": commit.hexsha[:7],
                "message": str(commit.message).split("\n")[0][:80],
                "author": str(commit.author),
                "date": datetime.fromtimestamp(
                    commit.committed_date,
                    tz=Localization.get().get_tzinfo(),
                ).strftime('%Y-%m-%d %H:%M %Z')
            }
        except Exception:
            pass
        
        return {
            "is_git_repo": True,
            "remote_url": remote_url,
            "current_branch": current_branch,
            "is_dirty": is_dirty,
            "untracked_count": untracked_count,
            "last_commit": last_commit
        }
    except Exception as e:
        return {"is_git_repo": False, "error": str(e)}


# --- Source Control panel: change listing, diffs, stage/unstage, commit ---
# Deliberately view + basic-actions scope only (no pull/push/branch
# management) -- see plugins/_source_control/AGENTS.md.
#
# Built on plain `git diff`/`git diff --cached` via GitPython's `repo.git`
# command proxy rather than the `IndexFile.diff()` object API: the latter's
# staged-vs-HEAD direction turned out inverted from `git status`/`git diff
# --cached` conventions when verified against a real repo (a newly staged
# file came back "deleted" instead of "added"), and it flatly rejects
# NULL_TREE for the pre-first-commit case ("other must be None, INDEX, a
# Tree or Commit"). Plain `git diff --cached` already handles a repo with no
# commits yet correctly on its own (diffs against an empty tree), so no
# NULL_TREE special-casing is needed at all with this approach.

_NAME_STATUS_LABELS = {
    "A": "added",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "C": "copied",
    "T": "type_changed",
    "U": "unmerged",
}


def _is_a0_project_file(path: str | None) -> bool:
    """Excludes A0's own project metadata dir from user-facing change lists.

    Same exclusion already applied in get_repo_status()/_list_dirty_tracked_files()
    above, hoisted here for the Source Control panel's own functions.
    """
    if not path:
        return False
    return path.startswith(".a0proj") or path == ".a0proj"


def _open_repo(repo_path: str) -> Repo:
    repo = Repo(repo_path)
    if repo.bare:
        raise ValueError(f"Repository at {repo_path} is bare and cannot be used.")
    return repo


def _parse_name_status(output: str) -> list[dict]:
    entries = []
    for line in output.splitlines():
        if not line.strip():
            continue
        # Renamed/copied lines carry two tab-separated paths (old, new) with
        # a similarity-percentage suffix on the status code (e.g. "R100");
        # the LAST field is the path the user sees today.
        parts = line.split("\t")
        path = parts[-1]
        if _is_a0_project_file(path):
            continue
        entries.append({"path": path, "status": _NAME_STATUS_LABELS.get(parts[0][:1], "modified")})
    return entries


def list_changed_files(repo_path: str) -> dict:
    """Lists staged/unstaged changes and untracked files for the Source Control panel.

    Returns {"staged": [...], "unstaged": [...], "untracked": [...]}, where
    staged/unstaged entries are {"path": str, "status": "added"|"modified"|...}
    and untracked is a plain list of paths. A0 project metadata (.a0proj) is
    excluded throughout, matching get_repo_status()'s own dirty-check.
    """
    repo = _open_repo(repo_path)

    staged = _parse_name_status(repo.git.diff("--cached", "--name-status"))
    unstaged = _parse_name_status(repo.git.diff("--name-status"))
    untracked = [path for path in repo.untracked_files if not _is_a0_project_file(path)]

    return {"staged": staged, "unstaged": unstaged, "untracked": untracked}


def get_file_diff(repo_path: str, file_path: str, *, staged: bool = False) -> str:
    """Unified diff text for one tracked file's change.

    staged=True diffs what's staged (against HEAD, or an empty tree
    pre-first-commit); staged=False diffs the working tree against the
    index (what's not yet staged). Untracked files have no meaningful diff
    here -- use get_untracked_file_preview() for those instead.
    """
    repo = _open_repo(repo_path)
    if staged:
        return repo.git.diff("--cached", "--", file_path)
    return repo.git.diff("--", file_path)


def get_untracked_file_preview(repo_path: str, file_path: str, *, max_bytes: int = 200_000) -> str:
    """Whole-file preview for an untracked file (there's no prior version to diff against)."""
    abs_path = os.path.join(repo_path, file_path)
    try:
        with open(abs_path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        return f"Unable to read {file_path}: {exc}"
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += "\n... (truncated)"
    return text


def stage_files(repo_path: str, paths: list[str]) -> None:
    if not paths:
        return
    repo = _open_repo(repo_path)
    repo.index.add(paths)


def unstage_files(repo_path: str, paths: list[str]) -> None:
    if not paths:
        return
    repo = _open_repo(repo_path)
    if repo.head.is_valid():
        repo.git.restore("--staged", "--", *paths)
    else:
        # `git restore --staged` needs a HEAD to restore from; pre-first-commit
        # there's nothing to restore TO, so just drop the paths from the index.
        repo.index.remove(paths, working_tree=False)


def commit_staged(repo_path: str, message: str) -> str:
    """Commits currently staged changes and returns the new commit's hex sha.

    Raises if the message is blank or nothing is staged -- this mirrors
    plain `git commit`'s own refusal, surfaced as a normal exception for the
    API layer's existing generic error handling to catch.
    """
    repo = _open_repo(repo_path)
    message = (message or "").strip()
    if not message:
        raise ValueError("Commit message is required.")
    if not repo.git.diff("--cached", "--name-only").strip():
        raise ValueError("Nothing staged to commit.")
    commit = repo.index.commit(message)
    return commit.hexsha
