#!/bin/sh
# MarketOps Linux installer.
#
# The default run copies the package, builds a private venv and prepares the
# selected local clients. It never changes another application's global
# registration and never prompts when there is no terminal to answer: the two
# consent-requiring steps below stay off until the customer asks for them.
set -eu

package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

usage() {
    printf '%s\n' \
        'Usage: install.sh [DESTINATION] [CLIENT] [OPTIONS]' \
        '' \
        '  DESTINATION        new or previously installed directory (default: prompt)' \
        '  CLIENT             claude, codex, workbuddy, both (default), all' \
        '  --register-codex   also add this install to the global Codex CLI' \
        '                     (requires --client codex, both or all)' \
        '  --no-register-codex  never touch the global Codex CLI' \
        '  -h, --help         show this help'
}

fail() {
    printf 'INSTALL_BLOCKED: %s: %s\n' "$1" "$2" >&2
    exit 2
}

destination=''
client='both'
positional=0
register_codex='ask'

while [ "$#" -gt 0 ]; do
    case "$1" in
        --register-codex) register_codex='yes' ;;
        --no-register-codex) register_codex='no' ;;
        --client)
            [ "$#" -ge 2 ] || fail INSTALL_CONFIGURATION_INVALID 'Use --client claude, codex, workbuddy, both or all.'
            client=$2
            shift
            ;;
        --client=*) client=${1#--client=} ;;
        --destination)
            [ "$#" -ge 2 ] || fail INSTALL_CONFIGURATION_INVALID 'Use --destination with an installation directory.'
            destination=$2
            shift
            ;;
        --destination=*) destination=${1#--destination=} ;;
        -h|--help) usage; exit 0 ;;
        --)
            shift
            while [ "$#" -gt 0 ]; do
                positional=$((positional + 1))
                if [ "$positional" -eq 1 ]; then destination=$1
                elif [ "$positional" -eq 2 ]; then client=$1
                else fail INSTALL_CONFIGURATION_INVALID 'Use at most one destination and one client.'
                fi
                shift
            done
            continue
            ;;
        -*)
            fail INSTALL_CONFIGURATION_INVALID "Unknown option $1. Run install.sh --help."
            ;;
        *)
            positional=$((positional + 1))
            if [ "$positional" -eq 1 ]; then
                destination=$1
            elif [ "$positional" -eq 2 ]; then
                client=$1
            else
                fail INSTALL_CONFIGURATION_INVALID 'Use at most one destination and one client.'
            fi
            ;;
    esac
    shift
done

case "$client" in
    claude|codex|workbuddy|both|all) ;;
    *) fail UNSUPPORTED_SETUP_CLIENT 'Use --client claude, codex, workbuddy, both or all.' ;;
esac
case "$client" in
    codex|both|all) ;;
    *)
        if [ "$register_codex" = 'yes' ]; then
            fail CODEX_REGISTRATION_REQUIRES_CODEX_CLIENT 'Use --client codex, both or all together with --register-codex.'
        fi
        ;;
esac

interactive='no'
if [ -t 0 ]; then
    interactive='yes'
fi

if [ -z "$destination" ]; then
    if [ "$interactive" != 'yes' ]; then
        fail INSTALL_DESTINATION_REQUIRED 'Run install.sh with an installation directory, for example: install.sh ~/MarketOps --client both'
    fi
    printf 'Choose an installation directory (separate from research data): '
    IFS= read -r destination || destination=''
    if [ -z "$destination" ]; then
        fail INSTALL_DESTINATION_REQUIRED 'Run install.sh with an installation directory, for example: install.sh ~/MarketOps --client both'
    fi
fi

find_python() {
    candidate=$1
    [ -n "$candidate" ] || return 1
    if "$candidate" -c 'import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize("P")==8' >/dev/null 2>&1; then
        printf '%s\n' "$candidate"
        return 0
    fi
    return 1
}

