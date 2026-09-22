#!/bin/sh
set -e

GIT_DIR=/edge-ai-suites-git
CANONICAL_REPO_URL="https://github.com/open-edge-platform/edge-ai-suites.git"

# No custom image is built for this; install the few extra packages needed on every start.
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import qrcode, PIL" >/dev/null 2>&1; then
  apk add --no-cache python3 py3-qrcode py3-pillow su-exec git >/dev/null 2>&1 || \
    echo "Warning: failed to install QR code dependencies; skipping QR code generation."
fi

# Bind-mounted repo is owned by the host user; git refuses to touch it otherwise.
git config --global --add safe.directory "$GIT_DIR" 2>/dev/null || true

# Only apps that ship a generator (e.g. loitering-detection) get a QR panel; others no-op.
if command -v python3 >/dev/null 2>&1 && [ -f /grafana-src/generate_qr_code.py ]; then
  # An explicit GIT_BRANCH always wins; otherwise auto-detect from the mounted .git dir.
  if [ -z "$GIT_BRANCH" ] && [ -d "$GIT_DIR" ]; then
    GIT_BRANCH="$(git --git-dir="$GIT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    if [ -z "$GIT_BRANCH" ] || [ "$GIT_BRANCH" = "HEAD" ]; then
      GIT_BRANCH="$(git --git-dir="$GIT_DIR" describe --tags --exact-match 2>/dev/null)"
    fi
  fi
  GIT_BRANCH="${GIT_BRANCH:-main}"

  # Fall back to "main" if the branch/tag isn't pushed to GitHub yet, so the QR
  # code never links to a 404 for local-only/unpublished work.
  if [ "$GIT_BRANCH" != "main" ] && ! timeout 10 git ls-remote --exit-code --heads --tags "$CANONICAL_REPO_URL" "$GIT_BRANCH" >/dev/null 2>&1; then
    echo "Branch/tag '$GIT_BRANCH' not found on $CANONICAL_REPO_URL; falling back to 'main'."
    GIT_BRANCH="main"
  fi

  python3 /grafana-src/generate_qr_code.py --branch "$GIT_BRANCH" || \
    echo "Warning: failed to update GitHub QR code in Grafana dashboard."
fi

# Drop back to the non-root grafana user for the actual server process.
exec su-exec grafana /run.sh "$@"
