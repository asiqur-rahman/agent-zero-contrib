#!/usr/bin/env bash
# Push asiqurrahman/agent-zero :production + :vX.Y to Docker Hub.
# :production is the rolling tag for this fork's current build (no :latest --
# this deliberately diverges from upstream agent0ai/agent-zero, so "latest
# upstream release" isn't the right name for what gets pushed here).
# Invoked by: make push   (or make push-check for a dry-run preflight)
# Also invoked headlessly by Jenkinsfile's "Push to Docker Hub" stage with
# CLI_VERSION preset (from the earlier "Suggest version" stage), skipping
# the interactive prompt below.
#
# Version is never hardcoded. In a console, make push always prompts,
# suggesting the next minor after the newest vX.Y tag already on Docker Hub
# (matching upstream agent-zero's own vX.Y git-tag convention -- no patch
# component). Override without prompting: make push VERSION=vX.Y
#
# Adapted from the same pattern used in the OpenHands-contrib repo's
# push-openhands-canvas.sh, simplified for this project: always builds via
# the repo-root DockerfileLocal (which COPYs the current checkout instead of
# `git clone`-ing a branch -- see docker/run/fs/ins/install_A0.sh), so what
# gets pushed is always exactly what's checked out locally or in CI, never a
# separate fetch of docker/run/Dockerfile's BRANCH=<name> git-clone path
# (that path is hardcoded to https://github.com/agent0ai/agent-zero and
# would silently build upstream's branch instead of this fork's).
#
# WSL/Ubuntu: make's default shell is dash -- this script always runs under bash.
# Prompt reads from /dev/tty so it still works when make redirects stdin.

set -euo pipefail

IMAGE="${IMAGE:-asiqurrahman/agent-zero}"
# Set only when user passes: make push VERSION=vX.Y (or Jenkins presets it)
CLI_VERSION="${CLI_VERSION:-}"
# amd64-only by default (fast, routine iteration). Override for a
# QEMU-emulated multi-arch build in one go: make push PLATFORMS=linux/amd64,linux/arm64
PLATFORMS="${PLATFORMS:-linux/amd64}"
BUILDER_NAME="agent-zero-multiarch"
DOCKERFILE="DockerfileLocal"

cd "$(dirname "$0")/.."

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "$*" >&2; }

preflight() {
  command -v docker >/dev/null 2>&1 || die "docker not found in PATH"
  docker info >/dev/null 2>&1 || die "Docker daemon not reachable. Start Docker / dockerd."
  docker buildx version >/dev/null 2>&1 || die "docker buildx not available"
  if [ ! -f "${HOME}/.docker/config.json" ]; then
    die "Not logged in to Docker Hub. Run: docker login"
  fi
  if ! grep -Eq '"auths"|"credsStore"|"credHelpers"' "${HOME}/.docker/config.json" 2>/dev/null; then
    die "Docker config has no credentials. Run: docker login"
  fi
}

# Best-effort only -- network/rate-limit must never abort push.
list_hub_tags() {
  local raw=""
  raw="$(curl -fsS -m 10 \
    "https://hub.docker.com/v2/repositories/${IMAGE}/tags/?page_size=100" 2>/dev/null || true)"
  [ -n "$raw" ] || return 0
  printf '%s' "$raw" \
    | grep -oE '"name":"[^"]*"' \
    | sed -E 's/"name":"([^"]*)"/\1/' \
    | grep -E '^v[0-9]+\.[0-9]+$' \
    || true
}

is_version() {
  printf '%s' "$1" | grep -Eq '^v[0-9]+\.[0-9]+$'
}

# Suggest next minor after Hub's newest tag (v2.10 -> v2.11), matching
# upstream agent-zero's major.minor-only tagging (no patch component).
# Fallback when Docker Hub has no tags yet (e.g. first ever push): the
# newest vX.Y tag already fetched into this checkout (this fork tracks
# upstream's tags via the `upstream` remote), so the very first push starts
# from a sane baseline instead of v0.1.
suggest_next() {
  local last="$1" major minor
  if [ -z "$last" ]; then
    last="$(git tag -l 'v*' 2>/dev/null | grep -E '^v[0-9]+\.[0-9]+$' | sort -V | tail -1 || true)"
    if [ -z "$last" ]; then
      printf 'v0.1'
      return 0
    fi
  fi
  IFS=. read -r major minor <<<"${last#v}"
  printf 'v%s.%s' "$major" "$((minor + 1))"
}

# Logs -> stderr; chosen version alone on stdout.
pick_version() {
  local pushed last suggest version input=""
  local -a tags=()

  mapfile -t tags < <(list_hub_tags | sort -V)
  if [ "${#tags[@]}" -gt 0 ]; then
    pushed="$(printf '%s ' "${tags[@]}" | sed 's/[[:space:]]*$//')"
    last="${tags[-1]}"
  else
    pushed=""
    last=""
  fi
  suggest="$(suggest_next "$last")"

  if [ -n "$pushed" ]; then
    info "Version tags on Docker Hub (${IMAGE}): ${pushed}"
    info "Most recent: ${last}"
  else
    info "No vX.Y tags found on Docker Hub yet (or offline/rate-limited)."
  fi

  # Explicit override skips the prompt.
  if [ -n "$CLI_VERSION" ]; then
    version="$CLI_VERSION"
    info "Using VERSION from command line: ${version}"
    is_version "$version" || die "VERSION must look like vX.Y (got: ${version})"
    printf '%s' "$version"
    return 0
  fi

  # Always ask in a real console (read the controlling TTY -- works under make).
  if [ -e /dev/tty ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
    info "Enter the version to tag & push (immutable snapshot alongside :production)."
    printf "Version to tag & push [%s]: " "$suggest" >/dev/tty
    input=""
    # Do not let a failed read abort the script (set -e).
    IFS= read -r input </dev/tty || true
    version="${input:-$suggest}"
  else
    die "No console TTY to ask for VERSION. Re-run in a terminal, or: make push VERSION=vX.Y"
  fi

  is_version "$version" || die "VERSION must look like vX.Y (got: ${version})"
  printf '%s' "$version"
}

is_multi_platform() {
  case "$PLATFORMS" in
    *,*) return 0 ;;
    *) return 1 ;;
  esac
}

