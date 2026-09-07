# Source Control Plugin DOX

## Purpose

- Own a right-canvas panel that tracks git changes (status, diffs, stage/unstage, commit, push/pull) for the currently active project's repository, with a VS Code-style two-pane layout (file list left, side-by-side diff right).

## Ownership

- `api/source_control.py` owns the single backend API handler (`/plugins/_source_control/source_control`), dispatched by an `action` field.
- `webui/` owns the panel markup, its Alpine store, and the modal/canvas entry (`main.html`).
- `extensions/webui/right_canvas_register_surfaces/` (and its `surfaces_register/` compat alias) owns registering this panel as a right-canvas surface.

## Local Contracts

- Scope is **view + basic actions + push/pull**: status, per-file diffs, stage/unstage, commit, push (to the branch's existing tracking remote, or `--set-upstream` on first push), and pull (fast-forward with auto-stash, via the existing `update_repo()`). Still explicitly out of scope: branch creation/switching/deletion, merge/rebase beyond what `update_repo()` already provides, and any credential UI -- push/pull use whatever git credentials are already configured in the environment (SSH agent, `credential.helper`, etc.); there is no token prompt here, matching the project clone flow's own transient-only `git_token` handling.
- Tracks the **active project's** repository only (`helpers.projects.get_context_project_name()` -> `get_project_folder()`), not Agent Zero's own installation repo and not an arbitrary path. No project active, or the active project isn't a git repo, is a normal "nothing to show" state, not an error.
- All git mechanics live in `helpers/git.py` (`list_changed_files`, `get_file_diff`, `get_untracked_file_preview`, `stage_files`, `unstage_files`, `commit_staged`, `push_repo`, and the pre-existing `update_repo`/`DirtyTreeConflictError` reused for pull), not duplicated here -- this plugin is a thin UI + API layer over that module.
- Every git helper call is scoped to the resolved project path; never operate on a path outside `usr/projects/<name>`.
- Excludes `.a0proj` (A0's own project metadata) from every change list and diff, matching `get_repo_status()`'s existing dirty-check convention.
- `git diff`/`git diff --cached` (via GitPython's `repo.git` command proxy) are used for listing and diffing, not the `IndexFile.diff()` object API -- the latter's staged-vs-HEAD direction was verified inverted from `git status` conventions against a real repo, and it rejects the pre-first-commit (`NULL_TREE`) case outright. Do not reintroduce it without re-verifying both cases against a real repo first.
- The side-by-side diff view is a plain-JS unified-diff parser owned by this plugin's own store (`parseUnifiedDiff` in `source-control-store.js`), not Ace's `ext/diff` extension (vendored and already used by `_editor`, but its `createDiffView` API surface could not be verified without a live browser session -- revisit only after confirming its exact option contract against a running instance, not from reading the minified source).

## Work Guidance

- Keep the panel's file list and diff view driven entirely by the API's `status`/`diff` actions -- no client-side git logic beyond the pure-function unified-diff-to-two-columns parser.
- Surface commit/stage/unstage/push/pull errors (e.g. "nothing staged", blank commit message, push auth failure, pull conflict) directly from the API's `error` field; don't reinterpret them client-side. A pull conflict additionally carries `conflicting_files` -- show it, don't attempt to auto-resolve.

## Verification

- `tests/test_git_source_control.py` covers the `helpers/git.py` mechanics directly against real (not mocked) git repos, including push/pull against a real local bare "remote" (two clones, no live network host needed) -- extend it, don't replace it with mocks, when adding new git operations here. Use plain `subprocess` git commands for test-repo setup (clone/push/branch), matching `tests/test_plugin_git_update.py`'s own pattern -- GitPython's `Remote.push()`/`checkout()` object-API wrappers produced a clone that silently failed to pull correctly in this exact push+diverge+pull scenario during development; the underlying `helpers/git.py` functions under test still use GitPython/`repo.git.*` correctly, this caveat is about test *setup* only.
- `parseUnifiedDiff()` in `source-control-store.js` was verified against 8 cases (1:1 modification, unequal removed/added counts, pure addition, pure deletion, multiple hunks, empty input, and -- the one real bug this caught -- a `\ No newline at end of file` marker sitting between the removed and added blocks, which broke the pairing loop until fixed to skip it inline within both collection loops, not just at top-level dispatch). This repo has no JS test framework/runner to hold a permanent regression test for it (no `package.json`, no `vitest`/`jest`, nothing under any `*.test.js`/`*.spec.js` convention) -- re-verify by hand (or add one, deliberately, if this ever becomes a recurring need) before changing the parsing logic, don't assume it still holds.
- Manual smoke test: activate a project with a real git repo, make a change, confirm it shows up staged/unstaged correctly, stage it, commit it, push it (against a real remote you can push to), and pull (with a divergent remote commit) to confirm the panel refreshes to a clean state each time.

## Child DOX Index

No child DOX files.
