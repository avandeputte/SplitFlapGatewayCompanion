#!/usr/bin/env bash
#
# SplitFlap Companion launcher (macOS).
#
#   • Double-click this file in Finder to start the app, OR
#   • run it from a terminal:
#        ./run-companion.command                    # use the default gateway
#        ./run-companion.command http://192.168.1.5 # override the gateway URL
#        GATEWAY_URL=http://192.168.1.5 ./run-companion.command
#
# On first run it creates a Python virtual-env and installs dependencies; after
# that it just starts. Press Ctrl-C (or close the window) to stop.

set -euo pipefail

# --- edit this if your gateway lives at a different address --------------------
DEFAULT_GATEWAY_URL="http://192.168.2.235"
# ------------------------------------------------------------------------------

# The companion REQUIRES a gateway URL and refuses to start without one.
# Priority: 1st argument  >  existing GATEWAY_URL env var  >  the default above.
export GATEWAY_URL="${1:-${GATEWAY_URL:-$DEFAULT_GATEWAY_URL}}"

# Work from the backend directory next to this script, wherever it was launched.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/backend"

# First run: build the virtual-env and install runtime dependencies.
if [ ! -x .venv/bin/python ]; then
  echo "First-time setup: creating Python environment and installing dependencies…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
  echo "Setup complete."
fi

echo
echo "  SplitFlap Companion  →  gateway  $GATEWAY_URL"
echo "  Open  http://localhost:8000   (also reachable at this Mac's LAN IP)"
echo "  Press Ctrl-C to stop."
echo

# Run straight from the venv's python (no 'activate' needed). '-m app' binds
# 0.0.0.0:8000 by default so it's reachable on the LAN.
exec ./.venv/bin/python -m app
