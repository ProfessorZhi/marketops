#!/usr/bin/env bash
# Run the independent Wave-1 quality gate and emit machine-readable evidence.
#
# Why a script and not a bare pytest line:
#   * checklist §G-5 — the `pytest` on PATH on this host is not a real install;
#     the interpreter must be named explicitly and recorded.
#   * checklist §G-6 — pytest's final count line does not survive a redirect on
#     this host, so counts are taken from --junitxml instead.
#   * every run must be reproducible from the recorded SHA, so the SHA is written
#     beside the evidence rather than quoted in a report.
#
# Usage: ./run_gate.sh [output-dir]
#   INTERPRETER  override the Python interpreter (default: the gate venv below)
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERPRETER="${INTERPRETER:-/home/operator/artifacts/marketops-capgateway-independent-qa-20261017/venv/bin/python}"
OUT="${1:-/home/operator/artifacts/marketops-capgateway-independent-qa-20261017/evidence}"

if [ ! -x "$INTERPRETER" ]; then
  echo "no interpreter at $INTERPRETER" >&2
  exit 2
fi

mkdir -p "$OUT"
SHA="$(git -C "$HERE" rev-parse HEAD)"
BRANCH="$(git -C "$HERE" rev-parse --abbrev-ref HEAD)"
{
  echo "sha=$SHA"
  echo "branch=$BRANCH"
  echo "interpreter=$INTERPRETER"
  echo "python=$("$INTERPRETER" -V 2>&1)"
  echo "pytest=$("$INTERPRETER" -c 'import pytest;print(pytest.__version__)' 2>&1)"
  echo "started_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT/run-context.txt"

"$INTERPRETER" -m pytest "$HERE" \
  -p no:cacheprovider -q --tb=short \
  --junitxml="$OUT/qa-gate.xml" \
  > "$OUT/qa-gate.log" 2>&1
RC=$?

"$INTERPRETER" - "$OUT/qa-gate.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
suite = root if root.tag == "testsuite" else root.find("testsuite")
print("tests=%s failures=%s errors=%s skipped=%s time=%ss" % (
    suite.get("tests"), suite.get("failures"), suite.get("errors"),
    suite.get("skipped"), suite.get("time")))
for case in suite.iter("testcase"):
    for kind in ("failure", "error", "skipped"):
        node = case.find(kind)
        if node is None:
            continue
        message = (node.get("message") or "").strip().splitlines()
        print("%-8s %s::%s" % (kind.upper(), case.get("classname", ""),
                               case.get("name")))
        if message:
            print("         %s" % message[0][:200])
PY

echo "pytest_exit=$RC"
exit "$RC"
