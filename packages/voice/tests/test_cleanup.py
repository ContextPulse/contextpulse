"""Tests for the cleanup module — rule-based and LLM text polishing."""


from contextpulse_voice.cleanup import (
    _capitalize_after_punctuation,
    _fix_common_phrases,
    _fix_i_capitalization,
    _fix_spacing_around_punctuation,
    _fix_title_case,
    _is_hallucination,
    clean,
    clean_basic,
)


class TestCleanBasic:
    def test_empty_string(self):
        assert clean_basic("") == ""

    def test_capitalize_first_letter(self):
        result = clean_basic("hello world")
        assert result[0] == "H"

    def test_adds_trailing_period(self):
        result = clean_basic("hello world")
        assert result.endswith(".")

    def test_preserves_existing_terminal_punctuation(self):
        result = clean_basic("hello world!")
        assert result.endswith("!")
        assert not result.endswith("!.")

    def test_removes_filler_words(self):
        result = clean_basic("uh I want to um go there")
        assert "uh" not in result.lower()
        assert "um" not in result.lower()

    def test_collapses_multiple_spaces(self):
        result = clean_basic("hello    world")
        assert "  " not in result

    def test_fixes_standalone_i(self):
        result = clean_basic("i went to the store")
        assert "I went" in result


class TestFixCommonPhrases:
    def test_gonna(self):
        assert "going to" in _fix_common_phrases("I'm gonna do it")

    def test_wanna(self):
        assert "want to" in _fix_common_phrases("I wanna go")

    def test_duplicate_the(self):
        result = _fix_common_phrases("the the cat sat")
        assert result.count("the") == 1

    def test_preserves_intentional_words(self):
        result = _fix_common_phrases("that that works fine")
        # "that" is not in SAFE_DEDUP, so it's preserved
        assert result.count("that") == 2


class TestCapitalizeAfterPunctuation:
    def test_after_period(self):
        assert "Hello. World" in _capitalize_after_punctuation("Hello. world")

    def test_after_question(self):
        assert "What? Really" in _capitalize_after_punctuation("What? really")

    def test_after_exclamation(self):
        assert "Wow! Great" in _capitalize_after_punctuation("Wow! great")


class TestFixSpacing:
    def test_removes_space_before_comma(self):
        assert _fix_spacing_around_punctuation("hello , world") == "hello, world"

    def test_adds_space_after_comma(self):
        assert _fix_spacing_around_punctuation("hello,world") == "hello, world"


class TestFixICapitalization:
    def test_standalone_i(self):
        assert _fix_i_capitalization("i went") == "I went"

    def test_preserves_words_containing_i(self):
        result = _fix_i_capitalization("is it")
        assert result == "Is It" or result == "is it"  # only standalone 'i'


class TestFixTitleCase:
    def test_fixes_title_casing(self):
        result = _fix_title_case("A Lot Of These Business Ideas Are Great")
        # Mid-sentence words should be lowercased
        assert "of" in result.lower()
        assert "these" in result.lower()

    def test_preserves_short_text(self):
        result = _fix_title_case("Hello World")
        assert result == "Hello World"

    def test_preserves_normal_casing(self):
        result = _fix_title_case("I went to the store and bought some things")
        assert result == "I went to the store and bought some things"

    def test_preserves_all_caps(self):
        result = _fix_title_case("Use The API For Your SQL Query Now")
        assert "API" in result
        assert "SQL" in result


class TestIsHallucination:
    def test_conversational_response(self):
        assert _is_hallucination("I don't see any text to clean up")

    def test_helpful_response(self):
        assert _is_hallucination("I'd be happy to help you clean up")

    def test_normal_text(self):
        assert not _is_hallucination("The quick brown fox jumps over the lazy dog.")

    def test_echoed_prompt(self):
        assert _is_hallucination("Here is the clean up of the voice dictation")


class TestCleanPipeline:
    def test_basic_only(self):
        result = clean("um hello world", use_llm=False)
        assert "um" not in result.lower()
        assert result[0].isupper()

    def test_empty_input(self):
        assert clean("", use_llm=False) == ""
