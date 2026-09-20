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
# Narrowed 2026-09-19 on David's decision: this refusal used to fire on EVERY
# agent push, whatever the destination. That blocked the workflow this project's
# own CLAUDE.md prescribes -- "code-in-progress lives on the private
# contextpulse-wip remote" -- because an agent could not push there either, and
# the only ways past were David pushing by hand or --no-verify, which also
# bypasses the content gate. The tenet being enforced is about PUBLICATION:
# "anything published to a public repo". A push to a PRIVATE remote publishes
# nothing, so refusing it bought no safety and cost the private-WIP lane.
#
# Git hands a pre-push hook its destination as $1 (remote name) and $2 (remote
# URL). This gate simply never read them. It does now, and it fails CLOSED:
# only a destination positively proven PRIVATE is allowed. An unresolvable
# slug, a non-GitHub host, no gh, no auth, a network blip, or INTERNAL all
# resolve to UNKNOWN and are refused exactly as PUBLIC is -- David's standing
# rule is "unresolvable visibility = treat as public and ask".
#
# This narrows WHO may push WHERE. It does not touch WHAT may leave: the
# content gate below still runs on every push, private destinations included.

REMOTE_NAME="${1:-}"
REMOTE_URL="${2:-}"

remote_visibility() {
    # Echo PUBLIC / PRIVATE / INTERNAL / UNKNOWN for a push destination URL.
    local url="$1"
    if [ -z "$url" ]; then echo "UNKNOWN"; return; fi

    # Accept https://github.com/OWNER/REPO(.git) and git@github.com:OWNER/REPO(.git).
    local slug owner repo
    case "$url" in
        *github.com[:/]*)
            slug="${url#*github.com}"
            slug="${slug#[:/]}"
            slug="${slug%.git}"
            slug="${slug%/}"
            ;;
        *)
            # Not GitHub: nothing here can prove it is private.
            echo "UNKNOWN"; return ;;
    esac

    # Require exactly OWNER/REPO. Anything else is not a slug worth trusting.
    owner="${slug%%/*}"
    repo="${slug#*/}"
    if [ -z "$owner" ] || [ -z "$repo" ] || [ "$repo" != "${repo%%/*}" ]; then
        echo "UNKNOWN"; return
    fi

    if ! command -v gh >/dev/null 2>&1; then echo "UNKNOWN"; return; fi

    local vis
    vis="$(gh repo view "$owner/$repo" --json visibility -q .visibility 2>/dev/null)"
    case "$vis" in
        PUBLIC|PRIVATE|INTERNAL) echo "$vis" ;;
        *) echo "UNKNOWN" ;;
    esac
}

