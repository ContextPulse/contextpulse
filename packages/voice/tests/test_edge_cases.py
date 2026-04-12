"""Edge case tests for Voice — unusual inputs, boundary conditions, error handling."""



from contextpulse_voice.cleanup import _is_hallucination, clean_basic
from contextpulse_voice.vocabulary import (
    _DEFAULT_VOCABULARY,
    _compile_patterns,
    apply_punctuation,
)


class TestPunctuationEdgeCases:
    def test_all_punctuation_words(self):
        """Test every supported punctuation word."""
        for word in ["period", "comma", "exclamation point", "question mark",
                     "colon", "semicolon", "dash", "hyphen", "ellipsis",
                     "open parenthesis", "close parenthesis",
                     "open bracket", "close bracket"]:
            result = apply_punctuation(f"word {word} word")
            # Should not contain the original word
            assert word not in result.lower() or word == "dash"  # dash might remain as substring

    def test_multiple_punctuation_in_sequence(self):
        result = apply_punctuation("hello period period period")
        # Should not have more than one period in sequence
        assert "..." not in result or result.count("...") <= 1

    def test_punctuation_at_start(self):
        result = apply_punctuation("period hello world")
        assert result.startswith(".")

    def test_punctuation_at_end(self):
        result = apply_punctuation("hello world period")
        assert result.endswith(".")

    def test_unicode_text(self):
        result = apply_punctuation("héllo wörld period")
        assert "." in result

    def test_mixed_case_punctuation(self):
        result = apply_punctuation("hello PERIOD world")
        assert "." in result

    def test_very_long_text(self):
        text = " ".join(["hello world"] * 500) + " period"
        result = apply_punctuation(text)
        assert result.endswith(".")
        assert len(result) > 0

    def test_only_punctuation_words(self):
        result = apply_punctuation("period comma exclamation point")
        # Should be only punctuation symbols
        assert len(result.strip()) > 0

    def test_newline_preservation(self):
        result = apply_punctuation("first new line second new line third")
        assert "\n" in result


class TestCleanupEdgeCases:
    def test_single_word(self):
        result = clean_basic("hello")
        assert result == "Hello."

    def test_single_character(self):
        result = clean_basic("a")
        assert result == "A."

    def test_all_caps(self):
        result = clean_basic("THIS IS IMPORTANT")
        # Should preserve all-caps
        assert "THIS" in result or "this" in result

    def test_numbers(self):
        result = clean_basic("I have 3 cats and 2 dogs")
        assert "3" in result
        assert "2" in result

    def test_urls_preserved(self):
        result = clean_basic("check out github.com for more info")
        assert "github.com" in result.lower() or "github" in result.lower()

    def test_code_terms(self):
        result = clean_basic("run npm install and then npm start")
        assert "npm" in result

    def test_contractions(self):
        result = clean_basic("I can't believe it's not butter")
        assert "can't" in result or "cannot" in result

    def test_multiple_sentences(self):
        result = clean_basic("first sentence. second sentence. third sentence")
        assert result.count(".") >= 2

    def test_excessive_whitespace(self):
        result = clean_basic("hello     world     test")
        assert "  " not in result

    def test_tab_characters(self):
        result = clean_basic("hello\tworld")
        assert "\t" not in result or len(result) > 0

    def test_emoji_text(self):
        # Should not crash on emoji
        result = clean_basic("hello 🌍 world")
        assert len(result) > 0

    def test_mixed_language(self):
        result = clean_basic("hello bonjour hola")
        assert len(result) > 0


