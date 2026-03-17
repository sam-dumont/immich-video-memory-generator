"""Tests for i18n — locale detection, month names, date formatting, ordinals."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from immich_memories.i18n import (
    Translator,
    _,
    detect_system_locale,
    format_birthday_title,
    format_date_range,
    format_year_title,
    get_month_name,
    get_ordinal,
    get_short_month_name,
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
# get_short_month_name
# ---------------------------------------------------------------------------


class TestGetShortMonthName:
    def test_english_short(self):
        assert get_short_month_name(1, "en") == "Jan"
        assert get_short_month_name(9, "en") == "Sep"

    def test_french_short(self):
        assert get_short_month_name(2, "fr") == "Fév"
        assert get_short_month_name(7, "fr") == "Juil"

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError):
            get_short_month_name(0, "en")

    def test_unsupported_locale_falls_back(self):
        assert get_short_month_name(5, "de") == "May"


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
# format_date_range
# ---------------------------------------------------------------------------


class TestFormatDateRange:
    def test_same_month_same_year(self):
        assert format_date_range(6, 2024, 6, 2024, "en") == "June 2024"

    def test_different_months_same_year_en(self):
        assert format_date_range(3, 2024, 8, 2024, "en") == "March to August 2024"

    def test_different_months_same_year_fr(self):
        assert format_date_range(3, 2024, 8, 2024, "fr") == "Mars à Août 2024"

    def test_different_years_en(self):
        result = format_date_range(11, 2023, 2, 2024, "en")
        assert result == "November 2023 to February 2024"

    def test_different_years_fr(self):
        result = format_date_range(11, 2023, 2, 2024, "fr")
        assert result == "Novembre 2023 à Février 2024"


# ---------------------------------------------------------------------------
# format_year_title
# ---------------------------------------------------------------------------


class TestFormatYearTitle:
    def test_returns_year_string(self):
        assert format_year_title(2024) == "2024"
        assert format_year_title(1999, "fr") == "1999"


# ---------------------------------------------------------------------------
# format_birthday_title
# ---------------------------------------------------------------------------


class TestFormatBirthdayTitle:
    def test_english_birthday(self):
        title, subtitle = format_birthday_title(1, "Alice", "en")
        assert title == "1st Year"
        assert subtitle == "Alice"

    def test_french_birthday(self):
        title, subtitle = format_birthday_title(1, "Alice", "fr")
        assert title == "1ère Année"
        assert subtitle == "Alice"

    def test_no_name(self):
        title, subtitle = format_birthday_title(3, None, "en")
        assert title == "3rd Year"
        assert subtitle is None


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
# Translator class
# ---------------------------------------------------------------------------


class TestTranslator:
    def test_month_delegation(self):
        t = Translator("en")
        assert t.month(1) == "January"

    def test_ordinal_delegation(self):
        t = Translator("en")
        assert t.ordinal(2) == "2nd"

    def test_date_range_delegation(self):
        t = Translator("en")
        assert t.date_range(1, 2024, 3, 2024) == "January to March 2024"

    def test_unsupported_locale_falls_back(self):
        t = Translator("zz")
        assert t.locale == "en"

    def test_callable_translates(self):
        t = Translator("en")
        # NullTranslations returns the original string
        assert t("hello") == "hello"


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
