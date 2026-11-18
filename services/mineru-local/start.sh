#!/usr/bin/env bash
# Start the MinerU local doclib with its TCP API bound to loopback.
#
# Everything stateful lives under /data, not $HOME: the model bundle alone is
# several GB and the host policy forbids large caches in HOME.
#
# The env vars below are not optional decoration. Without
# MINERU_DOCLIB_TCP_ENABLED the service only exposes a Unix socket and the
# benchmark harness has nothing to call.
set -euo pipefail

MINERU_HOME_DIR="${MINERU_HOME_DIR:-/data/home/fjd/marketops-lab/mineru-home}"
MINERU_ENV="${MINERU_ENV:-/data/home/fjd/marketops-lab/envs/mineru}"
API_HOST="${MINERU_API_HOST:-127.0.0.1}"
API_PORT="${MINERU_API_PORT:-18000}"

export MINERU_HOME="$MINERU_HOME_DIR"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/data/home/fjd/marketops-lab/cache}"
export HF_HOME="${HF_HOME:-/data/home/fjd/marketops-lab/hf}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export MINERU_DOCLIB_TCP_ENABLED=true
export MINERU_DOCLIB_TCP_HOST="$API_HOST"
export MINERU_DOCLIB_TCP_PORT="$API_PORT"

"$MINERU_ENV/bin/mineru" server start
sleep 2
"$MINERU_ENV/bin/mineru" server status --json
