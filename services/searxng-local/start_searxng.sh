#!/usr/bin/env bash
# Start the local SearXNG instance.
#
# SearXNG is a meta search: it owns no index, so "started" is not the same as
# "usable". Check engine health with services/searxng-local/acceptance.py, not
# with the process table.
set -euo pipefail

SEARXNG_SRC="${SEARXNG_SRC:-/data/home/fjd/marketops-lab/src/searxng}"
SEARXNG_VENV="${SEARXNG_VENV:-/data/home/fjd/marketops-lab/envs/searxng}"
SEARXNG_PORT="${SEARXNG_PORT:-8888}"

if [[ ! -f "$SEARXNG_SRC/searx/webapp.py" ]]; then
  echo "SearXNG source not found at $SEARXNG_SRC" >&2
  echo "clone it with: git clone --depth 1 https://github.com/searxng/searxng.git $SEARXNG_SRC" >&2
  exit 1
fi
if [[ ! -f "$SEARXNG_SRC/settings.yml" ]]; then
  echo "settings.yml missing; copy searx/settings.yml and set a secret_key" >&2
  exit 1
fi

cd "$SEARXNG_SRC"
export PYTHONPATH="$SEARXNG_SRC"
export SEARXNG_SETTINGS_PATH="$SEARXNG_SRC/settings.yml"
exec "$SEARXNG_VENV/bin/python" -m searx.webapp
