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

import contextlib
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


WEEKDAY_NAMES = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr": ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"],
}


def get_weekday_name(weekday: int, locale_code: str = "en") -> str:
    """Localized weekday name; ``weekday`` follows ``date.weekday()`` (0=Monday)."""
    if not 0 <= weekday <= 6:
        raise ValueError(f"Weekday must be 0-6, got {weekday}")
    names = WEEKDAY_NAMES.get(locale_code, WEEKDAY_NAMES[DEFAULT_LOCALE])
    return names[weekday]


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

    # Fallback to English
    return get_ordinal(n, "en")


def detect_system_locale() -> str:
    """Detect the system's preferred locale.

    Returns:
        Two-letter language code.
    """
    with contextlib.suppress(Exception):
        # Try to get system locale
        sys_locale = locale.getdefaultlocale()[0]
        if sys_locale:
            lang = sys_locale.split("_")[0].lower()
            if lang in SUPPORTED_LOCALES:
                return lang

    # Check LANG environment variable
    lang_env = os.environ.get("LANG", "")
    if lang_env:
        lang = lang_env.split("_")[0].lower()
        if lang in SUPPORTED_LOCALES:
            return lang

    return DEFAULT_LOCALE
