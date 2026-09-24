"""Every film language prints whole titles in its own words (#1101)."""

from __future__ import annotations

import re
import string
from datetime import date
from pathlib import Path

import pytest

from immich_memories.i18n import (
    LOCALES_DIR,
    SUPPORTED_LOCALES,
    film_text,
    film_text_n,
    get_month_name,
    get_ordinal,
)
from immich_memories.memory_types.factory import holiday_label
from immich_memories.titles._trip_titles import _get_duration_label, _get_time_label
from immich_memories.titles.text_builder import SelectionType, generate_title

HAND_WRITTEN = {"en", "fr"}


def _po(locale: str) -> str:
    return (LOCALES_DIR / locale.replace("-", "_") / "LC_MESSAGES" / "messages.po").read_text(
        encoding="utf-8"
    )


def _keys(locale: str) -> set[str]:
    return set(re.findall(r'^msgid "([^"]+)"', _po(locale), flags=re.MULTILINE))


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_every_language_has_a_catalogue_with_every_english_key(locale: str) -> None:
    missing = _keys("en") - _keys(locale) - {"ordinal"}

    assert missing == set()


@pytest.mark.parametrize("locale", sorted(set(SUPPORTED_LOCALES) - HAND_WRITTEN))
def test_a_machine_drafted_catalogue_says_so(locale: str) -> None:
    assert "AI-DRAFTED" in _po(locale)


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_no_template_asks_for_a_value_the_code_does_not_give(locale: str) -> None:
    given = {"season", "year", "start_year", "end_year", "day", "person", "ordinal", "n"}
    for prefix in ("", "start_", "end_"):
        given |= {f"{prefix}month", f"{prefix}month_lc", f"{prefix}month_of", f"{prefix}month_num"}
    for template in re.findall(r'^msgstr(?:\[\d\])? "(.*)"$', _po(locale), flags=re.MULTILINE):
        assert _placeholders(template) <= given, template


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_no_title_leaks_a_catalogue_key(locale: str) -> None:
    titles = [
        generate_title(SelectionType.BIRTHDAY_YEAR, birthday_age=3, locale=locale).main_title,
        generate_title(SelectionType.SINGLE_MONTH, month=7, year=2024, locale=locale).main_title,
        generate_title(
            SelectionType.DATE_RANGE,
            start_date=date(2024, 3, 1),
            end_date=date(2025, 5, 31),
            locale=locale,
        ).main_title,
        generate_title(SelectionType.SEASON, season="fall", year=2024, locale=locale).main_title,
        generate_title(
            SelectionType.ON_THIS_DAY, start_date=date(2020, 7, 14), locale=locale
        ).subtitle,
        _get_duration_label(5, locale),
        _get_time_label(date(2024, 6, 20), date(2024, 7, 3), locale),
        holiday_label("christmas", 2024, locale),
    ]

    assert all(titles)
    assert not any(re.search(r"\b(title|trip|season|holiday)\.", str(t)) for t in titles)


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en", ["3rd Year", "July 2024", "A WEEK", "SUMMER 2024", "Christmas"]),
        ("fr", ["3ème Année", "Juillet 2024", "UNE SEMAINE", "ÉTÉ 2024", "Noël"]),
        ("nl", ["3e jaar", "Juli 2024", "EEN WEEK", "ZOMER 2024", "Kerstmis"]),
        ("de", ["3. Jahr", "Juli 2024", "EINE WOCHE", "SOMMER 2024", "Weihnachten"]),
        ("es", ["3.º año", "Julio de 2024", "UNA SEMANA", "VERANO DE 2024", "Navidad"]),
        ("it", ["3° anno", "Luglio 2024", "UNA SETTIMANA", "ESTATE 2024", "Natale"]),
        ("pt-BR", ["3º ano", "Julho de 2024", "UMA SEMANA", "VERÃO DE 2024", "Natal"]),
        ("pt-PT", ["3º ano", "Julho de 2024", "UMA SEMANA", "VERÃO DE 2024", "Natal"]),
        ("pl", ["3. rok", "Lipiec 2024", "TYDZIEŃ", "LATO 2024", "Boże Narodzenie"]),
        ("sv", ["År 3", "Juli 2024", "EN VECKA", "SOMMAR 2024", "Juldagen"]),
        ("ru", ["3-й год", "Июль 2024", "НЕДЕЛЯ", "ЛЕТО 2024", "Рождество"]),
        ("ja", ["3年目", "2024年7月", "1週間", "2024年の夏", "クリスマス"]),
        ("zh-Hans", ["第3年", "2024年7月", "一周", "2024年夏天", "圣诞节"]),
        ("ko", ["3번째 해", "2024년 7월", "일주일", "2024년 여름", "크리스마스"]),
    ],
)
def test_the_fixed_phrases_in_each_language(locale: str, expected: list[str]) -> None:
    got = [
        generate_title(SelectionType.BIRTHDAY_YEAR, birthday_age=3, locale=locale).main_title,
        generate_title(SelectionType.SINGLE_MONTH, month=7, year=2024, locale=locale).main_title,
        _get_duration_label(7, locale),
        _get_time_label(date(2024, 6, 20), date(2024, 7, 3), locale),
        holiday_label("christmas", 2024, locale),
    ]

    assert got == expected


