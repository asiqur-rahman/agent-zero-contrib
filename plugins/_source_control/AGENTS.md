# Source Control Plugin DOX

## Purpose

- Own a right-canvas panel that tracks git changes (status, diffs, stage/unstage, commit) for the currently active project's repository.

## Ownership

- `api/source_control.py` owns the single backend API handler (`/plugins/_source_control/source_control`), dispatched by an `action` field.
- `webui/` owns the panel markup, its Alpine store, and the modal/canvas entry (`main.html`).
- `extensions/webui/right_canvas_register_surfaces/` (and its `surfaces_register/` compat alias) owns registering this panel as a right-canvas surface.

## Local Contracts

- Scope is deliberately **view + basic actions only**: status, per-file diffs, stage/unstage, and commit. No pull/push, no branch creation/switching/deletion, no merge/rebase -- those are out of scope for this panel; a full git client is a different, much larger feature.
- Tracks the **active project's** repository only (`helpers.projects.get_context_project_name()` -> `get_project_folder()`), not Agent Zero's own installation repo and not an arbitrary path. No project active, or the active project isn't a git repo, is a normal "nothing to show" state, not an error.
- All git mechanics live in `helpers/git.py` (`list_changed_files`, `get_file_diff`, `get_untracked_file_preview`, `stage_files`, `unstage_files`, `commit_staged`), not duplicated here -- this plugin is a thin UI + API layer over that module.
- Every git helper call is scoped to the resolved project path; never operate on a path outside `usr/projects/<name>`.
- Excludes `.a0proj` (A0's own project metadata) from every change list and diff, matching `get_repo_status()`'s existing dirty-check convention.
- `git diff`/`git diff --cached` (via GitPython's `repo.git` command proxy) are used for listing and diffing, not the `IndexFile.diff()` object API -- the latter's staged-vs-HEAD direction was verified inverted from `git status` conventions against a real repo, and it rejects the pre-first-commit (`NULL_TREE`) case outright. Do not reintroduce it without re-verifying both cases against a real repo first.

## Work Guidance

- Keep the panel's file list and diff view driven entirely by the API's `status`/`diff` actions -- no client-side git logic.
- Surface commit/stage/unstage errors (e.g. "nothing staged", blank commit message) directly from the API's `error` field; don't reinterpret them client-side.

## Verification

- `tests/test_git_source_control.py` covers the `helpers/git.py` mechanics directly against real (not mocked) git repos -- extend it, don't replace it with mocks, when adding new git operations here.
- Manual smoke test: activate a project with a real git repo, make a change, confirm it shows up staged/unstaged correctly, stage it, commit it, confirm the panel refreshes to a clean state.

## Child DOX Index

No child DOX files.
