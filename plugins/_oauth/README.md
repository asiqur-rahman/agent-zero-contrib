# OAuth Connections

Generic local OAuth bridge for Agent Zero.

Tokens in `auth.json` are password-equivalent credentials. Keep this plugin on trusted local machines only. Do not configure `auth_file_path` to share a rotating refresh-token file with Codex CLI or another client.

The settings UI groups providers as account-backed connections. More than one account provider can be connected at the same time, and the Main/Utility model slots can choose models from any connected OAuth provider.

Each model slot has its own provider selector. The selector lists connected OAuth accounts only, so Main and Utility can use different account-backed providers when more than one account is connected.

OAuth-backed model providers do not require users to enter API keys. Agent Zero supplies a local dummy key only at runtime after the selected account provider is connected, so unconnected providers stay blank in API-key surfaces.

## Providers

### Codex/ChatGPT (`codex_oauth`)

- Uses the existing Codex device-code flow.
- Writes Codex-compatible credentials to an Agent Zero-owned `auth.json` file.
- Refreshes local tokens when needed.
- Exposes the local OpenAI-compatible wrapper at `/oauth/codex/v1`.
- Lets users choose default reasoning effort, visible reasoning summaries, and answer verbosity while preserving explicit per-request settings.

### GitHub Copilot (`github_copilot_oauth`)

- Uses GitHub's OAuth device flow.
- Exchanges the GitHub access token for a Copilot API token.
- Stores credentials under `usr/plugins/_oauth/github_copilot/auth.json`.

### Google Cloud Gemini (`gemini_api_oauth`)

- Uses Google's OAuth authorization-code flow with PKCE.
- Requires a user-provided Google Cloud OAuth client with the Generative Language API enabled.
- Proxies the official Gemini OpenAI-compatible endpoint at `/oauth/gemini-api/v1`.
- Stores credentials under `usr/plugins/_oauth/gemini_api/auth.json`.
- Uses Gemini API billing and quotas. It does not use Antigravity, Gemini Code Assist, Gemini CLI, Google AI Pro, or Google AI Ultra subscription quota.

### xAI Grok (`xai_grok_oauth`)

- Uses xAI's browser-based PKCE flow.
- Supports manual callback paste for remote hosts where the browser cannot reach the local callback directly.
- Stores credentials under `usr/plugins/_oauth/xai_grok/auth.json`.

### Command Code (`command_code_cli`)