@pytest.mark.parametrize(
    ("locale", "days", "expected"),
    [
        ("pl", 5, "5 DNI"),
        ("ru", 4, "4 ДНЯ"),
        ("ru", 5, "5 ДНЕЙ"),
        ("ru", 22, "22 ДНЯ"),
        ("de", 10, "10 TAGE"),
        ("ja", 10, "10日間"),
    ],
)
def test_day_counts_take_the_languages_plural(locale: str, days: int, expected: str) -> None:
    assert _get_duration_label(days, locale) == expected


def test_a_day_number_takes_the_month_form_polish_and_russian_need() -> None:
    otd_pl = generate_title(SelectionType.ON_THIS_DAY, start_date=date(2020, 7, 14), locale="pl")
    otd_ru = generate_title(SelectionType.ON_THIS_DAY, start_date=date(2020, 7, 14), locale="ru")

    assert otd_pl.main_title == "14 lipca"
    assert otd_ru.main_title == "14 июля"


def test_months_come_from_cldr() -> None:
    assert [get_month_name(8, loc) for loc in ("fr", "de", "pl", "ja")] == [
        "Août",
        "August",
        "Sierpień",
        "8月",
    ]


def test_a_language_without_a_catalogue_key_falls_back_to_english() -> None:
    assert film_text("title.on_this_day_subtitle", "sw") == "Through the Years"
    assert film_text_n("trip.days", 4, "sw") == "4 days"
    assert get_ordinal(2, "sw") == "2nd"


def test_every_supported_locale_has_a_directory() -> None:
    dirs = {p.name for p in Path(LOCALES_DIR).iterdir() if p.is_dir()}

    assert {loc.replace("-", "_") for loc in SUPPORTED_LOCALES} <= dirs


def test_a_russian_title_draws_with_the_bundled_fonts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WHY: a developer box may have run `titles fonts --install`; CI has not.
    monkeypatch.setenv("IMMICH_MEMORIES_FONTS_DIR", str(tmp_path))
    from immich_memories.titles.font_chain import uncovered_letters
    from immich_memories.titles.fonts import bundled_font_path

    title = generate_title(SelectionType.ON_THIS_DAY, start_date=date(2020, 7, 14), locale="ru")

    montserrat = bundled_font_path("Montserrat", "Bold")
    assert uncovered_letters(f"{title.main_title} {title.subtitle}", montserrat, bold=True) == ""


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("de", "EINE WOCHE AUF KRETA, GRIECHENLAND, JUNI 2024"),
        ("nl", "EEN WEEK OP KRETA, GRIEKENLAND, JUNI 2024"),
        ("pl", "TYDZIEŃ NA KRECIE, GRECJA, CZERWIEC 2024"),
        ("ru", "КРИТ, ГРЕЦИЯ · НЕДЕЛЯ, ИЮНЬ 2024"),
        ("ja", "クレタ島, ギリシャ · 1週間, 2024年6月"),
        ("zh-Hans", "克里特岛, 希腊 · 一周, 2024年6月"),
        ("ko", "크레타섬, 그리스 · 일주일, 2024년 6월"),
    ],
)
def test_a_crete_trip_title_in_each_design(locale: str, expected: str) -> None:
    from immich_memories.titles._trip_titles import generate_trip_title

    title = generate_trip_title(
        "Crete, Greece", date(2024, 6, 20), date(2024, 6, 26), locale, kind="island"
    )

    assert title == expected
