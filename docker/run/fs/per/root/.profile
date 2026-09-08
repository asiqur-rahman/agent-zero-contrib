# .bashrc

# Source global definitions
if [ -f /etc/bashrc ]; then
    . /etc/bashrc
fi

# Activate the virtual environment
source /opt/venv/bin/activate

# Persist Command Code CLI's session under usr/ (the one directory this
# container keeps across restarts/redeploys), so a plain `command-code
# login` run in this shell survives a recreation without typing an
# override -- see plugins/_oauth/README.md. Scoped to interactive shells
# here (not a Dockerfile ENV) so supervisord's own services (sshd, cron,
# searxng, run_ui) never see a relocated HOME -- unlike CLAUDE_CONFIG_DIR/
# CURSOR_HOME, HOME is load-bearing for far more than one CLI's config.
# Note: this also moves `cd ~`/`~` expansion in this shell to that path.
export HOME="/a0/usr/plugins/_oauth/command_code_cli/home"

# Persisted npm prefix for the _oauth plugin's external CLIs, plus the
# `~/.local/bin`/`~/.cursor/bin` targets Cursor CLI's own installer writes
# to under this relocated HOME -- so `command-code`, `claude` and `agent`
# resolve here the same way they do for the run_ui service.
export PATH="/a0/usr/plugins/_oauth/_cli/npm/bin:$HOME/.local/bin:$HOME/.cursor/bin:$PATH"
