"""Internationalization (i18n) utilities for Immich Memories.

This module provides:
- Translation support using gettext
- Month name localization
- Ordinal formatting
- Language detection

Supported languages: English (en), French (fr)
Extensible to other languages by adding .po/.mo files.
"""

from __future__ import annotations

import gettext
import locale
import os
from functools import lru_cache
from pathlib import Path

# Supported locales
SUPPORTED_LOCALES = ["en", "fr"]
DEFAULT_LOCALE = "en"

# Path to locale files
LOCALES_DIR = Path(__file__).parent / "locales"


@lru_cache(maxsize=10)
def get_translator(locale_code: str) -> gettext.GNUTranslations | gettext.NullTranslations:
    """Get translator for specified locale.

    Args:
        locale_code: Language code (e.g., "en", "fr").

    Returns:
        GNUTranslations or NullTranslations object.
    """
    if locale_code not in SUPPORTED_LOCALES:
        locale_code = DEFAULT_LOCALE

    try:
        return gettext.translation(
            "messages",
            localedir=LOCALES_DIR,
            languages=[locale_code],
        )
    except FileNotFoundError:
        # Fallback to NullTranslations (returns original strings)
        return gettext.NullTranslations()


def _(message: str, locale_code: str = DEFAULT_LOCALE) -> str:
    """Translate a message.

    Args:
        message: Message to translate.
        locale_code: Target language code.

    Returns:
        Translated message.
    """
    return get_translator(locale_code).gettext(message)


def ngettext(
    singular: str,
    plural: str,
    n: int,
    locale_code: str = DEFAULT_LOCALE,
) -> str:
    """Translate with plural support.

    Args:
        singular: Singular form.
        plural: Plural form.
        n: Count for pluralization.
        locale_code: Target language code.

    Returns:
        Translated string.
    """
    return get_translator(locale_code).ngettext(singular, plural, n)


# Month names (fallback if .mo files not present)
_MONTH_NAMES: dict[str, list[str]] = {
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "fr": [
        "Janvier",
        "Février",
        "Mars",
        "Avril",
        "Mai",
        "Juin",
        "Juillet",
        "Août",
        "Septembre",
        "Octobre",
        "Novembre",
        "Décembre",
    ],
}


def get_month_name(month: int, locale_code: str = "en") -> str:
    """Get localized month name.

    Args:
        month: Month number (1-12).
        locale_code: Language code.

    Returns:
        Localized month name.

    Raises:
        ValueError: If month is not 1-12.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"Month must be 1-12, got {month}")

    # Try to get from translations
    month_key = f"month.{month}"
    translated = _(month_key, locale_code)

    # If translation returns the key, use fallback
    if translated == month_key:
        if locale_code not in _MONTH_NAMES:
            locale_code = "en"
        return _MONTH_NAMES[locale_code][month - 1]

    return translated


# Short month names
_SHORT_MONTH_NAMES: dict[str, list[str]] = {
    "en": [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ],
    "fr": [
        "Jan",
        "Fév",
        "Mar",
        "Avr",
        "Mai",
        "Juin",
        "Juil",
        "Août",
        "Sep",
        "Oct",
        "Nov",
        "Déc",
    ],
}


def get_short_month_name(month: int, locale_code: str = "en") -> str:
    """Get localized short month name.

    Args:
        month: Month number (1-12).
        locale_code: Language code.

    Returns:
        Localized short month name.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"Month must be 1-12, got {month}")

    if locale_code not in _SHORT_MONTH_NAMES:
        locale_code = "en"

    return _SHORT_MONTH_NAMES[locale_code][month - 1]


def get_ordinal(n: int, locale_code: str = "en") -> str:
    """Get localized ordinal string for a number.

    Args:
        n: The number to convert to ordinal.
        locale_code: Language code.

    Returns:
        Ordinal string (e.g., "1st", "2nd", "1ère", "2ème").
    """
    if locale_code == "en":
        # English ordinals
        suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    elif locale_code == "fr":
        # French ordinals
        if n == 1:
            return "1ère"
        return f"{n}ème"

    else:
        # Fallback to English
        return get_ordinal(n, "en")


