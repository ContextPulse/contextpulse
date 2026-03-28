# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for context vocabulary builder — extracts project names for Voice."""

import json
from pathlib import Path

from contextpulse_voice.context_vocab import (
    _extract_names_from_context,
    _split_camel_to_phrase,
    build_context_vocabulary,
    get_context_entries,
    get_known_proper_nouns,
    rebuild_context_vocabulary,
)


class TestSplitCamelToPhrase:
    """Tests for CamelCase → space-separated phrase splitting."""

    def test_two_parts(self):
        assert _split_camel_to_phrase("ContextPulse") == "context pulse"

    def test_three_parts(self):
        assert _split_camel_to_phrase("StockTrader") == "stock trader"

    def test_with_co_suffix(self):
        assert _split_camel_to_phrase("DryerVentCo") == "dryer vent co"

    def test_multi_part(self):
        assert _split_camel_to_phrase("OutsideCatalyst") == "outside catalyst"

    def test_single_word_returns_none(self):
        assert _split_camel_to_phrase("Projects") is None

    def test_acronym_returns_none(self):
        assert _split_camel_to_phrase("AWS") is None

    def test_short_result_returns_none(self):
        assert _split_camel_to_phrase("HiLo") is None  # "hi lo" = 5 chars, too short

    def test_all_lower_returns_none(self):
        assert _split_camel_to_phrase("projects") is None

    def test_swing_pulse(self):
        assert _split_camel_to_phrase("SwingPulse") == "swing pulse"

    def test_trading_copilot(self):
        result = _split_camel_to_phrase("TradingCoPilot")
        assert result is not None
        assert "trading" in result

    def test_personal_vault(self):
        assert _split_camel_to_phrase("PersonalVault") == "personal vault"


class TestExtractNamesFromContext:
    """Tests for extracting proper nouns from PROJECT_CONTEXT.md content."""

    def test_finds_camel_case(self):
        text = "This is about ContextPulse and StockTrader integration."
        names = _extract_names_from_context(text)
        assert "ContextPulse" in names
        assert "StockTrader" in names

    def test_ignores_short_words(self):
        text = "The MyApp tool."
        names = _extract_names_from_context(text)
        assert "MyApp" not in names  # Only 5 chars

    def test_finds_quoted_names(self):
        text = 'The product is called "SwingPulse" and it tracks stocks.'
        names = _extract_names_from_context(text)
        assert "SwingPulse" in names

    def test_empty_text(self):
        assert _extract_names_from_context("") == []

    def test_no_camel_case(self):
        assert _extract_names_from_context("just plain text here") == []


class TestBuildContextVocabulary:
    """Tests for scanning project directories."""

    def test_scans_project_dirs(self, tmp_path):
        # Create mock project directories
        (tmp_path / "ContextPulse").mkdir()
        (tmp_path / "StockTrader").mkdir()
        (tmp_path / "simple").mkdir()  # lowercase, should not generate entry

        vocab = build_context_vocabulary(tmp_path, skills_dirs=[])
        assert "context pulse" in vocab
        assert vocab["context pulse"] == "ContextPulse"
        assert "stock trader" in vocab
        assert vocab["stock trader"] == "StockTrader"

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        vocab = build_context_vocabulary(tmp_path, skills_dirs=[])
        assert len(vocab) == 0

    def test_skips_common_phrases(self, tmp_path):
        (tmp_path / "IslandModel").mkdir()
        vocab = build_context_vocabulary(tmp_path, skills_dirs=[])
        # "island model" is in _COMMON_PHRASES
        assert "island model" not in vocab

    def test_extracts_from_project_context(self, tmp_path):
        proj = tmp_path / "MyProject"
        proj.mkdir()
        ctx = proj / "PROJECT_CONTEXT.md"
        ctx.write_text(
            "# MyProject\n\nThis uses SwingPulse for trading signals.\n",
            encoding="utf-8",
        )
        vocab = build_context_vocabulary(tmp_path, skills_dirs=[])
        assert "swing pulse" in vocab

    def test_nonexistent_root_returns_empty(self):
        vocab = build_context_vocabulary(Path("/nonexistent/path"), skills_dirs=[])
        assert vocab == {}

    def test_dir_name_priority_over_context_extract(self, tmp_path):
        """Directory name entry should not be overwritten by context extract."""
        proj = tmp_path / "ContextPulse"
        proj.mkdir()
        ctx = proj / "PROJECT_CONTEXT.md"
        # Even if context mentions a different casing
        ctx.write_text("# ContextPulse\n\n", encoding="utf-8")
        vocab = build_context_vocabulary(tmp_path, skills_dirs=[])
        assert vocab["context pulse"] == "ContextPulse"


