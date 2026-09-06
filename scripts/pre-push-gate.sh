#!/bin/bash
# ContextPulse pre-push gate — runs pre-publish.py before every push.
#
# Blocks the push if any BLOCKER-severity issue is detected:
#   - PII (emails), secrets (gitleaks), internal project refs, AWS IDs,
#     hardcoded user paths, license issues, deleted-file leaks.
#
# HIGH/MEDIUM/LOW findings print a warning but do NOT block — EXCEPT the
# content-leak class below, which blocks regardless of declared severity.
#
# Why (2026-08-17): a merge audit of phase1-kg-spine found an internal handoff
# doc referencing a DIFFERENT private venture (CryptoTrader) and 12 files with
# absolute paths under a developer home directory (the literal is omitted here
# on purpose: check 26 scans this file too, and a comment quoting the pattern
# blocks the gate on its own documentation). Both are HIGH, not BLOCKER, so this
# gate would
# have printed a warning and let the push through to a PUBLIC repo. Severity
# tuned for "release quality" is the wrong axis for "must never be published":
# a lint warning is not the same kind of thing as another project's name.
#
# Deliberately NOT blocking on all HIGH — ruff/bandit/pip-audit noise would
# train the reflex to reach for --no-verify, which is strictly worse.
#
# Bypass only in a true emergency: git push --no-verify
# (CI still runs security.yml, so the leak gets caught post-push — fix fast.)

set -u

# --- WHO is pushing, checked before WHAT is being pushed ----------------------
#
# Added 2026-09-06, from David's standing direction the same day: "My time is
# required on five things only: ... anything published to a public repo ...".
# Publication to a public repo is HIS decision, and that is true of a clean push
# as much as a dirty one.
#
# The gate below is excellent at its own job and answers a different question.
# It asks "is this content safe to publish" -- PII, secrets, internal project
# names, leaked paths -- and a perfectly clean agent push passes it. The tenet is
# not about content quality; it is about who gets to decide that this estate
# publishes at all. So this check runs FIRST and is cheap.
#
# CLAUDECODE is set in every Claude Code session and unset in David's own shell,
# which makes it the discriminator. It is not a security boundary -- anything
# running as David can unset it -- and it is not trying to be: the threat here is
# an agent publishing without being asked, not an attacker. The security boundary
# for content is the gate below, and CI re-runs it after the push regardless.
if [ "${CLAUDECODE:-}" = "1" ] || [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
    echo "" >&2
    echo "PRE-PUSH REFUSED -- this is a PUBLIC repository and an agent is pushing." >&2
    echo "" >&2
    echo "  David's standing direction, 2026-09-06: publishing anything to a public" >&2
    echo "  repo is one of the five things reserved to him. That holds even when the" >&2
    echo "  content is clean -- the decision to publish is his, not the content's." >&2
    echo "" >&2
    echo "  What to do: stop, and tell David what you want to publish and why." >&2
    echo "  He pushes it himself, or tells you to. Do NOT reach for --no-verify:" >&2
    echo "  that bypasses the content gate as well, and it is the reflex this repo's" >&2
    echo "  own gate was tuned to avoid teaching." >&2
    echo "" >&2
    exit 1
fi

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

# Checks whose whole purpose is "this must never be published." Any non-DONE
# result here blocks the push regardless of the check's declared severity.
#   24 PII · 26 hardcoded user paths · 66 business-strategy docs
#   67 internal project refs · 68 infrastructure IDs · 69 patent/trademark
#   70 marketing/launch materials · 71 agent/AI config files
LEAK_CLASS_IDS = {24, 26, 66, 67, 68, 69, 70, 71}

real_blockers = []
high_count = 0
high_sample = []
for r in data.get("results", []):
    sev = r.get("severity")
    status = r.get("status")
    detail = r.get("detail", "")
    try:
        cid = int(r.get("id"))
    except (TypeError, ValueError):
        cid = None
    if sev == "BLOCKER" and status != "DONE":
        if "SKIPPED" in detail or "--skip-history" in detail:
            continue
        real_blockers.append(f"  [{r.get('id')}] {r.get('name')}: {detail}")
    elif cid in LEAK_CLASS_IDS and status != "DONE":
        if "SKIPPED" in detail or "--skip-history" in detail:
            continue
        real_blockers.append(
            f"  [{r.get('id')}] {r.get('name')} (leak-class {sev}): {detail}"
        )
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
