"""Tests for ProjectRegistry."""

from contextpulse_project.registry import ProjectRegistry


class TestProjectRegistry:
    def test_scan_finds_all_projects(self, registry):
        projects = registry.list_all()
        assert len(projects) == 4
        names = {p.name for p in projects}
        assert names == {"SwingPulse", "DryerVentCo", "Voiceasy", "ContextPulse"}

    def test_get_by_name(self, registry):
        info = registry.get("SwingPulse")
        assert info is not None
        assert info.name == "SwingPulse"
        assert "trading" in info.overview.lower()

    def test_get_case_insensitive(self, registry):
        info = registry.get("swingpulse")
        assert info is not None
        assert info.name == "SwingPulse"

    def test_get_nonexistent(self, registry):
        assert registry.get("NonExistent") is None

    def test_keywords_include_name_variants(self, registry):
        info = registry.get("SwingPulse")
        assert "swingpulse" in info.keywords
        assert "swing" in info.keywords
        assert "pulse" in info.keywords

    def test_keywords_include_tech_stack(self, registry):
        info = registry.get("SwingPulse")
        assert "next.js" in info.keywords or "fastapi" in info.keywords
        assert "supabase" in info.keywords

    def test_keywords_include_domains(self, registry):
        info = registry.get("Voiceasy")
        assert "voiceasy.app" in info.keywords

    def test_aliases_extracted(self, registry):
        info = registry.get("SwingPulse")
        assert "TradeFoundry" in info.aliases

    def test_alias_keywords(self, registry):
        info = registry.get("SwingPulse")
        assert "tradefoundry" in info.keywords

    def test_overview_extracted(self, registry):
        info = registry.get("DryerVentCo")
        assert "dryer vent" in info.overview.lower()

    def test_goals_extracted(self, registry):
        info = registry.get("DryerVentCo")
        assert any("$5K" in g for g in info.goals)

    def test_empty_dir(self, tmp_path):
        reg = ProjectRegistry(projects_root=tmp_path)
        reg.scan()
        assert reg.list_all() == []

    def test_skips_template(self, sample_projects):
        (sample_projects / "_PROJECT_TEMPLATE").mkdir()
        (sample_projects / "_PROJECT_TEMPLATE" / "PROJECT_CONTEXT.md").write_text("# Template")
        reg = ProjectRegistry(projects_root=sample_projects)
        reg.scan()
        names = {p.name for p in reg.list_all()}
        assert "_PROJECT_TEMPLATE" not in names

    def test_rescan(self, registry, sample_projects):
        # Add a new project
        new_dir = sample_projects / "NewProject"
        new_dir.mkdir()
        (new_dir / "PROJECT_CONTEXT.md").write_text("# New\n\n## Overview\nA new project.")
        registry.rescan()
        assert registry.get("NewProject") is not None
