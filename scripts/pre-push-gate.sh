#!/bin/bash
# ContextPulse pre-push gate — runs pre-publish.py before every push.
#
# Blocks the push if any BLOCKER-severity issue is detected:
#   - PII (emails), secrets (gitleaks), internal project refs, AWS IDs,
#     hardcoded user paths, license issues, deleted-file leaks.
#
# HIGH/MEDIUM/LOW findings print a warning but do NOT block.
#
# Bypass only in a true emergency: git push --no-verify
# (CI still runs security.yml, so the leak gets caught post-push — fix fast.)

set -u

PRE_PUBLISH="$HOME/Projects/AgentConfig/scripts/pre-publish.py"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"

if [ -z "$REPO_ROOT" ]; then
    echo "pre-push: not in a git repo" >&2
    exit 1
fi

if [ ! -f "$PRE_PUBLISH" ]; then
    echo "pre-push: pre-publish.py not found at $PRE_PUBLISH — skipping gate" >&2
    echo "pre-push: install AgentConfig to enable the pre-push gate" >&2
    exit 0
fi

echo "pre-push: running publication gate (pre-publish.py)..."

JSON_OUT="$(mktemp)"
STDERR_OUT="$(mktemp)"
trap 'rm -f "$JSON_OUT" "$STDERR_OUT"' EXIT

# On Windows/MSYS, Python sees Win paths, not MSYS /tmp paths.
if command -v cygpath >/dev/null 2>&1; then
    JSON_PY="$(cygpath -w "$JSON_OUT")"
else
    JSON_PY="$JSON_OUT"
fi

# Full history scan is covered by GitHub Actions security.yml on every push.
# Skip it locally for speed. CI catches anything we miss here.
python "$PRE_PUBLISH" "$REPO_ROOT" --skip-history --json > "$JSON_OUT" 2> "$STDERR_OUT"

# Parse results — real blockers only (excluding --skip-history artifacts).
PARSE_OUT="$(python <<EOF
import json
try:
    with open(r"$JSON_PY") as f:
        data = json.load(f)
except Exception as e:
    print(f"PARSE_ERROR: {e}")
    raise SystemExit(0)

real_blockers = []
high_count = 0
high_sample = []
for r in data.get("results", []):
    sev = r.get("severity")
    status = r.get("status")
    detail = r.get("detail", "")
    if sev == "BLOCKER" and status != "DONE":
        if "SKIPPED" in detail or "--skip-history" in detail:
            continue
        real_blockers.append(f"  [{r.get('id')}] {r.get('name')}: {detail}")
    elif sev == "HIGH" and status != "DONE":
        high_count += 1
        if len(high_sample) < 5:
            high_sample.append(f"  [{r.get('id')}] {r.get('name')}: {detail[:80]}")

print(f"BLOCKER_COUNT:{len(real_blockers)}")
print(f"HIGH_COUNT:{high_count}")
if real_blockers:
    print("BLOCKERS:")
    for b in real_blockers:
        print(b)
if high_sample:
    print("HIGH_SAMPLE:")
    for h in high_sample:
        print(h)
EOF
)"

BLOCKER_COUNT=$(echo "$PARSE_OUT" | grep '^BLOCKER_COUNT:' | cut -d: -f2)
HIGH_COUNT=$(echo "$PARSE_OUT" | grep '^HIGH_COUNT:' | cut -d: -f2)

if [ "${BLOCKER_COUNT:-0}" -gt 0 ]; then
    echo ""
    echo "=================================================================="
    echo "  BLOCKED: pre-publish gate found $BLOCKER_COUNT BLOCKER issue(s)."
    echo "=================================================================="
    echo ""
    echo "$PARSE_OUT" | sed -n '/^BLOCKERS:/,/^HIGH_SAMPLE:/p' | grep -v '^HIGH_SAMPLE:' | grep -v '^BLOCKERS:'
    echo ""
    echo "Fix these, then push again. Emergency bypass: git push --no-verify"
    echo "(Use --no-verify with care — this is a PUBLIC repo.)"
    exit 1
fi

if [ "${HIGH_COUNT:-0}" -gt 0 ]; then
    echo "pre-push: OK — no BLOCKERS, but $HIGH_COUNT HIGH warning(s):"
    echo "$PARSE_OUT" | sed -n '/^HIGH_SAMPLE:/,$p' | grep -v '^HIGH_SAMPLE:'
    echo "(HIGH issues don't block push but should be addressed before release.)"
else
    echo "pre-push: OK — no blockers or HIGH warnings"
fi

exit 0
