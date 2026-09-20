#!/bin/bash
# Run locally exactly what GitHub Actions runs in .github/workflows/ci.yml.
#
# WHY THIS EXISTS (2026-09-20). CONTRIBUTING.md and README.md both told a
# contributor to run `pytest packages/ -x -q`. Continuous integration does not
# run that. Its test-cross-platform job runs
#
#     pytest packages/core/tests packages/memory/tests packages/project/tests \
#            tests/ --ignore=tests/integration
#
# and `pytest packages/` reaches none of the root `tests/` directory. So the
# documented command could pass on a developer's machine while the build went
# red on a file that command never collected -- which is exactly what happened
# to pull request #19: the author ran the voice package's tests, saw green,
# pushed, and the env-read guard in tests/test_config_readers.py failed four
# jobs. The command was not careless; it was the command the docs named.
#
# The fix is this script, and the rule that goes with it: when a job in ci.yml
# changes, change the matching block below in the same commit. A local command
# that has drifted from CI is worse than no local command, because it buys
# false confidence.
#
# WHAT IT CANNOT DO. Roughly half of this repo's red builds are platform-only:
# a headless input library on Linux runners, inode reuse on ext4, a macOS job.
# Nothing run on a Windows desktop can catch those, and this script does not
# pretend to. It closes the gap that IS local, which is the gap that keeps
# biting.
#
# USAGE
#   scripts/ci-tests.sh           full set, mirrors every CI job (~100s here)
#   scripts/ci-tests.sh --fast    lint + the root-tests gap only (~2s)
#   scripts/ci-tests.sh --list    print the commands without running them
#
# --fast is what the pre-push hook runs on every push; the full set runs before
# a push to a public remote, where a red build is visible and costs an email.
# Exit code is 0 only if every block passed.

set -u

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ci-tests: not inside a git repository" >&2
    exit 1
}
cd "$REPO_ROOT" || exit 1

# The repo's own virtualenv, so this behaves the same from any shell. Falls
# back to whatever python is on PATH (CI has no .venv).
PY="$REPO_ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python"

MODE="full"
case "${1:-}" in
    --fast) MODE="fast" ;;
    --list) MODE="list" ;;
    "")     MODE="full" ;;
    *)      echo "ci-tests: unknown argument '$1' (want --fast, --list or nothing)" >&2
            exit 2 ;;
esac

FAILED=()
PASSED=()

# Run one CI block. $1 = label (names the ci.yml job it mirrors), rest = command.
# Mirrors CI's own tolerance of pytest exit 5 ("no tests collected") only for
# the property block, which ci.yml handles the same way and for the same reason.
run_block() {
    local label="$1"; shift
    if [ "$MODE" = "list" ]; then
        printf '  %-28s %s\n' "$label" "$*"
        return 0
    fi
    printf '\n=== %s\n' "$label"
    "$@"
    local rc=$?
    if [ "$rc" -eq 5 ] && [ "$label" = "test-integration: property" ]; then
        echo "No property tests collected -- OK (ci.yml treats exit 5 the same way)"
        rc=0
    fi
    if [ "$rc" -eq 0 ]; then
        PASSED+=("$label")
    else
        FAILED+=("$label (exit $rc)")
    fi
    return 0
}

if [ "$MODE" = "list" ]; then
    echo "Blocks this script runs, and the ci.yml job each mirrors:"
fi

# --- lint job -----------------------------------------------------------------
run_block "lint" \
    "$PY" -m ruff check \
    packages/core/src packages/memory/src packages/project/src \
    packages/screen/src packages/voice/src packages/touch/src \
    packages/knowledge/src

# --- test-cross-platform job --------------------------------------------------
# The root-tests half only. The packages half is a strict subset of the
# test-windows block below, so running it twice would just cost time.
# THIS is the block that `pytest packages/` misses entirely.
#
# Widened from the single test_config_readers.py file to the whole directory on
# 2026-09-20, in the same commit that widened ci.yml, per the rule above. Seven
# other root files had been sitting here uncollected by any job
# (cp-seven-test-files-run-by-no-ci-job). tests/integration/ is excluded because
# the test-integration job owns it and it is windows-only.
run_block "test-cross-platform: root tests" \
    "$PY" -m pytest tests/ --ignore=tests/integration -q --tb=short -p no:cacheprovider

if [ "$MODE" = "fast" ]; then
    :
else
    # --- test-windows job -----------------------------------------------------
    # On a non-Windows machine this also stands in for test-macos, whose command
    # is this one plus `-k "not windows"` -- a strict subset.
    run_block "test-windows: all packages" \
        "$PY" -m pytest packages/ -q --tb=short --ignore=packages/agent -p no:cacheprovider

    # --- test-integration job -------------------------------------------------
    run_block "test-integration: integration" \
        "$PY" -m pytest tests/integration/ -q --tb=short \
        -m "integration or not integration" --timeout=60 -p no:cacheprovider

    run_block "test-integration: property" \
        "$PY" -m pytest packages/ -q --tb=short -m property --timeout=120 -p no:cacheprovider
fi

if [ "$MODE" = "list" ]; then
    echo ""
    echo "(--fast runs the first two only)"
    exit 0
fi

echo ""
echo "=============================================="
if [ "${#FAILED[@]}" -eq 0 ]; then
    echo "ci-tests ($MODE): all ${#PASSED[@]} block(s) passed."
    echo ""
    echo "Platform-only failures are still possible: this machine cannot run the"
    echo "Linux or macOS jobs. Green here means the LOCAL half of CI is green."
    exit 0
fi

echo "ci-tests ($MODE): ${#FAILED[@]} block(s) FAILED:"
for f in "${FAILED[@]}"; do echo "  - $f"; done
echo ""
echo "These same commands run in GitHub Actions, so this push would go red."
exit 1