if [ "${CLAUDECODE:-}" = "1" ] || [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
    DEST_VISIBILITY="$(remote_visibility "$REMOTE_URL")"

    if [ "$DEST_VISIBILITY" = "PRIVATE" ]; then
        echo "pre-push: agent push to PRIVATE remote '${REMOTE_NAME:-?}' -- allowed." >&2
        echo "pre-push: publication gate still runs on the content below." >&2

    elif [ -n "${CONTEXTPULSE_PUBLISH_APPROVED_BY:-}" ]; then
        # Corrected 2026-09-19, David, verbatim: "I don't think I ever asked to
        # have to do pushes myself. I want you to use your skills to do pushes
        # even to public, but you need my permission to do so."
        #
        # The refusal above encoded the stricter reading -- that an agent may
        # NEVER push to a public remote -- and so made David the manual operator
        # of every publish. The rule he actually holds is that a public push
        # needs his PERMISSION, which is a different thing: permission can be
        # given, and once given the agent does the work.
        #
        # So the public path is now openable, but only by an explicit, named
        # approval that an agent has to set deliberately for this one push, and
        # every use is appended to an audit log. This is deliberately NOT a
        # security boundary -- as the note above says, anything running as David
        # can set any variable -- and the threat model has not changed: the thing
        # being prevented is an agent publishing WITHOUT BEING ASKED. An approval
        # that must be typed out, attributed, and logged cannot be reached by
        # accident or by momentum, which is the whole job.
        #
        # It is strictly better than the --no-verify it replaces: --no-verify
        # skips the content gate below as well, so the old "ask David to push it"
        # advice was one impatient keystroke away from disabling leak scanning
        # entirely. This path leaves the content gate fully armed.
        # REPO_ROOT is not set until further down, and `set -u` is on, so
        # resolve the root here rather than reaching for it early.
        APPROVAL_LOG="$(git rev-parse --show-toplevel 2>/dev/null)/logs/public-pushes.log"
        mkdir -p "$(dirname "$APPROVAL_LOG")" 2>/dev/null || true
        {
            printf '%s\tremote=%s\turl=%s\tvisibility=%s\thead=%s\tapproved_by=%s\n' \
                "$(date '+%Y-%m-%d %H:%M:%S')" \
                "${REMOTE_NAME:-?}" \
                "${REMOTE_URL:-?}" \
                "$DEST_VISIBILITY" \
                "$(git rev-parse --short HEAD 2>/dev/null || echo '?')" \
                "$CONTEXTPULSE_PUBLISH_APPROVED_BY"
        } >> "$APPROVAL_LOG" 2>/dev/null || true

        echo "pre-push: agent push to $DEST_VISIBILITY remote '${REMOTE_NAME:-?}'." >&2
        echo "pre-push: approved by: $CONTEXTPULSE_PUBLISH_APPROVED_BY" >&2
        echo "pre-push: recorded in logs/public-pushes.log; content gate still runs." >&2

    else
        echo "" >&2
        echo "PRE-PUSH REFUSED -- agent pushing to a destination that is not proven private," >&2
        echo "and no publication approval was given." >&2
        echo "" >&2
        echo "  remote:     ${REMOTE_NAME:-?} ${REMOTE_URL:-(no url given)}" >&2
        echo "  visibility: $DEST_VISIBILITY" >&2
        echo "" >&2
        echo "  David's standing direction: a public push needs his permission. Not his" >&2
        echo "  hands on the keyboard -- his permission. UNKNOWN is refused on the same" >&2
        echo "  footing as PUBLIC, because unresolved visibility is treated as public:" >&2
        echo "  the cost of a needless question is one sentence, and the cost of a wrong" >&2
        echo "  publish is a one-way door." >&2
        echo "" >&2
        echo "  What to do: tell David what you want to publish and why, and ask. If he" >&2
        echo "  says yes, re-run the push with his approval named:" >&2
        echo "" >&2
        echo "    CONTEXTPULSE_PUBLISH_APPROVED_BY=\"david, <date>, <what he approved>\" \\" >&2
        echo "      git push <remote> <branch>" >&2
        echo "" >&2
        echo "  Do NOT reach for --no-verify: that bypasses the content gate as well," >&2
        echo "  and it is the reflex this repo's own gate was tuned to avoid teaching." >&2
        echo "" >&2
        exit 1
    fi
fi

# --- WILL THIS PUSH GO RED? ---------------------------------------------------
#
# Added 2026-09-20, after David asked why failure emails keep arriving. The
# answer for the local half was that CONTRIBUTING.md and README.md named
# `pytest packages/ -x -q`, which never collects the root `tests/` directory,
# while ci.yml's test-cross-platform job runs tests/test_config_readers.py.
# Pull request #19 failed four jobs on exactly that gap: its author ran the
# documented command, saw green, and pushed.
#
# scripts/ci-tests.sh is now the single command that mirrors ci.yml, and this
# block runs it. Two tiers, because a gate that costs two minutes on every
# push is how a repo teaches itself `--no-verify` -- a reflex this gate's own
# comments were tuned to avoid:
#
#   every push        --fast   lint + the root-tests gap      ~2s here
#   public remote     full     every job this platform can run ~100s here
#
# The full set runs only where a red build is visible and costs an email. A
# push to the private work-in-progress remote stays cheap on purpose.
#
# What this CANNOT catch: platform-only failures. Headless input libraries on
# Linux runners, inode reuse on ext4, the macOS job. Those need the runners by
# definition, and roughly half this repo's red builds have been that class.
#
# Escape hatch, for a genuine emergency only:
#   CONTEXTPULSE_SKIP_CI_TESTS=1 git push ...
# It skips the test block and NOTHING else -- the publication gate below still
# runs. Prefer it over --no-verify, which turns off the content gate too.

CI_TESTS="$(git rev-parse --show-toplevel 2>/dev/null)/scripts/ci-tests.sh"

if [ -n "${CONTEXTPULSE_SKIP_CI_TESTS:-}" ]; then
    echo "pre-push: CI test check SKIPPED (CONTEXTPULSE_SKIP_CI_TESTS set)." >&2
    echo "pre-push: the publication gate below still runs." >&2
elif [ ! -f "$CI_TESTS" ]; then
    echo "pre-push: scripts/ci-tests.sh not found -- skipping the CI test check." >&2
else
    # DEST_VISIBILITY is set above only for agent pushes; compute it for
    # David's own shell too, and fail toward the thorough option when unsure.
    CI_SCOPE_VIS="${DEST_VISIBILITY:-$(remote_visibility "$REMOTE_URL")}"
    if [ "$CI_SCOPE_VIS" = "PRIVATE" ]; then
        CI_MODE="--fast"
    else
        CI_MODE=""   # full set: public, internal or unresolvable destination
    fi

    echo "pre-push: running the checks GitHub Actions runs (${CI_MODE:---full})..."
    if ! bash "$CI_TESTS" $CI_MODE; then
        echo "" >&2
        echo "PRE-PUSH REFUSED -- these checks also run in GitHub Actions, so this" >&2
        echo "push would turn the build red and mail David about it." >&2
        echo "" >&2
        echo "  Reproduce and iterate locally with:" >&2
        echo "    bash scripts/ci-tests.sh $CI_MODE" >&2
        echo "" >&2
        echo "  Genuine emergency only:" >&2
        echo "    CONTEXTPULSE_SKIP_CI_TESTS=1 git push ..." >&2
        echo "  (skips this block only; the publication gate still runs. Do NOT" >&2
        echo "   reach for --no-verify, which turns off the content gate as well.)" >&2
        echo "" >&2
        exit 1
    fi
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