- Command Code (https://commandcode.ai) publishes no OAuth/REST API for third parties, so this provider does not drive a login flow the way the others do -- it shells out to the locally installed `command-code` CLI binary and uses its own public contract instead: `command-code status --json` for account status, and `command-code -p ... --output-format json` (headless mode) for generation.
- Sign-in happens outside Agent Zero: install the CLI (`npm i -g command-code`) and run `command-code login` yourself on the machine running Agent Zero, then click Refresh in this settings page. `start_login`/`poll_login`/callback methods all fail closed with that instruction -- there is no OAuth handshake for this plugin to perform.
- Requires Node.js and the `command-code` npm package on the PATH of the process running Agent Zero. Not currently installed in the shipped Docker image -- this provider only works when Agent Zero runs where you've installed the CLI yourself (e.g. a native `python run_ui.py` checkout).
- Each chat completion is a fresh, stateless `command-code -p` invocation (no `--resume`), matching how Agent Zero sends its full conversation state on every call regardless of backend.
- "Disconnect" cannot sign the CLI out remotely -- the credential lives in `~/.commandcode`, shared with any other tool using it on that machine. Run `command-code logout` yourself if you want to sign out.
- `~/.commandcode` resolves against `$HOME`, which by default is outside the one directory (`usr/`) this container persists across restarts, image updates, or redeploys -- every recreation would otherwise lose the login. Unlike Claude Code below, Command Code has no documented config-dir override, so there is no scoped env var to relocate just its config; only a full `HOME` override works (Cursor CLI, below, turns out to be in this same boat, despite first appearances). Because `HOME` is far more load-bearing than `CLAUDE_CONFIG_DIR` alone (npm cache, shell profile, etc.), this is set as an **interactive-shell-only export in `/root/.bashrc`/`/root/.profile`** (see `docker/run/fs/per/root/`), not a container-wide Dockerfile `ENV` -- so a plain `command-code login` inside a `docker exec` shell persists correctly on its own, without affecting supervisord's own services (sshd, cron, searxng, run_ui), which never see a relocated `HOME`. One side effect: `cd ~`/`~` expansion inside that shell now resolves to `usr/plugins/_oauth/command_code_cli/home` instead of `/root` -- shared with Cursor CLI's own dotfiles too, see below. (Running Agent Zero natively outside Docker, or from a non-interactive shell that skips `.bashrc`: export `HOME` to that same path yourself before logging in.)

### Claude Code (`claude_code_cli`)

- Anthropic's Claude Code CLI (https://docs.claude.com/claude-code) also publishes no third-party OAuth/REST API, so this provider follows the same external-CLI pattern as Command Code: it shells out to the locally installed `claude` binary and uses its own public headless contract, `claude -p ... --output-format json`, for generation. No tool permissions are granted (no `--allowedTools`/`--permission-mode`), so this is a plain text-completion backend, not the agentic coding delegate the separate `_orchestrator` plugin's Claude Code adapter provides -- see that plugin if you want Claude Code to actually read/edit files or run shell commands on Agent Zero's behalf.
- Sign-in happens outside Agent Zero: install the CLI (`npm i -g @anthropic-ai/claude-code`) and run `claude auth login` yourself (or set `ANTHROPIC_API_KEY`), then click Refresh in this settings page. `start_login`/`poll_login`/callback methods all fail closed with that instruction.
- Unlike Command Code, this provider points the CLI's `CLAUDE_CONFIG_DIR` at `usr/plugins/_oauth/claude_code_cli/config` -- a directory under this container's one persisted volume -- so the login **does** survive a container recreation, using Claude Code's own documented config-dir override. The shipped Docker image sets `CLAUDE_CONFIG_DIR` as a container-level env var (see `docker/run/Dockerfile`), so a plain `claude auth login` inside a `docker exec` shell already writes there automatically -- no manual export needed. (Running Agent Zero natively outside Docker: export `CLAUDE_CONFIG_DIR` to that same path yourself before logging in, or the CLI falls back to its default `~/.claude`.)
- Auth state is detected by checking for `ANTHROPIC_API_KEY` or a non-empty `.credentials.json` under that config dir -- there is no `status --json` equivalent to shell out to, so this mirrors `plugins/_orchestrator/helpers/adapters/claude.py`'s own detection instead.
- Has no documented `--list-models` catalog to introspect, so `models()` always falls back to a curated list.
- Each chat completion is a fresh, stateless `claude -p` invocation (no `--resume`/`--continue`), matching how Agent Zero sends its full conversation state on every call regardless of backend.
- "Disconnect" deletes the `.credentials.json` under this plugin's own config dir -- safe here (unlike Command Code) because that directory is exclusively owned by this plugin, not shared system-wide.

### Cursor CLI (`cursor_cli`)

- Cursor CLI (https://cursor.com/cli) also publishes no third-party OAuth/REST API, so this follows the same external-CLI pattern -- it shells out to the locally installed `agent` binary using the headless contract confirmed in `plugins/_orchestrator/skills/orchestrator/references/cursor.md`: `agent -p --output-format text "<prompt>"`.
- **Unlike Command Code and Claude Code, this provider never auto-installs the CLI**, even on "Connect". Cursor's official installer is `curl https://cursor.com/install -fsS | bash` -- an unscoped shell script piped from a URL, not a scoped package-manager install like `npm install -g <package>` -- so it is never run automatically. Install it yourself, then run `agent` or `NO_OPEN_BROWSER=1 agent login` (or set `CURSOR_API_KEY`/`API_KEY_CURSOR`), then click Refresh in this settings page.
- **Confirmed live against the real CLI (v2026.09.02-c22c1a3): Cursor CLI does not honor a `CURSOR_HOME` override at all.** It always writes to `$HOME/.cursor/cli-config.json`, ignoring `CURSOR_HOME` entirely -- this contradicts what `plugins/_orchestrator/helpers/adapters/cursor.py` assumes (that adapter has not been verified against a real CLI session either) and what this provider originally assumed too. Since only `$HOME` actually relocates Cursor's config, this provider shares Command Code's persisted `HOME` directory (see the Command Code entry above) instead of maintaining a separate one -- exported in `/root/.bashrc`/`/root/.profile`, not a Dockerfile `ENV`, for the same load-bearing-`HOME` reasons already described for Command Code. A plain `agent login` (or the interactive `agent` first-run login) inside a `docker exec` shell persists correctly on its own. (Running Agent Zero natively outside Docker, or from a non-interactive shell that skips `.bashrc`: export `HOME` to that same shared path yourself before logging in.)
- Auth state is detected by reading `cli-config.json`'s `authInfo` object (`userId`/`email`/`displayName`/`authId`, populated only when logged in) -- confirmed against real CLI output. There is no separate token/credential file, and no documented `agent status --json` to parse.
- Cursor CLI selects models via its own interactive `/model` command, not a CLI flag this provider could drive per-request, so `models()` always returns a single `"auto"` placeholder rather than a fake selectable list.
- Completions use `--output-format text`, not `--output-format json` -- there is no confirmed JSON result schema for this CLI to parse against (unlike Command Code and Claude Code, both verified against real CLI output), so usage stats are always empty.
- "Disconnect" removes `cli-config.json` -- safe because Cursor's own `.cursor/` subdirectory under the shared `HOME` is exclusively its own, even though the parent directory is shared with Command Code.

## Usage Plan Metadata

The status API exposes `usage_plan_catalog` for subscription and billing context. It covers only connectable providers: Codex, GitHub Copilot, Google Cloud Gemini, and xAI Grok. Command Code, Claude Code, and Cursor CLI are not included -- their billing/tier metadata isn't published anywhere this plugin can read.

The same status response also includes `oauth_accounts`, a compact summary used by the settings modal, welcome discovery card, and onboarding wizard. Keep that summary provider-registry driven so new OAuth providers appear consistently across those surfaces.

## Remote xAI Callback

When Agent Zero is running on a remote host, the browser may complete the xAI authorization step somewhere other than the machine serving the local callback route. In that case, paste the callback value into the xAI card.

The xAI card accepts any of these formats:

- Full callback URL.
- Query string such as `?code=...&state=...`.
- Bare authorization code.