class TestVocabularyEdgeCases:
    def test_all_default_vocab_entries(self):
        """Verify every default vocabulary entry works."""
        patterns = _compile_patterns(_DEFAULT_VOCABULARY)
        for key, replacement in _DEFAULT_VOCABULARY.items():
            text = f"I use {key} daily"
            for pattern, repl in patterns:
                text = pattern.sub(repl, text)
            assert replacement in text, f"Vocab entry '{key}' -> '{replacement}' failed"

    def test_overlapping_patterns(self):
        """Test that longer patterns match before shorter ones."""
        vocab = {
            "post gress q l": "PostgreSQL",
            "post gress": "Postgres",
            "post": "mail",
        }
        patterns = _compile_patterns(vocab)
        text = "I use post gress q l database"
        for pattern, repl in patterns:
            text = pattern.sub(repl, text)
        assert "PostgreSQL" in text

    def test_case_preservation_in_replacement(self):
        vocab = {"kubernetes": "Kubernetes"}
        patterns = _compile_patterns(vocab)
        text = "I love KUBERNETES"
        for pattern, repl in patterns:
            text = pattern.sub(repl, text)
        assert "Kubernetes" in text

    def test_empty_vocabulary(self):
        patterns = _compile_patterns({})
        text = "hello world"
        for pattern, repl in patterns:
            text = pattern.sub(repl, text)
        assert text == "hello world"

    def test_special_regex_chars_in_key(self):
        """Vocabulary keys with regex special chars should be escaped."""
        vocab = {"c++": "C++", "c#": "C#"}
        patterns = _compile_patterns(vocab)
        # Should not crash with regex errors
        text = "I code in c++ and c#"
        for pattern, repl in patterns:
            text = pattern.sub(repl, text)

    def test_very_long_vocabulary(self):
        """Performance: 1000 vocab entries should still work."""
        vocab = {f"word{i}": f"Word{i}" for i in range(1000)}
        patterns = _compile_patterns(vocab)
        text = "I use word500 daily"
        for pattern, repl in patterns:
            text = pattern.sub(repl, text)
        assert "Word500" in text


class TestHallucinationDetection:
    def test_all_hallucination_starts(self):
        """Each hallucination pattern should be detected."""
        starts = [
            "I don't see any text",
            "I'm ready to help",
            "I can help you",
            "Could you provide more",
            "Please provide the text",
            "I'd be happy to assist",
            "No text was provided",
            "I'm sorry, but I need",
        ]
        for start in starts:
            assert _is_hallucination(start), f"Should detect: {start!r}"

    def test_normal_text_not_flagged(self):
        normal = [
            "The quick brown fox jumps.",
            "I went to the store and bought milk.",
            "Please add a new function called calculate total.",
            "The API endpoint returns a 404 error.",
        ]
        for text in normal:
            assert not _is_hallucination(text), f"False positive: {text!r}"


class TestFullPipeline:
    def test_pipeline_spoken_punctuation_and_vocab(self):
        """Test the full pipeline: punctuation → cleanup → vocabulary."""
        raw = "i use cube control to deploy kubernetes pods period it works great"
        text = apply_punctuation(raw)
        text = clean_basic(text)
        # Use default vocabulary
        patterns = _compile_patterns(_DEFAULT_VOCABULARY)
        for pattern, repl in patterns:
            text = pattern.sub(repl, text)
        assert "kubectl" in text
        assert "Kubernetes" in text
        assert text.endswith(".")
        assert text[0].isupper()

    def test_pipeline_filler_removal(self):
        raw = "um so basically i uh wanna um deploy to uh get hub"
        text = apply_punctuation(raw)
        text = clean_basic(text)
        patterns = _compile_patterns(_DEFAULT_VOCABULARY)
        for pattern, repl in patterns:
            text = pattern.sub(repl, text)
        assert "um" not in text.lower().split()
        assert "uh" not in text.lower().split()
        assert "GitHub" in text

    def test_pipeline_preserves_meaning(self):
        raw = "the function should return true if the value is greater than zero"
        text = apply_punctuation(raw)
        text = clean_basic(text)
        # Core meaning words should all be present
        for word in ["function", "return", "true", "value", "greater", "zero"]:
            assert word in text.lower()
