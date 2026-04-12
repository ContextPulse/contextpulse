"""Tests for the vocabulary module — word replacement and hot-reload."""

import json
from unittest.mock import patch

from contextpulse_voice import vocabulary


class TestApplyPunctuation:
    def test_period(self):
        result = vocabulary.apply_punctuation("hello period how are you")
        assert "." in result

    def test_comma(self):
        result = vocabulary.apply_punctuation("first comma second")
        assert "," in result

    def test_question_mark(self):
        result = vocabulary.apply_punctuation("how are you question mark")
        assert "?" in result

    def test_exclamation(self):
        result = vocabulary.apply_punctuation("wow exclamation point")
        assert "!" in result

    def test_newline(self):
        result = vocabulary.apply_punctuation("first new line second")
        assert "\n" in result

    def test_new_paragraph(self):
        result = vocabulary.apply_punctuation("first new paragraph second")
        assert "\n\n" in result

    def test_ellipsis(self):
        result = vocabulary.apply_punctuation("well ellipsis I think")
        assert "..." in result

    def test_redundant_punctuation_cleanup(self):
        result = vocabulary.apply_punctuation("hello, period the")
        # Should not have both comma and period
        assert ",." not in result

    def test_empty_string(self):
        assert vocabulary.apply_punctuation("") == ""

    def test_colon(self):
        result = vocabulary.apply_punctuation("note colon this is important")
        assert ":" in result

    def test_semicolon(self):
        result = vocabulary.apply_punctuation("first semicolon second")
        assert ";" in result

    def test_parentheses(self):
        result = vocabulary.apply_punctuation("hello open parenthesis world close parenthesis")
        assert "(" in result
        assert ")" in result


class TestApplyVocabulary:
    def test_empty_string(self):
        assert vocabulary.apply_vocabulary("") == ""

    def test_applies_default_vocabulary(self):
        # Force-load defaults
        vocabulary._compiled_patterns = None
        with patch.object(vocabulary, 'VOCAB_FILE', vocabulary.VOICE_DATA_DIR / "nonexistent.json"):
            with patch.object(vocabulary, 'LEARNED_VOCAB_FILE', vocabulary.VOICE_DATA_DIR / "nonexistent_learned.json"):
                vocabulary._compiled_patterns = vocabulary._compile_patterns(vocabulary._DEFAULT_VOCABULARY)
                result = vocabulary.apply_vocabulary("I use cube control daily")
                assert "kubectl" in result

    def test_word_boundary_matching(self):
        vocabulary._compiled_patterns = vocabulary._compile_patterns({"get hub": "GitHub"})
        result = vocabulary.apply_vocabulary("push to get hub")
        assert "GitHub" in result

    def test_case_insensitive(self):
        vocabulary._compiled_patterns = vocabulary._compile_patterns({"docker": "Docker"})
        result = vocabulary.apply_vocabulary("I love DOCKER containers")
        assert "Docker" in result

    def test_longer_phrases_match_first(self):
        vocab = {"post gress q l": "PostgreSQL", "post gress": "PostgreSQL"}
        vocabulary._compiled_patterns = vocabulary._compile_patterns(vocab)
        result = vocabulary.apply_vocabulary("using post gress q l database")
        assert "PostgreSQL" in result


class TestCompilePatterns:
    def test_sorts_by_length_descending(self):
        vocab = {"a": "A", "longer phrase": "LP"}
        patterns = vocabulary._compile_patterns(vocab)
        # Longer patterns should come first
        assert len(patterns) == 2

    def test_empty_vocab(self):
        patterns = vocabulary._compile_patterns({})
        assert patterns == []


class TestVocabFileManagement:
    def test_ensure_creates_dir(self, tmp_path):
        vocab_dir = tmp_path / "test_voice"
        vocab_file = vocab_dir / "vocabulary.json"
        with patch.object(vocabulary, 'VOICE_DATA_DIR', vocab_dir):
            with patch.object(vocabulary, 'VOCAB_FILE', vocab_file):
                path = vocabulary._ensure_vocab_file()
                assert path.exists()
                data = json.loads(path.read_text(encoding="utf-8"))
                assert isinstance(data, dict)
                assert len(data) > 0

    def test_load_merges_learned(self, tmp_path):
        vocab_dir = tmp_path / "test_voice2"
        vocab_dir.mkdir()
        vocab_file = vocab_dir / "vocabulary.json"
        learned_file = vocab_dir / "vocabulary_learned.json"

        vocab_file.write_text(json.dumps({"word1": "Word1"}), encoding="utf-8")
        learned_file.write_text(json.dumps({"word2": "Word2"}), encoding="utf-8")

        with patch.object(vocabulary, 'VOICE_DATA_DIR', vocab_dir):
            with patch.object(vocabulary, 'VOCAB_FILE', vocab_file):
                with patch.object(vocabulary, 'LEARNED_VOCAB_FILE', learned_file):
                    data = vocabulary._load_vocabulary()
                    assert "word1" in data
                    assert "word2" in data

    def test_user_vocab_takes_priority(self, tmp_path):
        vocab_dir = tmp_path / "test_voice3"
        vocab_dir.mkdir()
        vocab_file = vocab_dir / "vocabulary.json"
        learned_file = vocab_dir / "vocabulary_learned.json"

        vocab_file.write_text(json.dumps({"shared": "UserVersion"}), encoding="utf-8")
        learned_file.write_text(json.dumps({"shared": "LearnedVersion"}), encoding="utf-8")

        with patch.object(vocabulary, 'VOICE_DATA_DIR', vocab_dir):
            with patch.object(vocabulary, 'VOCAB_FILE', vocab_file):
                with patch.object(vocabulary, 'LEARNED_VOCAB_FILE', learned_file):
                    data = vocabulary._load_vocabulary()
                    assert data["shared"] == "UserVersion"


class TestGetEntries:
    def test_get_all_entries(self, tmp_path):
        vocab_dir = tmp_path / "test_entries"
        vocab_dir.mkdir()
        vocab_file = vocab_dir / "vocabulary.json"
        vocab_file.write_text(json.dumps({"a": "A"}), encoding="utf-8")

        with patch.object(vocabulary, 'VOICE_DATA_DIR', vocab_dir):
            with patch.object(vocabulary, 'VOCAB_FILE', vocab_file):
                with patch.object(vocabulary, 'LEARNED_VOCAB_FILE', vocab_dir / "none.json"):
                    entries = vocabulary.get_all_entries()
                    assert "a" in entries

    def test_get_learned_entries_empty(self, tmp_path):
        with patch.object(vocabulary, 'LEARNED_VOCAB_FILE', tmp_path / "nonexistent.json"):
            entries = vocabulary.get_learned_entries()
            assert entries == {}

    def test_get_learned_entries(self, tmp_path):
        learned_file = tmp_path / "vocabulary_learned.json"
        learned_file.write_text(json.dumps({"x": "Y"}), encoding="utf-8")
        with patch.object(vocabulary, 'LEARNED_VOCAB_FILE', learned_file):
            entries = vocabulary.get_learned_entries()
            assert entries == {"x": "Y"}
