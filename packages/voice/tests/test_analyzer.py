"""Tests for the analyzer module — auto-learning from transcription history."""

import json
import pytest

from contextpulse_voice.analyzer import (
    _is_style_change,
    build_user_profile,
    find_corrections,
    find_frequent_terms,
    load_entries_from_eventbus,
)


class TestFindCorrections:
    def test_finds_consistent_correction(self):
        # Need 3+ occurrences of a single-word substitution
        entries = [
            {"raw": "use gerard system", "cleaned": "use jerard system"},
            {"raw": "call gerard now", "cleaned": "call jerard now"},
            {"raw": "ask gerard here", "cleaned": "ask jerard here"},
            {"raw": "tell gerard that", "cleaned": "tell jerard that"},
        ]
        result = find_corrections(entries)
        assert "gerard" in result
        assert result["gerard"]["replacement"] == "jerard"

    def test_ignores_single_occurrence(self):
        entries = [
            {"raw": "rare word here", "cleaned": "rare replacement here"},
        ]
        result = find_corrections(entries)
        assert len(result) == 0  # needs 3+ occurrences

    def test_ignores_style_changes(self):
        entries = [
            {"raw": "that's pretty cool", "cleaned": "that's really great"},
        ] * 5
        result = find_corrections(entries)
        # "pretty" -> "really" should be filtered as style change
        assert "pretty" not in result

    def test_ignores_heavy_rewrites(self):
        entries = [
            {"raw": "one two three four five", "cleaned": "a b c d e f g h i j"},
        ] * 5
        result = find_corrections(entries)
        assert len(result) == 0

    def test_empty_entries(self):
        assert find_corrections([]) == {}

    def test_identical_raw_cleaned(self):
        entries = [
            {"raw": "hello world", "cleaned": "hello world"},
        ] * 5
        result = find_corrections(entries)
        assert len(result) == 0


class TestIsStyleChange:
    def test_filler_words(self):
        assert _is_style_change("pretty", "nice")
        assert _is_style_change("yeah", "yes")

    def test_same_word(self):
        assert _is_style_change("hello", "hello")

    def test_single_char(self):
        assert _is_style_change("a", "the")

    def test_very_different_lengths(self):
        assert _is_style_change("hi", "superlongword")

    def test_genuine_mishearing(self):
        assert not _is_style_change("kubectl", "cubecontrol")


class TestFindFrequentTerms:
    def test_finds_proper_nouns(self):
        entries = [
            {"cleaned": "I went to GitHub today"},
            {"cleaned": "Push to GitHub now"},
            {"cleaned": "Clone from GitHub repo"},
        ]
        terms = find_frequent_terms(entries)
        term_names = [t[0] for t in terms]
        assert "GitHub" in term_names

    def test_empty_entries(self):
        assert find_frequent_terms([]) == []


class TestBuildUserProfile:
    def test_basic_profile(self):
        entries = [
            {"raw": "hello world testing", "cleaned": "Hello world testing."},
            {"raw": "another test here", "cleaned": "Another test here."},
        ]
        profile = build_user_profile(entries)
        assert profile["total_dictations"] == 2
        assert profile["avg_words_per_dictation"] == 3.0

    def test_empty_entries(self):
        assert build_user_profile([]) == {}


class TestLoadFromEventBus:
    def test_loads_from_db(self, activity_db):
        entries = load_entries_from_eventbus(activity_db)
        assert len(entries) == 5
        assert "raw" in entries[0]
        assert "cleaned" in entries[0]

    def test_nonexistent_db(self, tmp_path):
        entries = load_entries_from_eventbus(tmp_path / "nonexistent.db")
        assert entries == []
