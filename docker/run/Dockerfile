# Use the pre-built base image for A0
# FROM agent-zero-base:local
# FROM agent0ai/agent-zero-base:testing
FROM agent0ai/agent-zero-base:latest

# Check if the argument is provided, else throw an error
ARG BRANCH
RUN if [ -z "$BRANCH" ]; then echo "ERROR: BRANCH is not set!" >&2; exit 1; fi
ENV BRANCH=$BRANCH

# Relocate Claude Code's config dir under the one directory this container
# persists across restarts (usr/), so `claude auth login` run in a
# `docker exec` shell survives a redeploy without the user needing to
# export this themselves. See plugins/_oauth/README.md.
#
# Cursor CLI has no equivalent scoped override -- confirmed live against
# the real CLI, it ignores CURSOR_HOME entirely and always writes to
# $HOME/.cursor. That relocation is done in /root/.bashrc/.profile instead
# (shared with Command Code's own HOME relocation there), not here.
ENV CLAUDE_CONFIG_DIR=/a0/usr/plugins/_oauth/claude_code_cli/config

# Put the persisted npm prefix the _oauth plugin installs external CLIs into
# (Command Code, Claude Code) on PATH container-wide. `npm install -g`'s
# default /usr/local prefix lives in the container's writable layer, which
# is discarded on every recreate (image update, CasaOS app update,
# `docker compose up --force-recreate`) -- so a CLI installed once came
# back "not installed" and every account showed disconnected. Installing
# into usr/ instead keeps the binary across recreates, and this PATH entry
# is what makes it visible to supervisord's own run_ui service, not just to
# an interactive shell. See plugins/_oauth/helpers/cli_runtime.py.
ENV PATH=/a0/usr/plugins/_oauth/_cli/npm/bin:$PATH

# Copy filesystem files to root
COPY ./fs/ /

# pre installation steps
RUN bash /ins/pre_install.sh $BRANCH

# install A0
RUN bash /ins/install_A0.sh $BRANCH

# install additional software
RUN bash /ins/install_additional.sh $BRANCH

# cleanup repo and install A0 without caching, this speeds up builds
ARG CACHE_DATE=none
RUN echo "cache buster $CACHE_DATE" && bash /ins/install_A02.sh $BRANCH

# post installation steps
RUN bash /ins/post_install.sh $BRANCH

# Expose ports
EXPOSE 22 80 9000-9009

RUN chmod +x /exe/initialize.sh /exe/run_A0.sh /exe/run_searxng.sh /exe/run_tunnel_api.sh /exe/trigger_self_update.sh

# initialize runtime and switch to supervisord
CMD ["/exe/initialize.sh", "$BRANCH"]