# Idempotent: creates the builder only if it doesn't already exist.
ensure_buildx_builder() {
  docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1 \
    || docker buildx create --name "$BUILDER_NAME" --driver docker-container >/dev/null \
    || die "Failed to create buildx builder '${BUILDER_NAME}'"
  docker buildx use "$BUILDER_NAME"
  docker buildx inspect --bootstrap >/dev/null \
    || die "Failed to bootstrap buildx builder '${BUILDER_NAME}'"
}

# Confirms the pushed manifest actually contains every requested platform --
# catches a silent single-arch fallback from a slipped flag.
verify_platforms() {
  local tag="$1" out p
  out="$(docker buildx imagetools inspect "${IMAGE}:${tag}" 2>&1)" \
    || die "imagetools inspect failed for ${IMAGE}:${tag}"
  IFS=',' read -ra want <<<"$PLATFORMS"
  for p in "${want[@]}"; do
    printf '%s' "$out" | grep -qF "$p" \
      || die "${IMAGE}:${tag} is missing platform ${p} (manifest list incomplete)"
  done
}

build_and_push() {
  local version="$1" cache_date git_sha git_ref
  cache_date="$(date +%Y-%m-%d:%H:%M:%S)"
  git_sha="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  git_ref="$(git branch --show-current 2>/dev/null || echo unknown)"

  info "Build git ref/sha : ${git_ref}@${git_sha}"

  docker buildx build \
    --platform "$PLATFORMS" \
    --build-arg "CACHE_DATE=${cache_date}" \
    -t "${IMAGE}:production" -t "${IMAGE}:${version}" \
    -f "$DOCKERFILE" \
    --push \
    . \
    || die "buildx build failed for ${IMAGE}"
}

push_image() {
  local version="$1"

  info "Using version: ${version}"
  case "$(pwd -P)" in
    /mnt/*)
      info "WARNING: build path is under /mnt (WSL 9p). If context transfer hangs, copy to \$HOME and rebuild."
      ;;
  esac

  if is_multi_platform; then
    info "Multi-platform build requested (${PLATFORMS})."
    ensure_buildx_builder
  fi

  info "Building + pushing (platforms: ${PLATFORMS})..."
  build_and_push "$version"

  info "Verifying pushed manifest lists every requested platform..."
  verify_platforms production
  verify_platforms "$version"

  info "Pushed ${IMAGE}:production and ${IMAGE}:${version} (${PLATFORMS})."
  info "On the target host: docker run -p 80:80 -v a0_usr:/a0/usr ${IMAGE}:production"
}

self_test() {
  local fails=0 v=""

  command -v docker >/dev/null || { echo "FAIL: docker missing"; fails=$((fails + 1)); }
  docker info >/dev/null 2>&1 || { echo "FAIL: docker daemon"; fails=$((fails + 1)); }
  docker buildx version >/dev/null 2>&1 || { echo "FAIL: docker buildx not available"; fails=$((fails + 1)); }
  [ -f "${HOME}/.docker/config.json" ] || { echo "FAIL: no docker login config"; fails=$((fails + 1)); }
  grep -Eq '"auths"|"credsStore"|"credHelpers"' "${HOME}/.docker/config.json" 2>/dev/null \
    || { echo "FAIL: docker config has no credentials"; fails=$((fails + 1)); }

  list_hub_tags >/dev/null || { echo "FAIL: list_hub_tags exited non-zero"; fails=$((fails + 1)); }
  echo "hub_lookup_ok"

  v="$(suggest_next "v2.10")"
  [ "$v" = "v2.11" ] || { echo "FAIL: suggest_next v2.10 -> expected v2.11 got '$v'"; fails=$((fails + 1)); }
  echo "suggest_next_ok"

  v="$(CLI_VERSION=v9.9 pick_version </dev/null)"
  [ "$v" = "v9.9" ] || { echo "FAIL: CLI_VERSION expected v9.9 got '$v'"; fails=$((fails + 1)); }
  echo "cli_version_ok"

  if ( CLI_VERSION='9.9.9' pick_version </dev/null >/dev/null 2>&1 ); then
    echo "FAIL: accepted junk CLI version"
    fails=$((fails + 1))
  else
    echo "reject_junk_version_ok"
  fi

  [ -f "$DOCKERFILE" ] || { echo "FAIL: missing $DOCKERFILE"; fails=$((fails + 1)); }
  [ -f requirements.txt ] || { echo "FAIL: missing requirements.txt"; fails=$((fails + 1)); }
  echo "files_ok"

  if [ "$fails" -eq 0 ]; then
    echo "SELF_TEST_PASS"
    exit 0
  fi
  echo "SELF_TEST_FAIL count=$fails"
  exit 1
}

case "${1:-}" in
  --self-test)
    self_test
    ;;
  --suggest-version)
    # Non-interactive: print the next-minor suggestion to stdout and exit,
    # no docker/login required. For CI callers (e.g. Jenkins) that need the
    # version up front without a TTY -- feed it back in as CLI_VERSION to
    # skip the prompt: CLI_VERSION=$(... --suggest-version) bash "$0"
    last="$(list_hub_tags | sort -V | tail -1)"
    suggest_next "$last"
    ;;
  *)
    preflight
    push_image "$(pick_version)"
    ;;
esac
