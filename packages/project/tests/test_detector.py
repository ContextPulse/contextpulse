"""Tests for ActiveProjectDetector."""

from contextpulse_project.detector import ActiveProjectDetector


class TestActiveProjectDetector:
    def test_cwd_detection(self, registry, sample_projects):
        detector = ActiveProjectDetector(registry, projects_root=sample_projects)
        result = detector.detect(cwd=str(sample_projects / "DataVault" / "src"))
        assert result == "DataVault"

    def test_cwd_root(self, registry, sample_projects):
        detector = ActiveProjectDetector(registry, projects_root=sample_projects)
        result = detector.detect(cwd=str(sample_projects / "WeatherApp"))
        assert result == "WeatherApp"

    def test_cwd_outside_projects(self, registry, sample_projects):
        detector = ActiveProjectDetector(registry, projects_root=sample_projects)
        result = detector.detect(cwd="/tmp/random")
        assert result is None

    def test_window_title_detection(self, registry, sample_projects):
        detector = ActiveProjectDetector(registry, projects_root=sample_projects)
        result = detector.detect(window_title="main.py - DataVault - VS Code")
        assert result == "DataVault"

    def test_window_title_alias(self, registry, sample_projects):
        detector = ActiveProjectDetector(registry, projects_root=sample_projects)
        result = detector.detect(window_title="TradeFoundry dashboard")
        assert result == "DataVault"

    def test_no_match(self, registry, sample_projects):
        detector = ActiveProjectDetector(registry, projects_root=sample_projects)
        result = detector.detect(cwd="/tmp", window_title="Google Chrome")
        assert result is None

    def test_cwd_takes_priority(self, registry, sample_projects):
        detector = ActiveProjectDetector(registry, projects_root=sample_projects)
        result = detector.detect(
            cwd=str(sample_projects / "WeatherApp"),
            window_title="DataVault dashboard",
        )
        assert result == "WeatherApp"
