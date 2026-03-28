"""Tests for classifier.py — OCR classification logic."""

from unittest.mock import MagicMock, patch

from PIL import Image


def _make_image(width=100, height=100):
    return Image.new("RGB", (width, height), (128, 128, 128))


class TestClassifyAndExtract:
    """Test the classify_and_extract function with mocked OCR."""

    def test_no_ocr_results_returns_image_type(self):
        mock_ocr = MagicMock()
        mock_ocr.return_value = (None, None)

        with patch("contextpulse_sight.classifier._get_ocr", return_value=mock_ocr):
            from contextpulse_sight.classifier import classify_and_extract
            result = classify_and_extract(_make_image())

        assert result["type"] == "image"
        assert result["text"] is None
        assert result["lines"] == 0
        assert result["chars"] == 0
        assert result["confidence"] == 0.0
        assert result["ocr_time"] >= 0

    def test_empty_results_returns_image_type(self):
        mock_ocr = MagicMock()
        mock_ocr.return_value = ([], None)

        with patch("contextpulse_sight.classifier._get_ocr", return_value=mock_ocr):
            from contextpulse_sight.classifier import classify_and_extract
            result = classify_and_extract(_make_image())

        assert result["type"] == "image"

    def test_high_confidence_text_returns_text_type(self):
        # Simulate OCR finding lots of text with high confidence
        ocr_results = [
            (None, "def hello_world():", 0.95),
            (None, "    print('Hello, world!')", 0.92),
            (None, "    return True", 0.90),
            (None, "# This is a comment that makes it over 100 chars total with all the other lines combined here", 0.88),
        ]
        mock_ocr = MagicMock()
        mock_ocr.return_value = (ocr_results, None)

        with patch("contextpulse_sight.classifier._get_ocr", return_value=mock_ocr):
            from contextpulse_sight.classifier import classify_and_extract
            result = classify_and_extract(_make_image())

        assert result["type"] == "text"
        assert result["text"] is not None
        assert "hello_world" in result["text"]
        assert result["lines"] == 4
        assert result["confidence"] > 0.7

    def test_low_confidence_returns_image_type(self):
        # Low confidence OCR
        ocr_results = [
            (None, "abc" * 50, 0.3),
            (None, "def" * 50, 0.2),
        ]
        mock_ocr = MagicMock()
        mock_ocr.return_value = (ocr_results, None)

        with patch("contextpulse_sight.classifier._get_ocr", return_value=mock_ocr):
            from contextpulse_sight.classifier import classify_and_extract
            result = classify_and_extract(_make_image())

        assert result["type"] == "image"
        assert result["text"] is None

    def test_few_chars_returns_image_type(self):
        # High confidence but very little text (< 100 chars)
        ocr_results = [
            (None, "OK", 0.99),
            (None, "Cancel", 0.98),
        ]
        mock_ocr = MagicMock()
        mock_ocr.return_value = (ocr_results, None)

        with patch("contextpulse_sight.classifier._get_ocr", return_value=mock_ocr):
            from contextpulse_sight.classifier import classify_and_extract
            result = classify_and_extract(_make_image())

        assert result["type"] == "image"
        assert result["chars"] < 100


class TestOCRLazyInit:
    """Test the lazy OCR engine initialization."""

    def test_get_ocr_returns_instance(self):
        mock_rapid = MagicMock()
        with patch("contextpulse_sight.classifier.RapidOCR", mock_rapid), \
             patch("contextpulse_sight.classifier._ocr", None):
            from contextpulse_sight.classifier import _get_ocr
            result = _get_ocr()
            mock_rapid.assert_called_once()

    def test_get_ocr_cached(self):
        sentinel = object()
        with patch("contextpulse_sight.classifier._ocr", sentinel):
            from contextpulse_sight.classifier import _get_ocr
            result = _get_ocr()
            assert result is sentinel
