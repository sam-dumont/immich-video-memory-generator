"""The words a film prints, in the film's language.

Dates, month and weekday names come from CLDR through Babel. Everything else a
title, a trip card or a holiday label says is a template in
`locales/<code>/LC_MESSAGES/messages.po`, read with Babel at run time (no
compiled .mo files). A key a catalogue lacks falls back to English.

The English and French catalogues are written by hand. The others were drafted
by an AI and say so in their headers; a native speaker's correction is welcome.
"""

from __future__ import annotations

import contextlib
import gettext
import locale
import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from babel import Locale
from babel.dates import get_day_names, get_month_names

# Film-text languages, as the config spells them.
SUPPORTED_LOCALES = [
    "en",
    "fr",
    "nl",
    "de",
    "es",
    "it",
    "pt-BR",
    "pt-PT",
    "pl",
    "sv",
    "ru",
    "ja",
    "zh-Hans",
    "ko",
]
DEFAULT_LOCALE = "en"

# Scripts without letter case, whose month names read best as numbers.
_CJK_LOCALES = frozenset({"ja", "zh-Hans", "ko"})
# What a system locale says, mapped to a film language ("pt" alone is Portugal's).
_SYSTEM_ALIASES = {
    "pt_br": "pt-BR",
    "pt": "pt-PT",
    "zh_cn": "zh-Hans",
    "zh_sg": "zh-Hans",
    "zh": "zh-Hans",
}

LOCALES_DIR = Path(__file__).parent / "locales"


def babel_locale(code: str) -> Locale:
    """The Babel locale for a film language ("pt-BR" -> pt_BR); English when unknown."""
    try:
        return Locale.parse(code.replace("-", "_"))
    except (ValueError, TypeError):
        return Locale.parse(DEFAULT_LOCALE)


def _supported(code: str) -> str:
    return code if code in SUPPORTED_LOCALES else DEFAULT_LOCALE


@lru_cache(maxsize=32)
def _catalogue(code: str) -> tuple[dict[str, object], Callable[[int], int]]:
    """msgid -> msgstr (a tuple of plural forms for a plural message), and the plural rule."""
    from babel.messages.pofile import read_po

    path = LOCALES_DIR / code.replace("-", "_") / "LC_MESSAGES" / "messages.po"
    if not path.is_file():
        return {}, lambda n: int(n != 1)
    with path.open("rb") as handle:
        catalogue = read_po(handle, locale=code.replace("-", "_"))
    strings: dict[str, object] = {}
    for message in catalogue:
        key = str(message.id[0] if isinstance(message.id, (tuple, list)) else message.id)
        if key and message.string:
            strings[key] = message.string
    return strings, gettext.c2py(catalogue.plural_expr)


def film_text(key: str, locale_code: str = DEFAULT_LOCALE, **values: object) -> str:
    """The catalogue's template for `key` in this language, filled with `values`.

    A key the language lacks takes the English template, so a half-translated
    catalogue still prints a whole title.
    """
    template = _catalogue(_supported(locale_code))[0].get(key) or _catalogue(DEFAULT_LOCALE)[0].get(
        key, key
    )
    if isinstance(template, tuple):
        template = template[0]
    return str(template).format(**values)


def film_text_n(key: str, n: int, locale_code: str = DEFAULT_LOCALE, **values: object) -> str:
    """A plural template: the form this language uses for `n`, filled with `n` and `values`."""
    strings, plural = _catalogue(_supported(locale_code))
    forms = strings.get(key)
    if not isinstance(forms, tuple):
        strings, plural = _catalogue(DEFAULT_LOCALE)
        forms = strings.get(key, (key,))
    assert isinstance(forms, tuple)
    form = forms[min(plural(n), len(forms) - 1)]
    return str(form).format(n=n, **values)


@lru_cache(maxsize=10)
def get_translator(locale_code: str) -> gettext.GNUTranslations | gettext.NullTranslations:
    """A gettext translator for compiled catalogues, if any are installed."""
    try:
        return gettext.translation(
            "messages", localedir=LOCALES_DIR, languages=[_supported(locale_code)]
        )
    except FileNotFoundError:
        return gettext.NullTranslations()


def _(message: str, locale_code: str = DEFAULT_LOCALE) -> str:
    """Translate a message through gettext."""
    return get_translator(locale_code).gettext(message)


def ngettext(
    singular: str,
    plural: str,
    n: int,
    locale_code: str = DEFAULT_LOCALE,
) -> str:
    """Translate with plural support through gettext."""
    return get_translator(locale_code).ngettext(singular, plural, n)


def _capitalised(word: str) -> str:
    return word[:1].upper() + word[1:]


def get_weekday_name(weekday: int, locale_code: str = "en") -> str:
    """Localized weekday name; ``weekday`` follows ``date.weekday()`` (0=Monday)."""
    if not 0 <= weekday <= 6:
        raise ValueError(f"Weekday must be 0-6, got {weekday}")
    names = get_day_names("wide", "format", babel_locale(_supported(locale_code)))
    return _capitalised(names[weekday])


def month_name_forms(month: int, locale_code: str = "en") -> dict[str, str]:
    """Every spelling of a month a template may want.

    `month` is the standalone name capitalised for the start of a title
    ("Juillet"), `month_lc` the same in lower case for the middle of one
    ("juillet"), `month_of` the form a day number takes ("14 lipca" in Polish,
    "14 июля" in Russian), `month_num` the number (CJK titles write "7月").
    """
    if not 1 <= month <= 12:
        raise ValueError(f"Month must be 1-12, got {month}")
    code = _supported(locale_code)
    where = babel_locale(code)
    if code in _CJK_LOCALES:
        standalone = formatted = get_month_names("abbreviated", "format", where)[month]
    else:
        standalone = get_month_names("wide", "stand-alone", where)[month]
        formatted = get_month_names("wide", "format", where)[month]
    return {
        "month": _capitalised(standalone),
        "month_lc": standalone,
        "month_of": formatted,
        "month_num": str(month),
    }


def get_month_name(month: int, locale_code: str = "en") -> str:
    """The month's standalone name, capitalised for a title ("Juillet", "Lipiec", "7月")."""
    return month_name_forms(month, locale_code)["month"]


def get_ordinal(n: int, locale_code: str = "en") -> str:
    """An ordinal number as a title prints it: "1st", "1ère", "1.", "1º".

    English picks its suffix from CLDR's ordinal plural rule; every other
    language has a template in its catalogue, with an optional `ordinal.1`
    for a first that is spelled differently.
    """
    code = _supported(locale_code)
    if code == DEFAULT_LOCALE:
        suffix = {"one": "st", "two": "nd", "few": "rd"}.get(
            babel_locale(code).ordinal_form(n), "th"
        )
        return f"{n}{suffix}"
    strings = _catalogue(code)[0]
    if n == 1 and "ordinal.1" in strings:
        return str(strings["ordinal.1"])
    return film_text("ordinal", code, n=n)


def detect_system_locale() -> str:
    """The film language this host's own locale asks for, or English."""
    candidates: list[str] = []
    with contextlib.suppress(Exception):
        sys_locale = locale.getdefaultlocale()[0]
        if sys_locale:
            candidates.append(sys_locale)
    candidates.append(os.environ.get("LANG", "").split(".")[0])
    for candidate in candidates:
        folded = candidate.casefold()
        for key in (folded, folded.split("_")[0]):
            code = _SYSTEM_ALIASES.get(key) or next(
                (c for c in SUPPORTED_LOCALES if c.casefold() == key), None
            )
            if code:
                return code
    return DEFAULT_LOCALE