def detect_system_locale() -> str:
    """Detect the system's preferred locale.

    Returns:
        Two-letter language code.
    """
    try:
        # Try to get system locale
        sys_locale = locale.getdefaultlocale()[0]
        if sys_locale:
            lang = sys_locale.split("_")[0].lower()
            if lang in SUPPORTED_LOCALES:
                return lang
    except Exception:
        pass

    # Check LANG environment variable
    lang_env = os.environ.get("LANG", "")
    if lang_env:
        lang = lang_env.split("_")[0].lower()
        if lang in SUPPORTED_LOCALES:
            return lang

    return DEFAULT_LOCALE


def format_date_range(
    start_month: int,
    start_year: int,
    end_month: int,
    end_year: int,
    locale_code: str = "en",
) -> str:
    """Format a date range for display.

    Args:
        start_month: Starting month (1-12).
        start_year: Starting year.
        end_month: Ending month (1-12).
        end_year: Ending year.
        locale_code: Language code.

    Returns:
        Formatted date range string.
    """
    start_month_name = get_month_name(start_month, locale_code)
    end_month_name = get_month_name(end_month, locale_code)

    if start_year == end_year:
        if start_month == end_month:
            return f"{start_month_name} {start_year}"

        # Same year, different months
        if locale_code == "fr":
            return f"{start_month_name} à {end_month_name} {start_year}"
        else:
            return f"{start_month_name} to {end_month_name} {start_year}"

    else:
        # Different years
        if locale_code == "fr":
            return f"{start_month_name} {start_year} à {end_month_name} {end_year}"
        else:
            return f"{start_month_name} {start_year} to {end_month_name} {end_year}"


def format_year_title(year: int, locale_code: str = "en") -> str:
    """Format a year for title display.

    Args:
        year: The year.
        locale_code: Language code.

    Returns:
        Formatted year string.
    """
    return str(year)


def format_birthday_title(
    age: int,
    person_name: str | None = None,
    locale_code: str = "en",
) -> tuple[str, str | None]:
    """Format a birthday year title.

    Args:
        age: The birthday age (1 = first birthday).
        person_name: Optional person's name.
        locale_code: Language code.

    Returns:
        Tuple of (main_title, subtitle).
    """
    ordinal = get_ordinal(age, locale_code)
    main_title = f"{ordinal} Année" if locale_code == "fr" else f"{ordinal} Year"

    return main_title, person_name


class Translator:
    """Context-aware translator for a specific locale."""

    def __init__(self, locale_code: str = DEFAULT_LOCALE):
        """Initialize translator.

        Args:
            locale_code: Language code to use.
        """
        if locale_code not in SUPPORTED_LOCALES:
            locale_code = DEFAULT_LOCALE
        self.locale = locale_code
        self._translator = get_translator(locale_code)

    def __call__(self, message: str) -> str:
        """Translate a message.

        Args:
            message: Message to translate.

        Returns:
            Translated message.
        """
        return self._translator.gettext(message)

    def month(self, month: int) -> str:
        """Get month name.

        Args:
            month: Month number (1-12).

        Returns:
            Localized month name.
        """
        return get_month_name(month, self.locale)

    def ordinal(self, n: int) -> str:
        """Get ordinal string.

        Args:
            n: Number.

        Returns:
            Ordinal string.
        """
        return get_ordinal(n, self.locale)

    def date_range(
        self,
        start_month: int,
        start_year: int,
        end_month: int,
        end_year: int,
    ) -> str:
        """Format a date range.

        Args:
            start_month: Starting month.
            start_year: Starting year.
            end_month: Ending month.
            end_year: Ending year.

        Returns:
            Formatted date range.
        """
        return format_date_range(
            start_month,
            start_year,
            end_month,
            end_year,
            self.locale,
        )
