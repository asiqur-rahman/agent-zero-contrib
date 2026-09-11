# Docker Runtime Image DOX

## Purpose

- Own the runnable Agent Zero image context and local compose example.
- Install Agent Zero from a selected branch onto the base image and prepare runtime entrypoints.

## Ownership

- `Dockerfile` owns branch-based image assembly, exposed ports, and container startup command.
- `docker-compose.yml` owns the local compose service example.
- `build.txt` owns maintainer build and push command notes.
- `fs/exe/` owns runtime entrypoint, supervisor, self-update, Node eval, and service scripts.
- `fs/ins/` owns preinstall, installation, virtualenv, Playwright, SSH, and postinstall scripts.
- Files under `fs/` are copied to container root during the runtime build.

## Local Contracts

- `BRANCH` is required for branch-based Docker builds.
- Preserve exposed ports for SSH, HTTP, and tunneled services unless docs and workflows are updated together.
- Keep the two-runtime Python model aligned with the root contract.
- Keep runtime desktop packages on `kali-last-snapshot`; align ATK's version-locked libraries and installed optional components with the pinned snapshot version, and pin the verified LibreOffice and complete Xpra runtime versions in `fs/ins/install_additional.sh` for both published architectures. Let those package dependencies constrain Python compatibility; do not depend on retired rolling package versions remaining downloadable.
- Do not bake secrets, local `.env` values, or user data into the image.
- Runtime startup must ensure `/a0/usr/uploads` exists before supervised services start.
- `CLAUDE_CONFIG_DIR` is set to a persisted path under `usr/` so `plugins/_oauth`'s Claude Code provider survives a container recreation; runtime startup must ensure that directory exists before supervised services start, same as `/a0/usr/uploads`. Do NOT add a `CURSOR_HOME` `ENV` for the same purpose -- confirmed live against the real Cursor CLI, it does not honor that variable at all and always writes to `$HOME/.cursor` regardless.
- Command Code CLI's `HOME` override (also a persisted path under `usr/`, for the same reason as above) is exported in `fs/per/root/.bashrc`/`.profile` instead of a Dockerfile `ENV`, deliberately -- `HOME` affects far more than one CLI's config, so it must stay scoped to interactive shells and never reach supervisord's own services. Do not move it to a container-wide `ENV`. Cursor CLI shares this exact same `HOME` relocation (see `plugins/_oauth/helpers/cursor_cli.py`'s module docstring) since it has no scoped override of its own -- both tools' dotfiles live side by side under it.
- External CLIs installed by `plugins/_oauth` (`command-code`, `claude`) go to the persisted npm prefix `/a0/usr/plugins/_oauth/_cli/npm`, which is on `PATH` as a container-wide `ENV` and is pre-created by `fs/exe/initialize.sh`. Adding a bin directory to `PATH` is safe container-wide (unlike `HOME`), and it has to be: `npm install -g`'s default `/usr/local` prefix lives in the writable layer and is discarded on every container recreate, which is what made a configured Command Code / Claude Code account come back as "not installed" and disconnected after an image update. Do not revert those installs to the default prefix.
- Runtime startup raises the soft open-file limit toward `A0_NOFILE_LIMIT` (default `65535`) before supervisord starts, bounded by the container hard limit.
- Self-update user-data backups skip Time Travel shadow history under `usr/.time_travel/` and transient Desktop agent state.
- Self-update rollback stashes use immutable Git object IDs for creation checks and restoration; resolve the matching reflog selector only when dropping that stash, preserving unrelated entries. Failed restoration retains the stash.
- Self-update waits up to 180 seconds for the updated or restored WebUI health check by default; `A0_SELF_UPDATE_HEALTH_TIMEOUT_SECONDS` may override it.
- Successful or already-current self-updates refresh an installed Codex CLI with npm on a best-effort basis; missing CLIs and registry failures must not block Agent Zero startup.

## Work Guidance

- Keep startup scripts explicit about framework runtime versus execution runtime.
- Coordinate tag, branch, and publishing changes with GitHub workflow automation.

## Verification

- Build `docker/run` when changing Dockerfile or install scripts.
- Smoke-test container startup after entrypoint, supervisor, port, or compose changes.

## Child DOX Index

No child DOX files.
