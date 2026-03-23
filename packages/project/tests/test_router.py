"""Tests for ProjectRouter."""

from contextpulse_project.router import ProjectRouter


class TestProjectRouter:
    def test_exact_name_match(self, registry):
        router = ProjectRouter(registry)
        matches = router.route("We should add this to SwingPulse")
        assert matches[0].project == "SwingPulse"
        assert matches[0].score == 1.0

    def test_keyword_match(self, registry):
        router = ProjectRouter(registry)
        matches = router.route("faster-whisper transcription voice dictation windows")
        assert matches[0].project == "Voiceasy"

    def test_dryer_vent_match(self, registry):
        router = ProjectRouter(registry)
        matches = router.route("dryer vent cleaning business Aspen luxury market")
        assert matches[0].project == "DryerVentCo"

    def test_alias_match(self, registry):
        router = ProjectRouter(registry)
        matches = router.route("TradeFoundry needs a new backend")
        assert matches[0].project == "SwingPulse"

    def test_no_match(self, registry):
        router = ProjectRouter(registry)
        matches = router.route("completely unrelated random gibberish xyzzy")
        assert len(matches) == 0

    def test_top_n(self, registry):
        router = ProjectRouter(registry)
        matches = router.route("python project AI agents", top_n=2)
        assert len(matches) <= 2

    def test_scores_normalized(self, registry):
        router = ProjectRouter(registry)
        matches = router.route("SwingPulse trading platform")
        assert matches[0].score == 1.0
        for m in matches[1:]:
            assert 0 <= m.score <= 1.0

    def test_best_match(self, registry):
        router = ProjectRouter(registry)
        best = router.best_match("contextpulse screen capture MCP")
        assert best is not None
        assert best.project == "ContextPulse"

    def test_best_match_none(self, registry):
        router = ProjectRouter(registry)
        best = router.best_match("xyzzy nothing matches")
        assert best is None

    def test_domain_match(self, registry):
        router = ProjectRouter(registry)
        matches = router.route("deploy the landing page to contextpulse.ai")
        assert matches[0].project == "ContextPulse"
