#!/bin/sh
set -eu
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_exe=''
for candidate in python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize("P")==8' 2>/dev/null; then
        python_exe=$(command -v "$candidate")
        break
    fi
done
if [ -z "$python_exe" ]; then
    echo 'Python 3.12 x64 is required. Install it from your Linux distribution or https://www.python.org/downloads/ and rerun this installer.' >&2
    exit 2
fi
destination=${1:-}
client=${2:-both}
if [ -z "$destination" ]; then
    printf 'Choose an absolute installation directory (separate from research data): '
    IFS= read -r destination
fi
set -- "$package_dir/scripts/setup_clients.py" --destination "$destination" --client "$client" --apply --bootstrap-runtime --resume
case "$client" in codex|both|all) if command -v codex >/dev/null 2>&1; then set -- "$@" --register-codex; fi;; esac
"$python_exe" "$@"
"$destination/.venv/bin/python" "$destination/plugins/marketops-research-assistant/scripts/customer_setup.py"
printf 'Open %s/START_HERE.md for host verification and the first research task.\n' "$destination"
