#!/bin/sh
# Claude Code POSIX launcher: choose one interpreter with MCP installed.
# Never installs packages, downloads code, reads secrets or changes HOME.
set -eu
here=$(CDPATH= cd "${0%/*}" && pwd)
# The host-facing entry point is the facade (host-compatible tool schemas); it
# serves the same server. A caller may name another entry script, but it must
# stay inside this plugin directory — an absolute path from the host config is
# not a reason to exec arbitrary code.
server="${1:-$here/research_mcp_facade.py}"
case "$server" in
    "$here"/*.py) ;;
    *) printf '%s\n' 'MARKETOPS_MCP_ENTRY_MUST_BE_INSIDE_PLUGIN' >&2; exit 78 ;;
esac
[ -f "$server" ] || { printf '%s\n' 'MARKETOPS_MCP_ENTRY_MISSING' >&2; exit 78; }

usable() {
    [ -x "$1" ] && "$1" -c 'import mcp' >/dev/null 2>&1
}

# Explicit override is authoritative. An invalid override must not silently
# switch to an unrelated Python environment.
if [ -n "${MARKETOPS_RESEARCH_PYTHON:-}" ]; then
    case "$MARKETOPS_RESEARCH_PYTHON" in
        /*) ;;
        *) printf '%s\n' 'MARKETOPS_PYTHON_OVERRIDE_NOT_ABSOLUTE' >&2; exit 78 ;;
    esac
    if usable "$MARKETOPS_RESEARCH_PYTHON"; then
        exec "$MARKETOPS_RESEARCH_PYTHON" "$server"
    fi
    printf '%s\n' 'MARKETOPS_PYTHON_OVERRIDE_NOT_READY: MCP SDK missing or Python unavailable' >&2
    exit 78
fi

# A project-local venv is preferred over system Python. Installed Codex and
# WorkBuddy packages instead bind their own absolute venv interpreter.
for candidate in "$here/../.venv/bin/python" "$here/../../.venv/bin/python"; do
    if usable "$candidate"; then
        exec "$candidate" "$server"
    fi
done
for name in python3 python; do
    candidate=$(command -v "$name" 2>/dev/null || true)
    if [ -n "$candidate" ] && usable "$candidate"; then
        exec "$candidate" "$server"
    fi
done
printf '%s\n' 'MARKETOPS_MCP_INTERPRETER_UNAVAILABLE: configure a private Python venv with MCP and MARKETOPS_RESEARCH_PYTHON' >&2
exit 78
