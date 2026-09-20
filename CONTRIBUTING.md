# Contributing to ContextPulse

Thanks for your interest in contributing! ContextPulse is open-source under AGPL-3.0 and we welcome community contributions.

## Development Setup

**Requirements:** Python 3.12+, Windows 10/11 or macOS 13+, Git

We recommend [uv](https://docs.astral.sh/uv/) for fast installs, but pip works too.

```bash
git clone https://github.com/ContextPulse/contextpulse
cd contextpulse

# Option A: uv (recommended)
uv venv
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # macOS / Linux
uv pip install -e "packages/core[dev]" -e packages/screen -e packages/voice -e packages/touch -e packages/project

# Option B: pip
python -m venv .venv
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # macOS / Linux
pip install -e "packages/core[dev]" -e packages/screen -e packages/voice -e packages/touch -e packages/project
```

This installs all packages in editable mode along with dev dependencies (pytest, ruff).

### Install Git Hooks (required for maintainers)

ContextPulse is a public repo, so we run a pre-push publication gate that blocks pushes containing PII, secrets, internal project references, AWS IDs, or other OSS-unsafe content. Git doesn't track `.git/hooks/`, so enable them manually:

```bash
bash scripts/install-git-hooks.sh
```

This installs:
- **pre-commit** (gitleaks) — blocks commits containing secrets
- **pre-push** (pre-publish.py gate) — blocks pushes with BLOCKER-severity issues

If `pre-publish.py` (a maintainer-side tool) isn't available, the pre-push hook skips silently. Contributors without it can still push; GitHub Actions (`security.yml`) runs equivalent checks on every push.

Emergency bypass (use sparingly, never on main): `git push --no-verify`

## Running Tests

**Run this before you push.** It is the one command that mirrors what GitHub
Actions runs, job for job:

```bash
bash scripts/ci-tests.sh          # everything CI runs on this platform (~100s)
bash scripts/ci-tests.sh --fast   # lint + the guards `pytest packages/` misses (~2s)
bash scripts/ci-tests.sh --list   # print the commands without running them
```

`pytest packages/` alone is **not** enough, and this is not a style preference.
CI's `test-cross-platform` job also runs `tests/test_config_readers.py`, which
lives in the root `tests/` directory that `packages/` never collects. A pull
request has already gone red on exactly that gap after its author ran the
package tests and saw green. The pre-push hook runs the fast set on every push
and the full set before a push to a public remote, so in normal work you do not
have to remember this.

While iterating on one package, the narrow commands are still the fast loop:

```bash
pytest packages/screen/tests/ -x -q
pytest packages/voice/tests/ -x -q
pytest packages/touch/tests/ -x -q
pytest packages/memory/tests/ -x -q
pytest packages/project/tests/ -x -q
```

Two things `ci-tests.sh` deliberately does not claim. It cannot catch
platform-only failures: the Linux and macOS jobs need the runners, and that
class has caused about half of this repository's red builds. And if you change
a job in `.github/workflows/ci.yml`, change the matching block in
`scripts/ci-tests.sh` in the same commit. A local command that has drifted from
CI is worse than no local command, because it buys false confidence.

All tests must pass before submitting a pull request.

## Code Style

We use **ruff** for linting and formatting (configured in `pyproject.toml`).

```bash
ruff check .
ruff format .
```

- Type hints required on all public functions
- Docstrings required on public classes and functions

## Pull Request Process

1. Fork the repo and create your branch from `main`
2. Keep PRs focused on a single concern
3. Add or update tests for any new functionality
4. Ensure all tests pass and linting is clean
5. Open a PR with a clear description of what and why

### PR Guidelines

- **One concern per PR** — don't bundle unrelated changes
- **Tests required** — new features need tests, bug fixes need regression tests
- **No breaking changes** without discussion in an issue first
- **MCP tool changes** require updating tool documentation

## Architecture Notes

- **EventBus (spine):** All modules emit events here. New data sources must emit to the EventBus.
- **MCP servers never write to `activity.db`.** They query it; the daemon is the only writer. The memory server is the one exception to the wider rule: `memory_store` and `memory_forget` write by design, to their own separate `memory.db` and `memory_cold.db`.
- **Pro features** are gated by `@_require_pro`. Don't add this decorator to community-contributed tools.

## Contributor License Agreement

By contributing to ContextPulse, you agree that your contributions are licensed under the [AGPL-3.0](LICENSE). You also grant Jerard Ventures LLC the right to use your contributions under alternative commercial licenses, which funds continued open-source development.

## Reporting Issues

**Bugs:** Include Python version, OS version, steps to reproduce, expected vs actual behavior, and relevant logs.

**Feature requests:** Describe what you want, why existing tools don't cover it, and your proposed approach.

---

Questions? Open an [issue](https://github.com/ContextPulse/contextpulse/issues) or visit [contextpulse.ai](https://contextpulse.ai).
