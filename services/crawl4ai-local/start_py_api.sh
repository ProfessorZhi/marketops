#!/usr/bin/env bash
# Start the Crawl4AI local REST API from the official Python package.
#
# This is the fallback path, used while the Docker image could not be pulled.
# It binds loopback only and reads the same token file the compose file uses,
# so the smoke test does not need to know which deployment answered.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${HERE}/.env"
ENV_PY="${CRAWL4AI_ENV_PY:-/data/home/fjd/marketops-lab/envs/crawl4ai/bin/python}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE (CRAWL4AI_API_TOKEN)" >&2
  exit 2
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/data/home/fjd/marketops-lab/ms-playwright}"
export CRAWL4AI_NO_CLOUD_NOTICE=1
export CRAWL4AI_HOST="${CRAWL4AI_HOST:-127.0.0.1}"
export CRAWL4AI_PORT="${CRAWL4AI_PORT:-11235}"

exec "$ENV_PY" "$HERE/py_api.py"
