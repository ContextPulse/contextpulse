"""Tests for MCP tool functions (unit tests, no MCP transport)."""

import json

from contextpulse_project import ActiveProjectDetector, ProjectRouter, mcp_server
from contextpulse_project.mcp_server import (
    get_active_project,
    get_project_context,
    identify_project,
    list_projects,
)


class TestMCPTools:
    def setup_method(self, method):
        """Patch the module-level instances before each test."""
        self._orig_registry = mcp_server._registry
        self._orig_router = mcp_server._router
        self._orig_detector = mcp_server._detector

    def teardown_method(self, method):
        """Restore original instances."""
        mcp_server._registry = self._orig_registry
        mcp_server._router = self._orig_router
        mcp_server._detector = self._orig_detector

    def _setup_registry(self, registry):
        mcp_server._registry = registry
        mcp_server._router = ProjectRouter(registry)
        mcp_server._detector = ActiveProjectDetector(registry)

    def test_identify_project(self, registry):
        self._setup_registry(registry)
        result = json.loads(identify_project("faster-whisper dictation windows"))
        assert result["project"] == "Voiceasy"
        assert result["score"] > 0

    def test_identify_project_no_match(self, registry):
        self._setup_registry(registry)
        result = json.loads(identify_project("xyzzy gibberish nothing"))
        assert result["project"] is None

    def test_get_active_project_cwd(self, registry, sample_projects):
        mcp_server._registry = registry
        mcp_server._detector = ActiveProjectDetector(registry, projects_root=sample_projects)
        result = json.loads(get_active_project(cwd=str(sample_projects / "DryerVentCo")))
        assert result["project"] == "DryerVentCo"

    def test_get_active_project_empty(self, registry):
        self._setup_registry(registry)
        result = json.loads(get_active_project())
        assert result["project"] is None

    def test_list_projects(self, registry):
        self._setup_registry(registry)
        result = list_projects()
        assert "SwingPulse" in result
        assert "DryerVentCo" in result
        assert "Voiceasy" in result
        assert "ContextPulse" in result

    def test_get_project_context_found(self, registry):
        self._setup_registry(registry)
        result = get_project_context("SwingPulse")
        assert "trading" in result.lower()

    def test_get_project_context_not_found(self, registry):
        self._setup_registry(registry)
        result = get_project_context("NonExistent")
        assert "not found" in result.lower()
