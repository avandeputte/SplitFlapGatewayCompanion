#!/usr/bin/env bash
#
# publish-image.sh — build a multi-arch Docker image of the SplitFlap Gateway
# Companion and push it to a container registry (GitHub Container Registry by
# default). One tag serves every host: the registry hands each machine the right
# architecture automatically.
#
#   • linux/arm64  → Apple Silicon Macs, Raspberry Pi (64-bit OS)
#   • linux/amd64  → x86 Linux, and Windows/macOS-Intel via Docker Desktop/WSL2
#                    (those run Linux containers — no Windows-native image needed)
#
# ---------------------------------------------------------------------------
# Quick start
# ---------------------------------------------------------------------------
#   export GHCR_OWNER=your-github-username         # required (lowercased for you)
#   export GHCR_TOKEN=ghp_xxx                       # a PAT with write:packages
#   ./scripts/publish-image.sh                      # build amd64+arm64, push
#
# If the `gh` CLI is logged in, GHCR_TOKEN is optional — the script reads a token
# from `gh auth token`. If you are already `docker login`-ed to the registry,
# no token is needed at all.
#
# ---------------------------------------------------------------------------
# Common invocations
# ---------------------------------------------------------------------------
#   ./scripts/publish-image.sh                      # version + latest, both arches
#   ./scripts/publish-image.sh --local             # native arch only, load locally
#                                                     (for a quick test build; no push)
#   ./scripts/publish-image.sh -p linux/amd64,linux/arm64,linux/arm/v7
#                                                     # add 32-bit Raspberry Pi OS
#   ./scripts/publish-image.sh --no-latest         # tag only :<version>
#   ./scripts/publish-image.sh --install-emulators # one-time QEMU setup (Linux CI)
#
# ---------------------------------------------------------------------------
# Configuration (environment variables — all overridable)
# ---------------------------------------------------------------------------
#   GHCR_OWNER    GitHub user/org that owns the package        (REQUIRED)
#   GHCR_TOKEN    PAT with `write:packages` scope, for login   (or use `gh`)
#   REGISTRY      registry host                 (default: ghcr.io)
#   IMAGE_NAME    repository/name               (default: splitflap-gateway-companion)
#   GHCR_REPO     source GitHub repo, for the image link label (default: SplitFlapGatewayCompanion)
#   PLATFORMS     comma-separated arch list     (default: linux/amd64,linux/arm64)
#
set -euo pipefail

# --- locate the project root (this script lives in <root>/scripts) ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- defaults ---------------------------------------------------------------
REGISTRY="${REGISTRY:-ghcr.io}"
IMAGE_NAME="${IMAGE_NAME:-splitflap-gateway-companion}"
GHCR_REPO="${GHCR_REPO:-SplitFlapGatewayCompanion}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
BUILDER_NAME="splitflap-companion-builder"

PUSH=1                 # push to the registry (default). --local flips to load-only.
TAG_LATEST=1           # also tag :latest
INSTALL_EMULATORS=0    # register QEMU binfmt handlers, then exit
EXTRA_TAGS=()

# --- colours (only when attached to a TTY) ----------------------------------
if [ -t 1 ]; then C_B=$'\033[1m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_0=$'\033[0m'
else C_B=""; C_G=""; C_Y=""; C_R=""; C_0=""; fi
info()  { printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }
ok()    { printf '%s✓%s %s\n'  "$C_G" "$C_0" "$*"; }
warn()  { printf '%s!%s %s\n'  "$C_Y" "$C_0" "$*" >&2; }
die()   { printf '%s✗ %s%s\n'  "$C_R" "$*" "$C_0" >&2; exit 1; }

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//; /^set -euo/d'; }

# --- parse args -------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    -l|--local)            PUSH=0 ;;
    --no-latest)           TAG_LATEST=0 ;;
    -p|--platforms)        PLATFORMS="${2:?--platforms needs a value}"; shift ;;
    -t|--tag)              EXTRA_TAGS+=("${2:?--tag needs a value}"); shift ;;
    --install-emulators)   INSTALL_EMULATORS=1 ;;
    -h|--help)             usage; exit 0 ;;
    *)                     die "unknown option: $1  (try --help)" ;;
  esac
  shift
done

# --- one-time QEMU setup (multi-arch emulation on Linux hosts / CI) ----------
# Docker Desktop (macOS/Windows) already ships these handlers, so this is only
# needed on a bare Linux host that will cross-build.
if [ "$INSTALL_EMULATORS" -eq 1 ]; then
  info "Installing QEMU binfmt handlers (requires privileged Docker)…"
  docker run --privileged --rm tonistiigi/binfmt --install all
  ok "Emulators installed. Re-run without --install-emulators to build."
  exit 0
fi

# --- preflight --------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker not found on PATH."
docker buildx version >/dev/null 2>&1 || die "docker buildx not available (update Docker / Docker Desktop)."
docker info >/dev/null 2>&1 || die "the Docker daemon isn't running — start Docker Desktop and retry."
[ -f "$ROOT_DIR/Dockerfile" ] || die "no Dockerfile at $ROOT_DIR — run this from the repo."

# Registries require lowercase repository paths; GitHub usernames may not be.
lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

