"""Tests for i18n — locale detection, month names, date formatting, ordinals."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from immich_memories.i18n import (
    _,
    detect_system_locale,
    get_month_name,
    get_ordinal,
    ngettext,
)

# ---------------------------------------------------------------------------
# get_month_name
# ---------------------------------------------------------------------------


class TestGetMonthName:
    def test_english_months(self):
        assert get_month_name(1, "en") == "January"
        assert get_month_name(6, "en") == "June"
        assert get_month_name(12, "en") == "December"

    def test_french_months(self):
        assert get_month_name(1, "fr") == "Janvier"
        assert get_month_name(8, "fr") == "Août"
        assert get_month_name(12, "fr") == "Décembre"

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError, match="Month must be 1-12"):
            get_month_name(0, "en")
        with pytest.raises(ValueError, match="Month must be 1-12"):
            get_month_name(13, "en")

    def test_unsupported_locale_falls_back_to_english(self):
        assert get_month_name(3, "de") == "March"


# ---------------------------------------------------------------------------
# get_ordinal
# ---------------------------------------------------------------------------


class TestGetOrdinal:
    def test_english_ordinals(self):
        assert get_ordinal(1, "en") == "1st"
        assert get_ordinal(2, "en") == "2nd"
        assert get_ordinal(3, "en") == "3rd"
        assert get_ordinal(4, "en") == "4th"
        assert get_ordinal(11, "en") == "11th"
        assert get_ordinal(12, "en") == "12th"
        assert get_ordinal(13, "en") == "13th"
        assert get_ordinal(21, "en") == "21st"
        assert get_ordinal(22, "en") == "22nd"
        assert get_ordinal(23, "en") == "23rd"
        assert get_ordinal(100, "en") == "100th"
        assert get_ordinal(101, "en") == "101st"
        assert get_ordinal(111, "en") == "111th"

    def test_french_ordinals(self):
        assert get_ordinal(1, "fr") == "1ère"
        assert get_ordinal(2, "fr") == "2ème"
        assert get_ordinal(10, "fr") == "10ème"

    def test_unsupported_locale_falls_back_to_english(self):
        assert get_ordinal(1, "de") == "1st"


# ---------------------------------------------------------------------------
# detect_system_locale
# ---------------------------------------------------------------------------


class TestDetectSystemLocale:
    def test_returns_supported_locale(self):
        result = detect_system_locale()
        assert result in ("en", "fr")

    def test_respects_lang_env(self):
        with (
            patch.dict(os.environ, {"LANG": "fr_FR.UTF-8"}),
            # WHY: mock getdefaultlocale to return None so LANG env is used
            patch("immich_memories.i18n.locale.getdefaultlocale", return_value=(None, None)),
        ):
            assert detect_system_locale() == "fr"

    def test_unsupported_lang_falls_back(self):
        with (
            patch.dict(os.environ, {"LANG": "zh_CN.UTF-8"}),
            patch("immich_memories.i18n.locale.getdefaultlocale", return_value=(None, None)),
        ):
            assert detect_system_locale() == "en"


# ---------------------------------------------------------------------------
# ngettext
# ---------------------------------------------------------------------------


class TestNgettext:
    def test_singular_and_plural(self):
        # NullTranslations returns singular for n=1, plural otherwise
        assert ngettext("item", "items", 1) == "item"
        assert ngettext("item", "items", 2) == "items"
        assert ngettext("item", "items", 0) == "items"


# ---------------------------------------------------------------------------
# _ translation function
# ---------------------------------------------------------------------------


class TestTranslateFunction:
    def test_returns_string(self):
        # NullTranslations returns the original string
        result = _("hello", "en")
        assert result == "hello"
