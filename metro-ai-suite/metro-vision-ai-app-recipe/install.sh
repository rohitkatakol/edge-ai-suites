#!/bin/bash

# Path to the .env file
ENV_FILE="./.env"

# Check if .env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found."
    exit 1
fi

# Check and set SAMPLE_APP from first argument
SAMPLE_APP_ARG="$1"
if [ -z "$SAMPLE_APP_ARG" ]; then
    echo "Error: First argument (SAMPLE_APP) is required."
    echo "Usage: $0 <smart-parking|loitering-detection|smart-intersection> [HOST_IP]"
    exit 1
fi

case "$SAMPLE_APP_ARG" in
    "smart-parking"|"loitering-detection"|"smart-intersection")
        # Update SAMPLE_APP in .env file
        if grep -q "^SAMPLE_APP=" "$ENV_FILE"; then
            sed -i "s/^SAMPLE_APP=.*/SAMPLE_APP=$SAMPLE_APP_ARG/" "$ENV_FILE"
        else
            echo "SAMPLE_APP=$SAMPLE_APP_ARG" >> "$ENV_FILE"
        fi
        ;;
    *)
        echo "Error: Invalid SAMPLE_APP value '$SAMPLE_APP_ARG'. Must be one of: smart-parking, loitering-detection, smart-intersection."
        exit 1
        ;;
esac

# Update the HOST_IP in .env file
# Check if HOST_IP is provided as second argument, otherwise use hostname -I
HOST_IP_ARG="$2"
if [ -z "$HOST_IP_ARG" ]; then
    HOST_IP=$(hostname -I | cut -f1 -d' ')
else
    # Validate IP format (basic validation for IPv4)
    if [[ ! $HOST_IP_ARG =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Warning: Invalid IP format. Using hostname -I instead."
        HOST_IP=$(hostname -I | cut -f1 -d' ')
    else
        HOST_IP=$HOST_IP_ARG
    fi
fi

echo "Configuring application to use $HOST_IP"
if grep -q "^HOST_IP=" "$ENV_FILE"; then
    # Replace existing HOST_IP line
    sed -i "s/^HOST_IP=.*/HOST_IP=$HOST_IP/" "$ENV_FILE"
else
    # Add HOST_IP if it doesn't exist
    echo "HOST_IP=$HOST_IP" >> "$ENV_FILE"
fi

# Detect the git branch/tag this repo is checked out on, so the Grafana QR code
# can read it from .env without needing git or .git mounted into any container.
GIT_BRANCH="$(git -C "$(dirname "$(readlink -f "$0")")" rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ -z "$GIT_BRANCH" ] || [ "$GIT_BRANCH" == "HEAD" ]; then
    GIT_BRANCH="$(git -C "$(dirname "$(readlink -f "$0")")" describe --tags --exact-match 2>/dev/null)"
fi
GIT_BRANCH="${GIT_BRANCH:-main}"

# Fall back to "main" if the branch/tag isn't pushed to GitHub yet, so the QR
# code never links to a 404 for local-only/unpublished work.
CANONICAL_REPO_URL="https://github.com/open-edge-platform/edge-ai-suites.git"
if [ "$GIT_BRANCH" != "main" ] && ! timeout 10 git ls-remote --exit-code --heads --tags "$CANONICAL_REPO_URL" "$GIT_BRANCH" >/dev/null 2>&1; then
    echo "Branch/tag '$GIT_BRANCH' not found on $CANONICAL_REPO_URL; falling back to 'main'."
    GIT_BRANCH="main"
fi

echo "Detected git branch/tag: $GIT_BRANCH"
# Branch names commonly contain '/', which breaks a '/'-delimited sed substitution;
# use awk so the value is updated in place regardless of its contents.
awk -v val="$GIT_BRANCH" '
  BEGIN { done = 0 }
  /^GIT_BRANCH=/ { print "GIT_BRANCH=" val; done = 1; next }
  { print }
  END { if (!done) print "GIT_BRANCH=" val }
' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"

# Extract SAMPLE_APP variable from .env file
SAMPLE_APP=$(grep -E "^SAMPLE_APP=" "$ENV_FILE" | cut -d '=' -f2 | tr -d '"' | tr -d "'")

# Embed a GitHub QR code into the sample app's Grafana dashboard, if it ships a
# generator (e.g. loitering-detection). Runs on the host/build machine, before
# any container starts, so Grafana never needs root or extra packages at runtime.
QR_GENERATOR="$SAMPLE_APP/src/grafana/generate_qr_code.py"
if [ -f "$QR_GENERATOR" ]; then
    echo "Embedding GitHub QR code into $SAMPLE_APP's Grafana dashboard..."
    docker run --rm \
      -e http_proxy -e https_proxy -e no_proxy \
      -v "$(pwd):/workspace" -w /workspace \
      python:3.12-slim bash -c \
      "pip install --quiet --no-input qrcode pillow && python3 '$QR_GENERATOR' --branch '$GIT_BRANCH'" || \
      echo "Warning: failed to embed GitHub QR code; dashboard will keep its last-committed QR/link."
fi

# Bring down the application before updating docker compose file
if docker compose ps >/dev/null 2>&1; then
    echo "Bringing down any running containers..."
    docker compose down -v --remove-orphans
fi

# Copy appropriate docker-compose file
if [ "$SAMPLE_APP" = "smart-intersection" ]; then
    cp compose-scenescape.yml docker-compose.yml
else
    cp compose-without-scenescape.yml docker-compose.yml
fi

# Check if the directory exists
if [ ! -d "$SAMPLE_APP" ]; then
    echo "Error: Directory $SAMPLE_APP does not exist."
    exit 1
fi

# Navigate to the directory and run the install script
echo "Navigating to $SAMPLE_APP directory and running install script..."
cd "$SAMPLE_APP" || exit 1

if [ -f "./install.sh" ]; then
    chmod +x ./install.sh
    ./install.sh $HOST_IP
else
    echo "Error: install.sh not found in $SAMPLE_APP directory."
    exit 1
fi