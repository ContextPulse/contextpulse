"""Export ContextPulse to a clean public repo directory.

Copies only public-safe files, excluding business plans, IP docs,
internal strategy docs, personal config, and benchmark results.
"""

import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
DST = SRC.parent / "ContextPulse-public"

# ── Directories to COMPLETELY EXCLUDE ──
EXCLUDE_DIRS = {
    "business-plan",
    "ip",
    ".claude",
    ".git",
    ".venv",
    "__pycache__",
    "dist", "dist2", "dist3",
    "build", "build2", "build3",
    "installer_output",
    "screenshots",
    "logs",
    ".wrangler",
    ".vscode",
    ".idea",
    "benchmark_results",
    "lambda/package",
}

# ── Specific files to EXCLUDE ──
EXCLUDE_FILES = {
    # Internal agent/project docs
    "CLAUDE.md",
    "PROJECT_CONTEXT.md",
    "LESSONS_LEARNED.md",
    "LESSONS_LEARNED_ARCHIVE.md",
    "TONIGHT_WORK_PLAN.md",
    "business-plan-prompt.md",
    "technical-plan-prompt.md",
    # Sensitive docs
    "docs/DOMAINS.md",
    "docs/LICENSE_PIPELINE.md",
    "docs/COMPETITIVE_LANDSCAPE_AND_FEATURE_ROADMAP.md",
    "docs/MONETIZATION.md",
    "docs/POSITIONING.md",
    "docs/LEAD_MAGNET.md",
    "docs/MARKETING_ASSETS.md",
    "docs/LAUNCH_POSTS.md",
    "docs/LANDING_PAGE_COPY.md",
    "docs/NAMING.md",
    "docs/ContextPulse-Market-Research.docx",
    "docs/gen_docx.js",
    "docs/market-research.md",
    "docs/concept-synapseai-original.md",
    "docs/VOICEASY_LONG_RUNNING_ANALYSIS.md",
    "docs/SESSION_B_DESIGN_SPEC_PROMPT.md",
    "docs/SESSION_B_FEATURE_EVAL_PROMPT.md",
    "docs/FEATURE_EVAL_PROMPT.md",
    "docs/elevator-pitch.md",
    # Benchmark with real window titles
    "packages/screen/benchmark_results/auto_benchmark_20260321_140502.json",
    # Internal scripts with hardcoded paths or infra details
    "scripts/canary_health_check.py",
    "scripts/canary_healthcheck.py",
    # Lambda deploy with S3 bucket name and SSM paths
    "lambda/deploy.sh",
    # Lambda deploy artifacts
    "lambda/lambda-deploy.zip",
    # Brand internal docs (keep assets, exclude strategy)
    "brand/BRAND.md",
    "brand/VISUAL_DESIGN_SPEC.md",
    "brand/voice.md",
    "brand/assets.md",
    # Leak test file
    "leak.json",
}

# ── Docs to KEEP (public-safe) ──
KEEP_DOCS = {
    "docs/MEMORY_MVP.md",
    "docs/VISION.md",
    "docs/FEATURE_IDEAS.md",
    "docs/FEATURE_PROPOSALS.md",
    "docs/FEATURE_FEASIBILITY.md",
    "docs/FEATURE_ROADMAP.md",
    "docs/PRODUCT_ROADMAP.md",
    "docs/SELF_IMPROVING_CONTEXT.md",
    "docs/ecosystem-roadmap.md",
    "docs/UNINSTALL.md",
    "docs/mcp-configs/README.md",
}


def should_exclude(rel: Path) -> bool:
    """Check if a file should be excluded from the public repo."""
    rel_str = str(rel).replace("\\", "/")

    # Check directory exclusions
    for d in EXCLUDE_DIRS:
        if rel_str.startswith(d + "/") or rel_str == d:
            return True

    # Check file exclusions
    if rel_str in EXCLUDE_FILES:
        return True

    # Exclude all docs/ except the ones in KEEP_DOCS
    if rel_str.startswith("docs/") and rel_str not in KEEP_DOCS:
        return True

    # Exclude egg-info, pyc, pyo
    if ".egg-info" in rel_str or rel_str.endswith((".pyc", ".pyo")):
        return True

    return False


def main():
    # Clean destination
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)

    copied = 0
    skipped = 0

    # Use git ls-files to only copy tracked files
    import subprocess
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=str(SRC),
        capture_output=True, text=True
    )
    tracked_files = result.stdout.strip().split("\n")

    for rel_str in tracked_files:
        rel = Path(rel_str)
        if should_exclude(rel):
            skipped += 1
            continue

        src_file = SRC / rel
        dst_file = DST / rel

        if not src_file.exists():
            continue

        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        copied += 1

    # Also copy untracked files we want (new files from this session)
    extras = [
        ".pre-commit-config.yaml",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/ISSUE_TEMPLATE/feature_request.md",
        "packages/core/src/contextpulse_core/platform/__init__.py",
        "packages/core/src/contextpulse_core/platform/base.py",
        "packages/core/src/contextpulse_core/platform/factory.py",
        "packages/core/src/contextpulse_core/platform/linux.py",
        "packages/core/src/contextpulse_core/platform/macos.py",
        "packages/core/src/contextpulse_core/platform/windows.py",
    ]
    for rel_str in extras:
        src_file = SRC / rel_str
        dst_file = DST / rel_str
        if src_file.exists() and not dst_file.exists():
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            copied += 1

    print(f"Exported {copied} files, skipped {skipped}")
    print(f"Destination: {DST}")


if __name__ == "__main__":
    main()
