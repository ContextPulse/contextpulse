"""Shared fixtures for contextpulse-project tests."""


import pytest
from contextpulse_project.registry import ProjectRegistry


@pytest.fixture
def sample_projects(tmp_path):
    """Create a minimal project tree with PROJECT_CONTEXT.md files."""
    projects = {
        "DataVault": {
            "overview": "AI-powered trading platform combining journaling, automated execution, backtesting, and curated picks.",
            "goals": ["- Build SwingPicks email alerts\n- Reach $10K MRR in 6 months"],
            "tech": ["- Next.js\n- FastAPI\n- Supabase\n- Alpaca"],
            "extra": "Registered swingpulse.io. Formerly TradeFoundry.",
        },
        "WeatherApp": {
            "overview": "Dryer vent cleaning business targeting Aspen luxury B2B and Boulder residential B2C.",
            "goals": ["- Target $5K/month/market by Q3 2026\n- PE exit by 2031"],
            "tech": ["- Python\n- Claude API\n- AWS Lambda"],
            "extra": "Partners: Jane Smith, Dan G.",
        },
        "SampleApp": {
            "overview": "Local-first voice dictation for Windows. Hold Ctrl+Space to speak, faster-whisper transcription.",
            "goals": ["- Launch on Product Hunt\n- Build paid subscription tier"],
            "tech": ["- Python 3.14\n- faster-whisper\n- PyInstaller\n- Inno Setup"],
            "extra": "Live on Gumroad. Domain: voiceasy.app.",
        },
        "ContextPulse": {
            "overview": "Always-on context platform for AI agents. Screen capture is product #1.",
            "goals": ["- Deploy landing page on contextpulse.ai\n- Ship Memory package"],
            "tech": ["- Python 3.14\n- mss\n- pynput\n- MCP SDK"],
            "extra": "Domains: contextpulse.ai, contextpulse.dev, contextpulse.io.",
        },
    }

    for name, content in projects.items():
        project_dir = tmp_path / name
        project_dir.mkdir()
        ctx = f"""# Project Context — {name}

## Overview
{content['overview']}

{content['extra']}

## Goals
{chr(10).join(content['goals'])}

## Tech Stack
{chr(10).join(content['tech'])}
"""
        (project_dir / "PROJECT_CONTEXT.md").write_text(ctx, encoding="utf-8")

    return tmp_path


@pytest.fixture
def registry(sample_projects):
    """Return a ProjectRegistry scanned against sample projects."""
    reg = ProjectRegistry(projects_root=sample_projects)
    reg.scan()
    return reg
