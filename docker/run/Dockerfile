# Use the pre-built base image for A0
# FROM agent-zero-base:local
# FROM agent0ai/agent-zero-base:testing
FROM agent0ai/agent-zero-base:latest

# Check if the argument is provided, else throw an error
ARG BRANCH
RUN if [ -z "$BRANCH" ]; then echo "ERROR: BRANCH is not set!" >&2; exit 1; fi
ENV BRANCH=$BRANCH

# Relocate the Claude Code / Cursor CLI config dirs under the one directory
# this container persists across restarts (usr/), so a `claude auth login`
# or `agent login` run in a `docker exec` shell survives a redeploy without
# the user needing to export these themselves. See plugins/_oauth/README.md.
ENV CLAUDE_CONFIG_DIR=/a0/usr/plugins/_oauth/claude_code_cli/config
ENV CURSOR_HOME=/a0/usr/plugins/_oauth/cursor_cli/home

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
