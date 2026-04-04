# Contributing to ContextPulse

Thanks for your interest in contributing! ContextPulse is open-source under AGPL-3.0 and we welcome community contributions.

## Development Setup

**Requirements:** Python 3.12+, Windows 10/11 or macOS 13+, Git, [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/junkyard-rules/contextpulse
cd contextpulse
uv venv
.venv\Scripts\activate
uv pip install -e "packages/core[dev]" -e packages/screen -e packages/voice -e packages/touch -e packages/project
```

This installs all packages in editable mode along with dev dependencies (pytest, ruff).

## Running Tests

```bash
# Full suite
pytest packages/ -x -q

# Single package
pytest packages/screen/tests/ -x -q
pytest packages/voice/tests/ -x -q
pytest packages/touch/tests/ -x -q
pytest packages/memory/tests/ -x -q
pytest packages/project/tests/ -x -q
```

All tests must pass before submitting a PR.

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
- **MCP servers are read-only.** They query `activity.db` but never write. Writes happen in the daemon.
- **Pro features** are gated by `@_require_pro`. Don't add this decorator to community-contributed tools.

## Contributor License Agreement

By contributing to ContextPulse, you agree that your contributions are licensed under the [AGPL-3.0](LICENSE). You also grant Jerard Ventures LLC the right to use your contributions under alternative commercial licenses, which funds continued open-source development.

## Reporting Issues

**Bugs:** Include Python version, OS version, steps to reproduce, expected vs actual behavior, and relevant logs.

**Feature requests:** Describe what you want, why existing tools don't cover it, and your proposed approach.

---

Questions? Open an issue or email david@jerardventures.com.