python_exe=''
for candidate in python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if resolved=$(find_python "$(command -v "$candidate")"); then
            python_exe=$resolved
            break
        fi
    fi
done
if [ -z "$python_exe" ] && command -v uv >/dev/null 2>&1; then
    # uv keeps interpreters outside PATH; `uv python find` is the supported
    # way to reach one without guessing a layout.
    if candidate=$(uv python find 3.12 2>/dev/null) && [ -n "$candidate" ]; then
        if resolved=$(find_python "$candidate"); then
            python_exe=$resolved
        fi
    fi
fi
if [ -z "$python_exe" ]; then
    fail PYTHON_312_64BIT_REQUIRED 'Install 64-bit Python 3.12 (python.org) or run "uv python install 3.12", then run install.sh again.'
fi

# Resolve exactly the way plan() does, so the receipt, the venv path and the
# commands printed to the customer all name the same absolute directory.
if ! destination=$("$python_exe" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$destination" 2>/dev/null) || [ -z "$destination" ]; then
    fail DESTINATION_REQUIRED 'Choose a valid installation directory, for example: install.sh ~/MarketOps --client both'
fi

register_args=''
case "$client" in codex|both|all)
    if [ "$register_codex" = 'ask' ]; then
        if [ "$interactive" = 'yes' ]; then
            printf 'Add this install to your global Codex CLI now? [y/N] '
            answer=''
            IFS= read -r answer || answer=''
            case "$answer" in
                y|Y|yes|YES) register_codex='yes' ;;
                *) register_codex='no' ;;
            esac
        else
            register_codex='no'
        fi
    fi
    if [ "$register_codex" = 'yes' ]; then
        register_args='--register-codex'
    else
        printf 'NOTICE: global Codex registration skipped. To add it later, run: codex plugin marketplace add "%s"\n' "$destination" >&2
    fi
    ;;
esac

log=$(mktemp "${TMPDIR:-/tmp}/marketops-install.XXXXXX") || fail INSTALL_STEP_FAILED 'Re-run install.sh: the temporary log could not be created.'
trap 'rm -f "$log"' EXIT HUP INT TERM

# shellcheck disable=SC2086
if "$python_exe" "$package_dir/scripts/setup_clients.py" \
        --destination "$destination" --client "$client" \
        --apply --bootstrap-runtime --resume $register_args >"$log" 2>&1; then
    cat "$log"
else
    status=$?
    # Print only our own one-line refusal. Child output (pip, providers, a
    # broken traceback) is never echoed: it can carry tokens or local paths.
    blocked=$(grep -E '^INSTALL_BLOCKED: [A-Z][A-Z0-9_]{2,80}: ' "$log" | tail -n 1 || true)
    if [ -n "$blocked" ]; then
        printf '%s\n' "$blocked" >&2
    else
        printf 'INSTALL_BLOCKED: INSTALL_STEP_FAILED: Re-run install.sh: it resumes the same destination. If it stops again, open INSTALL_STATE.json beside the destination.\n' >&2
    fi
    exit "$status"
fi

venv_python="$destination/.venv/bin/python"
if [ ! -x "$venv_python" ]; then
    fail INSTALL_STEP_FAILED 'Re-run install.sh: the private runtime was not created. If it stops again, open INSTALL_STATE.json beside the destination.'
fi

customer_setup="$destination/plugins/marketops-research-assistant/scripts/customer_setup.py"
if [ "$interactive" = 'yes' ]; then
    if ! "$venv_python" "$customer_setup"; then
        fail CUSTOMER_SETUP_INCOMPLETE 'Reopen START_HERE.md in the install directory and rerun the printed local account setup command.'
    fi
else
    printf 'NOTICE: no terminal; local account setup was not started. Run: "%s" "%s"\n' "$venv_python" "$customer_setup" >&2
fi
printf 'Open %s/START_HERE.md for host verification and the first research task.\n' "$destination"
