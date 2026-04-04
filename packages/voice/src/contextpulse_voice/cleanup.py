# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Text cleanup module — polishes raw transcription before pasting.

LLM-based transcript cleanup (direct API only).
"""

import logging
import re

logger = logging.getLogger(__name__)

# Filler words/sounds to strip
FILLERS = re.compile(
    r"\b(uh|um|uhm|erm|hmm|hm|like,?\s*(?=like)|you know,?\s*(?=you know)|"
    r"so,?\s*(?=so)|basically|actually,?\s*(?=actually)|literally)\b",
    re.IGNORECASE,
)

_HALLUCINATION_STARTS = (
    "I don't see any text",
    "I'm ready to",
    "I can help",
    "Could you provide",
    "Could you please",
    "There doesn't seem",
    "I notice you",
    "It seems like you",
    "Please provide",
    "Please share",
    "However, I don't",
    "However, I can't",
    "I'd be happy to",
    "I don't have",
    "No text was provided",
    "The text appears to be empty",
    "You haven't provided",
    "I'm sorry, but",
)


def _capitalize_after_punctuation(text: str) -> str:
    """Capitalize the first letter after sentence-ending punctuation."""
    def _cap(m: re.Match) -> str:
        return m.group(1) + m.group(2).upper()
    return re.sub(r'([.!?]\s+)([a-z])', _cap, text)


def _fix_common_phrases(text: str) -> str:
    """Fix common speech-to-text artifacts and awkward phrasing."""
    _SAFE_DEDUP = {"the", "a", "an", "is", "in", "on", "to", "of", "and", "it", "we", "he", "she"}
    for word in _SAFE_DEDUP:
        text = re.sub(r'\b(' + re.escape(word) + r')\s+\1\b', r'\1', text, flags=re.IGNORECASE)

    replacements = [
        (r'\bgonna\b', 'going to'),
        (r'\bwanna\b', 'want to'),
        (r'\bgotta\b', 'got to'),
        (r'\bkinda\b', 'kind of'),
        (r'\bsorta\b', 'sort of'),
        (r'\blemme\b', 'let me'),
        (r'\bgimme\b', 'give me'),
        (r'\bdunno\b', "don't know"),
        (r"\bcuz\b", "because"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    return text


def _fix_spacing_around_punctuation(text: str) -> str:
    """Remove spaces before punctuation, ensure space after."""
    text = re.sub(r'\s+([,;:.!?])', r'\1', text)
    text = re.sub(r'([,;:])([A-Za-z])', r'\1 \2', text)
    return text


def _fix_i_capitalization(text: str) -> str:
    """Capitalize standalone 'i' -> 'I'."""
    return re.sub(r'\bi\b', 'I', text)


def _fix_title_case(text: str) -> str:
    """Fix Whisper's random title-casing mid-sentence."""
    words = text.split()
    if len(words) <= 2:
        return text

    mid_words = words[1:]
    title_cased = sum(
        1 for w in mid_words
        if w and len(w) > 1 and w[0].isupper() and not w.isupper()
    )
    total_alpha = sum(1 for w in mid_words if w and w[0].isalpha() and len(w) > 1)

    if total_alpha < 3 or title_cased / total_alpha < 0.4:
        return text

    result = [words[0]]
    prev_ends_sentence = False
    for word in words[1:]:
        if prev_ends_sentence:
            result.append(word)
        elif word == 'I':
            result.append(word)
        elif word and len(word) > 1 and word[0].isupper() and not word.isupper():
            result.append(word.lower())
        else:
            result.append(word)
        prev_ends_sentence = word and word[-1] in '.!?'

    return ' '.join(result)


def _is_hallucination(text: str) -> bool:
    """Check if LLM response is conversational instead of cleaned text."""
    lower = text.lower()
    if any(lower.startswith(p.lower()) for p in _HALLUCINATION_STARTS):
        return True
    if "voice dictation" in lower and "clean up" in lower:
        return True
    return False


def clean_basic(text: str) -> str:
    """Fast, rule-based cleanup — always runs, no API needed."""
    if not text:
        return text

    text = FILLERS.sub("", text)
    text = _fix_title_case(text)
    text = _fix_common_phrases(text)
    text = _fix_i_capitalization(text)
    text = _fix_spacing_around_punctuation(text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    if text:
        text = text[0].upper() + text[1:]
    text = _capitalize_after_punctuation(text)

    if text and text[-1] not in ".!?":
        text += "."

    return text


def clean_with_llm(text: str, profile_context: str = "") -> str:
    """Polish text using Claude API for natural, professional output."""
    from contextpulse_voice.config import get_api_key
    api_key = get_api_key()
    if not api_key:
        logger.debug("No API key configured — skipping LLM cleanup")
        return text

    try:
        import anthropic

        context_section = ""
        if profile_context:
            context_section = (
                f"\n\nContext (use for proper noun spelling): {profile_context}"
            )

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Clean up this voice dictation. Fix grammar, punctuation, and "
                        "remove filler words. Keep the original meaning and tone. "
                        "Use the context below for proper noun capitalization and spelling. "
                        "Do NOT add any commentary — return ONLY the cleaned text."
                        f"{context_section}\n\n"
                        f"Dictation: {text}"
                    ),
                }
            ],
        )
        cleaned = response.content[0].text.strip()

        if _is_hallucination(cleaned):
            logger.warning("LLM returned conversational response — ignoring")
            return text
        if len(cleaned) > len(text) * 3 and len(text) > 10:
            logger.warning("LLM response suspiciously long — ignoring")
            return text
        if not cleaned:
            logger.warning("LLM returned empty response — ignoring")
            return text
        logger.info("LLM cleanup: '%s' -> '%s'", text[:50], cleaned[:50])
        return cleaned
    except Exception:
        logger.exception("LLM cleanup failed — using basic cleanup")
        return text


def clean(text: str, use_llm: bool = False, profile_context: str = "") -> str:
    """Run cleanup pipeline: basic rules first, then optional LLM polish."""
    text = clean_basic(text)
    if use_llm:
        text = clean_with_llm(text, profile_context=profile_context)
    return text
