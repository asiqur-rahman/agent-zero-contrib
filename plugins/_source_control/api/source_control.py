from __future__ import annotations

from helpers.api import ApiHandler, Input, Output, Request
from helpers import git, projects


class SourceControl(ApiHandler):
    """Backs the Source Control canvas panel for the active project's git repo.

    View + basic-actions scope only (status, diffs, stage/unstage, commit) --
    no pull/push/branch management. See plugins/_source_control/AGENTS.md.
    """

    async def process(self, input: Input, request: Request) -> Output:
        action = str(input.get("action") or "status").strip().lower()

        try:
            repo_path = self._resolve_active_project_path(input.get("context_id"))

            if action == "status":
                return self._status(repo_path)
            if action == "diff":
                return self._diff(repo_path, input)
            if action == "stage":
                return self._stage(repo_path, input)
            if action == "unstage":
                return self._unstage(repo_path, input)
            if action == "commit":
                return self._commit(repo_path, input)
            return {"ok": False, "error": f"Unsupported source control action: {action}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _resolve_active_project_path(self, ctxid: object) -> str:
        if not ctxid:
            raise Exception("context_id is required")
        context = self.use_context(str(ctxid))
        name = projects.get_context_project_name(context)
        if not name:
            raise Exception("No project is active in this chat")
        return projects.get_project_folder(name)

    def _status(self, repo_path: str) -> dict:
        repo_status = git.get_repo_status(repo_path)
        if not repo_status.get("is_git_repo"):
            return {
                "ok": True,
                "is_git_repo": False,
                "error": repo_status.get("error", ""),
            }
        changes = git.list_changed_files(repo_path)
        return {
            "ok": True,
            "is_git_repo": True,
            "current_branch": repo_status.get("current_branch", ""),
            "remote_url": repo_status.get("remote_url", ""),
            "last_commit": repo_status.get("last_commit"),
            **changes,
        }

    def _diff(self, repo_path: str, input: Input) -> dict:
        path = str(input.get("path") or "").strip()
        if not path:
            raise Exception("path is required")
        if input.get("untracked"):
            text = git.get_untracked_file_preview(repo_path, path)
        else:
            text = git.get_file_diff(repo_path, path, staged=bool(input.get("staged")))
        return {"ok": True, "path": path, "diff": text}

    def _stage(self, repo_path: str, input: Input) -> dict:
        git.stage_files(repo_path, self._paths_from_input(input))
        return {"ok": True}

    def _unstage(self, repo_path: str, input: Input) -> dict:
        git.unstage_files(repo_path, self._paths_from_input(input))
        return {"ok": True}

    def _commit(self, repo_path: str, input: Input) -> dict:
        commit_sha = git.commit_staged(repo_path, str(input.get("message") or ""))
        return {"ok": True, "commit": commit_sha}

    def _paths_from_input(self, input: Input) -> list[str]:
        paths = input.get("paths")
        if isinstance(paths, list):
            return [str(path) for path in paths if str(path).strip()]
        single = input.get("path")
        return [str(single)] if single else []
