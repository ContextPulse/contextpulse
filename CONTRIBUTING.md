# Contributing to ContextPulse

Thanks for your interest in contributing! ContextPulse is an open-source project and we welcome contributions from the community.

## Getting Started

### Prerequisites

- Python 3.14+
- Windows 10/11
- Git

### Development Setup

```bash
git clone https://github.com/junkyard-rules/contextpulse.git
cd contextpulse
python -m venv .venv
.venv\Scripts\activate
pip install -e packages/core -e packages/screen -e packages/voice -e packages/touch -e packages/project
pip install pytest ruff
```

### Running Tests

```bash
# All tests
pytest tests/ -x -q

# Specific package
pytest tests/test_sight/ -x -q
pytest tests/test_voice/ -x -q
pytest tests/test_touch/ -x -q
```

All tests must pass before submitting a PR.

## Code Style

- **Formatter/linter:** ruff (configured in `pyproject.toml`)
- **Type hints:** required on all public functions
- **Docstrings:** required on public classes and functions
- Run `ruff check .` and `ruff format .` before committing

## Pull Request Process

1. Fork the repo and create your branch from `main`
2. Make your changes — keep PRs focused on a single concern
3. Add or update tests for any new functionality
4. Ensure all tests pass (`pytest tests/ -x -q`)
5. Run linting (`ruff check . && ruff format --check .`)
6. Open a PR with a clear description of what and why

### PR Guidelines

- **One concern per PR** — don't bundle unrelated changes
- **Tests required** — new features need tests, bug fixes need regression tests
- **No breaking changes** without discussion in an issue first
- **MCP tool changes** require updating the tool documentation

## Contributor License Agreement (CLA)

By contributing to ContextPulse, you agree that your contributions will be licensed under the AGPL-3.0 license. You also grant Jerard Ventures LLC the right to use your contributions under alternative commercial licenses. This allows us to offer commercial licensing to companies that need to embed ContextPulse in proprietary products, which funds continued development of the open-source project.

## Reporting Issues

### Bug Reports

Please include:
- Python version and OS version
- Steps to reproduce
- Expected vs actual behavior
- Relevant log output (`contextpulse_crash.log` or daemon output)

### Feature Requests

Open an issue describing:
- What you want to do
- Why existing tools don't cover it
- Proposed approach (if you have one)

## Architecture Notes

Before contributing, understand the key patterns:

- **EventBus (spine):** All modules emit events here. If you add a new data source, it must emit to the EventBus.
- **MCP servers are read-only:** They query `activity.db` but never write to it. Writes happen in the daemon.
- **Dual-write:** Legacy `activity_db` tables + new `events` table. Both must be written for backward compatibility.
- **Pro features:** Gated by `@_require_pro` decorator. Don't add this to community-contributed tools.

## Code of Conduct

Be respectful, constructive, and collaborative. We're all here to build something useful.

---

Questions? Open an issue or email david@jerardventures.com.
