#!/usr/bin/env bash
# One-time switch from recorded mode to live models.
#   NEBIUS_API_KEY=... TAVILY_API_KEY=... scripts/go_live.sh
# Never paste keys into chat or commit them; export them in your shell first.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${NEBIUS_API_KEY:?export NEBIUS_API_KEY first}"
modal secret create homecoming NEBIUS_API_KEY="$NEBIUS_API_KEY" TAVILY_API_KEY="${TAVILY_API_KEY:-}" --force
modal deploy app.py
echo "--- probe: can Super VL read the sample kitchens?"
python scripts/probe.py
echo "--- live health:"
curl -s https://m-simakhov--homecoming-web.us-east.modal.run/api/health; echo
