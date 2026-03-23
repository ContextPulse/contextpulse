"""Tests for EventBus emit, query, search, listeners, and error handling."""

import time

import pytest

from contextpulse_core.spine.bus import EventBus
from contextpulse_core.spine.events import ContextEvent, EventType, Modality


@pytest.fixture
def bus(tmp_path):
    """Create an EventBus with a temporary database."""
    db_path = tmp_path / "test_activity.db"
    b = EventBus(db_path)
    yield b
    b.close()


def _make_event(**kwargs) -> ContextEvent:
    """Helper to create a valid ContextEvent with overrides."""
    defaults = dict(
        modality=Modality.SYSTEM,
        event_type=EventType.WINDOW_FOCUS,
    )
    defaults.update(kwargs)
    return ContextEvent(**defaults)


class TestEmit:
    """Test EventBus.emit() stores events correctly."""

    def test_emit_single_event(self, bus):
        event = _make_event(app_name="Chrome")
        bus.emit(event)
        assert bus.count() == 1

    def test_emit_multiple_events(self, bus):
        for i in range(5):
            bus.emit(_make_event(app_name=f"App{i}"))
        assert bus.count() == 5

    def test_emit_duplicate_ignored(self, bus):
        event = _make_event(event_id="dup-1")
        bus.emit(event)
        bus.emit(event)  # INSERT OR IGNORE
        assert bus.count() == 1

    def test_emit_invalid_event_raises(self, bus):
        bad_event = ContextEvent(event_id="", timestamp=-1.0)
        with pytest.raises(ValueError, match="Invalid event"):
            bus.emit(bad_event)

    def test_emit_invalid_empty_id_raises(self, bus):
        bad_event = ContextEvent(event_id="")
        with pytest.raises(ValueError):
            bus.emit(bad_event)


class TestQueryRecent:
    """Test EventBus.query_recent() time and modality filters."""

    def test_query_recent_returns_events(self, bus):
        bus.emit(_make_event())
        results = bus.query_recent(seconds=60)
        assert len(results) == 1
        assert isinstance(results[0], ContextEvent)

    def test_query_recent_respects_time_window(self, bus):
        # Emit event with old timestamp (won't match recent query)
        old_event = _make_event(timestamp=time.time() - 600)
        bus.emit(old_event)
        bus.emit(_make_event())  # current time
        results = bus.query_recent(seconds=60)
        assert len(results) == 1

    def test_query_recent_modality_filter(self, bus):
        bus.emit(_make_event(modality=Modality.SIGHT, event_type=EventType.SCREEN_CAPTURE))
        bus.emit(_make_event(modality=Modality.VOICE, event_type=EventType.TRANSCRIPTION))
        bus.emit(_make_event(modality=Modality.SYSTEM))

        sight_events = bus.query_recent(seconds=60, modality="sight")
        assert len(sight_events) == 1
        assert sight_events[0].modality == Modality.SIGHT

    def test_query_recent_limit(self, bus):
        for _ in range(10):
            bus.emit(_make_event())
        results = bus.query_recent(seconds=60, limit=3)
        assert len(results) == 3

    def test_query_recent_empty(self, bus):
        results = bus.query_recent(seconds=60)
        assert results == []


class TestSearch:
    """Test EventBus.search() FTS and fallback."""

    def test_search_by_window_title(self, bus):
        bus.emit(_make_event(
            window_title="GitHub Pull Request",
            app_name="Chrome",
        ))
        results = bus.search("GitHub", minutes_ago=5)
        assert len(results) >= 1

    def test_search_no_results(self, bus):
        bus.emit(_make_event(window_title="Notepad"))
        results = bus.search("nonexistent_term_xyz", minutes_ago=5)
        assert len(results) == 0

    def test_search_modality_filter(self, bus):
        bus.emit(_make_event(
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            window_title="SearchTarget",
        ))
        bus.emit(_make_event(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            window_title="SearchTarget",
        ))
        results = bus.search("SearchTarget", minutes_ago=5, modality="sight")
        assert len(results) >= 1
        for r in results:
            assert r["modality"] == "sight"


class TestGetByTime:
    """Test EventBus.get_by_time() temporal correlation."""

    def test_get_by_time_returns_nearby_events(self, bus):
        ts = time.time() - 200  # use past timestamps to satisfy validation
        bus.emit(_make_event(timestamp=ts))
        bus.emit(_make_event(timestamp=ts + 1))
        bus.emit(_make_event(timestamp=ts - 100))  # outside window

        results = bus.get_by_time(ts, window_seconds=5)
        assert len(results) == 2

    def test_get_by_time_empty(self, bus):
        results = bus.get_by_time(time.time(), window_seconds=1)
        assert results == []


class TestCount:
    """Test EventBus.count() with and without filters."""

    def test_count_empty(self, bus):
        assert bus.count() == 0

    def test_count_all(self, bus):
        bus.emit(_make_event())
        bus.emit(_make_event())
        assert bus.count() == 2

    def test_count_by_modality(self, bus):
        bus.emit(_make_event(modality=Modality.SIGHT, event_type=EventType.SCREEN_CAPTURE))
        bus.emit(_make_event(modality=Modality.VOICE, event_type=EventType.TRANSCRIPTION))
        bus.emit(_make_event(modality=Modality.SIGHT, event_type=EventType.OCR_RESULT))
        assert bus.count(modality="sight") == 2
        assert bus.count(modality="voice") == 1


class TestListeners:
    """Test EventBus listener notification."""

    def test_listener_called_on_emit(self, bus):
        received = []
        bus.on(lambda e: received.append(e))
        event = _make_event()
        bus.emit(event)
        assert len(received) == 1
        assert received[0].event_id == event.event_id

    def test_multiple_listeners(self, bus):
        counts = [0, 0]

        def listener_a(e):
            counts[0] += 1

        def listener_b(e):
            counts[1] += 1

        bus.on(listener_a)
        bus.on(listener_b)
        bus.emit(_make_event())
        assert counts == [1, 1]

    def test_listener_error_does_not_block(self, bus):
        """A failing listener should not prevent other listeners or crash emit."""
        received = []

        def bad_listener(e):
            raise RuntimeError("boom")

        def good_listener(e):
            received.append(e)

        bus.on(bad_listener)
        bus.on(good_listener)
        bus.emit(_make_event())  # should not raise
        assert len(received) == 1