class TestRebuildAndGet:
    """Tests for rebuild, get_context_entries, and get_known_proper_nouns."""

    def test_rebuild_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "contextpulse_voice.context_vocab.CONTEXT_VOCAB_FILE",
            tmp_path / "vocabulary_context.json",
        )
        monkeypatch.setattr(
            "contextpulse_voice.context_vocab.VOICE_DATA_DIR",
            tmp_path,
        )

        projects = tmp_path / "projects"
        projects.mkdir()
        (projects / "ContextPulse").mkdir()
        (projects / "StockTrader").mkdir()

        count = rebuild_context_vocabulary(projects)
        assert count >= 2

        vocab_file = tmp_path / "vocabulary_context.json"
        assert vocab_file.exists()
        data = json.loads(vocab_file.read_text())
        assert "context pulse" in data

    def test_get_context_entries_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "contextpulse_voice.context_vocab.CONTEXT_VOCAB_FILE",
            tmp_path / "nonexistent.json",
        )
        assert get_context_entries() == {}

    def test_get_known_proper_nouns(self, monkeypatch, tmp_path):
        vocab_file = tmp_path / "vocabulary_context.json"
        vocab_file.write_text(
            json.dumps({"context pulse": "ContextPulse", "stock trader": "StockTrader"}),
        )
        monkeypatch.setattr(
            "contextpulse_voice.context_vocab.CONTEXT_VOCAB_FILE",
            vocab_file,
        )
        nouns = get_known_proper_nouns()
        assert "ContextPulse" in nouns
        assert "StockTrader" in nouns


class TestVocabularyMerge:
    """Tests for three-way merge in vocabulary.py (user > learned > context)."""

    def test_context_vocab_loaded(self, monkeypatch, tmp_path):
        from contextpulse_voice import vocabulary

        # Set up user vocab
        user_file = tmp_path / "vocabulary.json"
        user_file.write_text(json.dumps({"git hub": "GitHub"}))
        monkeypatch.setattr(vocabulary, "VOCAB_FILE", user_file)

        # No learned vocab
        learned_file = tmp_path / "vocabulary_learned.json"
        monkeypatch.setattr(vocabulary, "LEARNED_VOCAB_FILE", learned_file)

        # Context vocab
        context_file = tmp_path / "vocabulary_context.json"
        context_file.write_text(
            json.dumps({"context pulse": "ContextPulse", "stock trader": "StockTrader"})
        )
        monkeypatch.setattr(vocabulary, "CONTEXT_VOCAB_FILE", context_file)

        data = vocabulary._load_vocabulary()
        assert data["git hub"] == "GitHub"
        assert data["context pulse"] == "ContextPulse"
        assert data["stock trader"] == "StockTrader"

    def test_user_overrides_context(self, monkeypatch, tmp_path):
        from contextpulse_voice import vocabulary

        user_file = tmp_path / "vocabulary.json"
        user_file.write_text(json.dumps({"context pulse": "CP"}))
        monkeypatch.setattr(vocabulary, "VOCAB_FILE", user_file)

        learned_file = tmp_path / "vocabulary_learned.json"
        monkeypatch.setattr(vocabulary, "LEARNED_VOCAB_FILE", learned_file)

        context_file = tmp_path / "vocabulary_context.json"
        context_file.write_text(json.dumps({"context pulse": "ContextPulse"}))
        monkeypatch.setattr(vocabulary, "CONTEXT_VOCAB_FILE", context_file)

        data = vocabulary._load_vocabulary()
        # User entry takes priority
        assert data["context pulse"] == "CP"

    def test_learned_overrides_context(self, monkeypatch, tmp_path):
        from contextpulse_voice import vocabulary

        user_file = tmp_path / "vocabulary.json"
        user_file.write_text(json.dumps({}))
        monkeypatch.setattr(vocabulary, "VOCAB_FILE", user_file)

        learned_file = tmp_path / "vocabulary_learned.json"
        learned_file.write_text(json.dumps({"context pulse": "CP-learned"}))
        monkeypatch.setattr(vocabulary, "LEARNED_VOCAB_FILE", learned_file)

        context_file = tmp_path / "vocabulary_context.json"
        context_file.write_text(json.dumps({"context pulse": "ContextPulse"}))
        monkeypatch.setattr(vocabulary, "CONTEXT_VOCAB_FILE", context_file)

        data = vocabulary._load_vocabulary()
        # Learned takes priority over context
        assert data["context pulse"] == "CP-learned"