OWNER="${GHCR_OWNER:-}"
# Fall back to the GitHub owner in the git remote, if one is configured.
if [ -z "$OWNER" ]; then
  remote="$(git -C "$ROOT_DIR" remote get-url origin 2>/dev/null || true)"
  case "$remote" in
    *github.com[:/]*) OWNER="$(printf '%s' "$remote" | sed -E 's#.*github.com[:/]([^/]+)/.*#\1#')" ;;
  esac
fi
[ -n "$OWNER" ] || die "set GHCR_OWNER to your GitHub username/org, e.g.  export GHCR_OWNER=alex-vdp"
OWNER="$(lc "$OWNER")"
IMAGE_NAME="$(lc "$IMAGE_NAME")"

VERSION="$(tr -d ' \t\n\r' < "$ROOT_DIR/VERSION" 2>/dev/null || true)"
[ -n "$VERSION" ] || die "VERSION file is empty or missing."
GIT_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
# Reproducible-ish build timestamp (UTC). SOURCE_DATE_EPOCH lets CI pin it.
# GNU date wants `-d @epoch`; BSD/macOS date wants `-r epoch` — try both.
if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
  CREATED="$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
          || date -u -r  "$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
          || date -u +%Y-%m-%dT%H:%M:%SZ)"
else
  CREATED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

IMAGE="$REGISTRY/$OWNER/$IMAGE_NAME"

# --- assemble tags ----------------------------------------------------------
TAG_ARGS=(-t "$IMAGE:$VERSION")
[ "$TAG_LATEST" -eq 1 ] && TAG_ARGS+=(-t "$IMAGE:latest")
for t in "${EXTRA_TAGS[@]:-}"; do [ -n "$t" ] && TAG_ARGS+=(-t "$IMAGE:$t"); done

info "Image      : ${C_B}$IMAGE${C_0}"
info "Version    : $VERSION   (git $GIT_SHA)"
if [ "$PUSH" -eq 1 ]; then
  info "Platforms  : $PLATFORMS"
  info "Action     : build + ${C_B}push${C_0}"
else
  NATIVE="linux/$(docker version -f '{{.Server.Arch}}' 2>/dev/null || echo amd64)"
  PLATFORMS="$NATIVE"
  info "Platforms  : $PLATFORMS   (local mode: native arch only)"
  info "Action     : build + ${C_B}load into local Docker${C_0} (no push)"
fi

# --- registry login (only when pushing) -------------------------------------
if [ "$PUSH" -eq 1 ]; then
  TOKEN="${GHCR_TOKEN:-${GITHUB_TOKEN:-${CR_PAT:-}}}"
  if [ -z "$TOKEN" ] && command -v gh >/dev/null 2>&1; then
    TOKEN="$(gh auth token 2>/dev/null || true)"
    [ -n "$TOKEN" ] && info "Using a token from the gh CLI for $REGISTRY login."
  fi
  if [ -n "$TOKEN" ]; then
    info "Logging in to $REGISTRY as $OWNER…"
    printf '%s' "$TOKEN" | docker login "$REGISTRY" -u "$OWNER" --password-stdin >/dev/null \
      || die "docker login failed — check the token has the 'write:packages' scope."
    ok "Logged in."
  else
    warn "No token found (GHCR_TOKEN / GITHUB_TOKEN / gh). Assuming you are already"
    warn "docker-login-ed to $REGISTRY; the push will fail otherwise."
  fi
fi

# --- ensure a docker-container buildx builder (needed for multi-arch) -------
if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
  info "Creating buildx builder '$BUILDER_NAME'…"
  docker buildx create --name "$BUILDER_NAME" --driver docker-container >/dev/null
fi
docker buildx use "$BUILDER_NAME"
docker buildx inspect --bootstrap >/dev/null

# --- build ------------------------------------------------------------------
OUTPUT=(--push); [ "$PUSH" -eq 0 ] && OUTPUT=(--load)

info "Building…"
docker buildx build \
  --builder "$BUILDER_NAME" \
  --platform "$PLATFORMS" \
  "${TAG_ARGS[@]}" \
  --label "org.opencontainers.image.title=SplitFlap Gateway Companion" \
  --label "org.opencontainers.image.source=https://github.com/$OWNER/$GHCR_REPO" \
  --label "org.opencontainers.image.version=$VERSION" \
  --label "org.opencontainers.image.revision=$GIT_SHA" \
  --label "org.opencontainers.image.created=$CREATED" \
  --label "org.opencontainers.image.licenses=CC-BY-NC-SA-4.0" \
  "${OUTPUT[@]}" \
  "$ROOT_DIR"

echo
if [ "$PUSH" -eq 1 ]; then
  ok "Pushed ${C_B}$IMAGE:$VERSION${C_0}${TAG_LATEST:+ (+ :latest)} for [$PLATFORMS]"
  echo
  echo "Pull it anywhere with:"
  echo "    docker pull $IMAGE:$VERSION"
  echo
  echo "First push is private. Make it public (optional) at:"
  echo "    https://github.com/users/$OWNER/packages/container/$IMAGE_NAME/settings"
else
  ok "Loaded ${C_B}$IMAGE:$VERSION${C_0} into local Docker for [$PLATFORMS]"
  echo "    docker run --rm -e GATEWAY_URL=http://<gateway-ip> -p 8000:8000 $IMAGE:$VERSION"
fi
