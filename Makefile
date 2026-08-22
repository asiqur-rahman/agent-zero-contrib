# Local Docker lifecycle for this checkout, mirroring the pattern used in
# the OpenHands-contrib repo's Makefile. Builds always use DockerfileLocal
# (the current checkout, not a git-cloned branch) via docker-compose.local.yml
# -- see that file's header for why it's separate from
# docker/run/docker-compose.yml. For raw docker/docker buildx commands
# (git-branch-based builds, manual pushes), see docker/run/build.txt.

COMPOSE := docker compose -p agent-zero-local -f docker-compose.local.yml

.PHONY: up down clean dev logs push push-check

# Build the local image from this checkout and run it.
up:
	$(COMPOSE) up -d --build
	@echo "Started. Access: http://localhost:$${A0_PORT:-50080}"

# Run the WebUI natively (no Docker) for fast iteration -- this is the dev
# workflow documented in AGENTS.md, not a hot-reload container (Agent Zero's
# Flask + Alpine.js WebUI has no build step to hot-reload).
dev:
	python run_ui.py

# Stop the container (data volume kept).
down:
	$(COMPOSE) down

# Stop the container and remove its data volume.
clean:
	$(COMPOSE) down -v

# Follow container logs.
logs:
	$(COMPOSE) logs -f

# Build fresh (via DockerfileLocal) and push asiqurrahman/agent-zero
# :production + :vX.Y to Docker Hub. Prompts for the version on the console
# (suggests the next minor after Docker Hub's newest vX.Y tag); skip the
# prompt with: make push VERSION=vX.Y
# Requires `docker login` to have been run already -- never enters credentials.
push:
	@CLI_VERSION="$(if $(filter command line,$(origin VERSION)),$(VERSION),)" \
		PLATFORMS="$(PLATFORMS)" \
		bash scripts/push-agent-zero.sh

# Verify push preflight (docker, buildx, login, required files) without
# building or pushing anything.
push-check:
	@bash scripts/push-agent-zero.sh --self-test
