#!/bin/bash
# Embed a GitHub QR code into this chart's Grafana dashboard config, before packaging/installing.
#
# Helm renders ConfigMaps from local files via `.Files.Get` at `helm install`/`template`
# time (client-side) — there is no in-cluster hook that can rewrite them afterwards, and the
# Grafana pod runs with runAsNonRoot/readOnlyRootFilesystem, so it cannot regenerate its own
# dashboard at runtime either. Run this script on the host BEFORE `helm install`/`upgrade`.
set -e

CHART_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APP_DIR="$(dirname "$CHART_DIR")"
QR_GENERATOR="$APP_DIR/src/grafana/generate_qr_code.py"
DASHBOARD="$CHART_DIR/config/grafana/dashboards/visualizer.json"

if [ ! -f "$QR_GENERATOR" ]; then
    echo "No QR code generator found for this chart; nothing to do."
    exit 0
fi

GIT_BRANCH="$(git -C "$CHART_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ -z "$GIT_BRANCH" ] || [ "$GIT_BRANCH" == "HEAD" ]; then
    GIT_BRANCH="$(git -C "$CHART_DIR" describe --tags --exact-match 2>/dev/null)"
fi
GIT_BRANCH="${GIT_BRANCH:-main}"

# Fall back to "main" if the branch/tag isn't pushed to GitHub yet, so the QR
# code never links to a 404 for local-only/unpublished work.
CANONICAL_REPO_URL="https://github.com/open-edge-platform/edge-ai-suites.git"
if [ "$GIT_BRANCH" != "main" ] && ! timeout 10 git ls-remote --exit-code --heads --tags "$CANONICAL_REPO_URL" "$GIT_BRANCH" >/dev/null 2>&1; then
    echo "Branch/tag '$GIT_BRANCH' not found on $CANONICAL_REPO_URL; falling back to 'main'."
    GIT_BRANCH="main"
fi

echo "Embedding GitHub QR code (branch/tag: $GIT_BRANCH) into $DASHBOARD..."
docker run --rm \
  -e http_proxy -e https_proxy -e no_proxy \
  -v "$APP_DIR:/workspace" -w /workspace \
  python:3.12-slim bash -c \
  "pip install --quiet --no-input qrcode pillow && python3 'src/grafana/generate_qr_code.py' --branch '$GIT_BRANCH' --dashboard 'helm-chart/config/grafana/dashboards/visualizer.json'" || \
  echo "Warning: failed to embed GitHub QR code; dashboard will keep its last-committed QR/link."
