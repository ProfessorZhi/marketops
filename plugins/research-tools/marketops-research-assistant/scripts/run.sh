#!/bin/sh
# Start the MarketOps MCP server with the Python the installer prepared.
here=$(CDPATH= cd "${0%/*}" && pwd)
home="${MARKETOPS_HOME:-$HOME/.marketops}"
for py in "${MARKETOPS_PYTHON:-}" "$home/venv/bin/python" python3; do
    [ -n "$py" ] || continue
    # only the MCP library is required: without Crawl4AI pages are read by TinyFish, DeepSeek or
    # plain HTTP (the plugin degrades, it does not stop)
    if "$py" -c 'import mcp, httpx' >/dev/null 2>&1; then
        exec "$py" "$here/server.py"
    fi
done
echo "MarketOps is not installed on this computer yet: run install.sh from the plugin folder (or the release zip), then restart Claude Code." >&2
exit 1
