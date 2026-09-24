"""A trip title says where it went the way its language does, or with no preposition at all."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime

import pytest
from babel import Locale

from immich_memories.i18n import SUPPORTED_LOCALES
from immich_memories.place_phrases import PREPOSITION_FREE, Place, phrase_module_name, place_phrase
from immich_memories.titles._trip_titles import generate_trip_title

_JULY = (date(2025, 7, 1), date(2025, 7, 14))


def test_every_title_language_has_phrase_rules_or_says_it_goes_without():
    for locale in SUPPORTED_LOCALES:
        has_rules = importlib.util.find_spec(phrase_module_name(locale)) is not None
        assert has_rules != (locale in PREPOSITION_FREE), locale


@pytest.mark.parametrize(
    ("label", "kind", "english", "french"),
    [
        ("Netherlands", "country", "in the Netherlands", "aux Pays-Bas"),
        ("United States", "country", "in the United States", "aux États-Unis"),
        ("United Kingdom", "country", "in the United Kingdom", "au Royaume-Uni"),
        ("Philippines", "country", "in the Philippines", "aux Philippines"),
        ("Maldives", "country", "in the Maldives", "aux Maldives"),
        ("Dominican Republic", "country", "in the Dominican Republic", "en République dominicaine"),
        (
            "United Arab Emirates",
            "country",
            "in the United Arab Emirates",
            "aux Émirats arabes unis",
        ),
        ("Italy", "country", "in Italy", "en Italie"),
        ("Portugal", "country", "in Portugal", "au Portugal"),
        ("Cyprus", "island", "in Cyprus", "à Chypre"),
        ("Crete, Greece", "island", "in Crete, Greece", "en Crète, Grèce"),
        ("Mallorca, Spain", "island", "in Mallorca, Spain", "à Majorque, Espagne"),
        (
            "Canary Islands, Spain",
            "region",
            "in the Canary Islands, Spain",
            "aux Canaries, Espagne",
        ),
        ("Apulia, Italy", "region", "in Apulia, Italy", "dans les Pouilles, Italie"),
        ("Saxony, Germany", "region", "in Saxony, Germany", "en Saxe, Allemagne"),
        (
            "Las Vegas, United States",
            "city",
            "in Las Vegas, United States",
            "à Las Vegas, États-Unis",
        ),
        (
            "Utah and Nevada, United States",
            "regions",
            "in Utah and Nevada, United States",
            "dans l'Utah et au Nevada, États-Unis",
        ),
    ],
)
def test_a_place_takes_its_own_preposition(label, kind, english, french):
    place = Place(label, kind)

    assert place_phrase("en", place) == english
    assert place_phrase("fr", place) == french


def test_the_article_is_given_to_exactly_the_country_names_that_take_it():
    takes_the = {
        name
        for code, name in Locale("en").territories.items()
        if len(code) == 2 and code.isalpha()
        if place_phrase("en", Place(name, "country")) == f"in the {name}"
    }

    assert takes_the == {
        "Bahamas",
        "British Indian Ocean Territory",
        "British Virgin Islands",
        "Canary Islands",
        "Caribbean Netherlands",
        "Cayman Islands",
        "Central African Republic",
        "Cocos (Keeling) Islands",
        "Comoros",
        "Cook Islands",
        "Dominican Republic",
        "Falkland Islands",
        "Faroe Islands",
        "French Southern Territories",
        "Gambia",
        "Heard & McDonald Islands",
        "Isle of Man",
        "Maldives",
        "Marshall Islands",
        "Netherlands",
        "Northern Mariana Islands",
        "Palestinian Territories",
        "Philippines",
        "Pitcairn Islands",
        "Seychelles",
        "Solomon Islands",
        "South Georgia & South Sandwich Islands",
        "Turks & Caicos Islands",
        "U.S. Outlying Islands",
        "U.S. Virgin Islands",
        "United Arab Emirates",
        "United Kingdom",
        "United States",
        "Åland Islands",
    }


def test_a_trip_title_carries_the_phrase():
    assert (
        generate_trip_title("Netherlands", *_JULY, "en")
        == "TWO WEEKS IN THE NETHERLANDS, JULY 2025"
    )
    assert (
        generate_trip_title("Apulia, Italy", *_JULY, "fr", kind="region")
        == "DEUX SEMAINES DANS LES POUILLES, ITALIE, JUILLET 2025"
    )


def test_a_place_french_has_no_phrase_for_goes_without_a_preposition():
    title = generate_trip_title("Nordland, Norway", *_JULY, "fr", kind="region")

    assert title == "NORDLAND, NORVÈGE · DEUX SEMAINES, JUILLET 2025"


def test_a_language_with_no_rules_has_no_phrase():
    assert place_phrase("xx", Place("Crete, Greece", "island")) is None


def test_a_label_saved_without_its_scale_is_read_from_its_shape():
    assert generate_trip_title("Crete, Greece", *_JULY, "fr") == (
        "DEUX SEMAINES EN CRÈTE, GRÈCE, JUILLET 2025"
    )
    assert generate_trip_title("Belgium → Spain", *_JULY, "fr") == (
        "BELGIQUE → ESPAGNE · DEUX SEMAINES, JUILLET 2025"
    )
    assert generate_trip_title("Barcelona, Spain", *_JULY, "fr") == (
        "DEUX SEMAINES À BARCELONA, ESPAGNE, JUILLET 2025"
    )


def test_a_detected_trip_carries_the_scale_its_title_is_phrased_by():
    from datetime import UTC, datetime, timedelta

    from immich_memories.analysis.trip_detection import detect_trips
    from immich_memories.api.models import Asset, AssetType, ExifInfo
    from immich_memories.generate_privacy import generate_trip_title_text

    def picture(n: int, city: str) -> Asset:
        ts = datetime(2025, 7, 1, 10, tzinfo=UTC) + timedelta(hours=8 * n)
        exif = ExifInfo(latitude=41.1, longitude=16.9, city=city, state="Apulia", country="Italy")
        return Asset(
            id=f"a{n}",
            type=AssetType.IMAGE,
            fileCreatedAt=ts,
            fileModifiedAt=ts,
            updatedAt=ts,
            exifInfo=exif,
        )

    pictures = [
        picture(i, city) for i, city in enumerate(["Bari", "Lecce", "Ostuni", "Otranto"] * 5)
    ]
    (trip,) = detect_trips(pictures, 64.0, -150.0, max_gap_days=3)
    preset = {
        "location_name": trip.location_name,
        "location_kind": trip.location_kind,
        "trip_start": trip.start_date,
        "trip_end": trip.end_date,
    }

    assert trip.location_kind == "region"
    assert generate_trip_title_text(preset, "fr").startswith(
        "UNE SEMAINE DANS LES POUILLES, ITALIE"
    )


async def _trip_title_from_model(reply_title: str, place: str, locale: str = "fr"):
    from unittest.mock import AsyncMock, patch

    from immich_memories.config_models_llm import LLMConfig
    from immich_memories.titles.llm_titles import MemoryTitleFacts, generate_title_with_llm

    config = LLMConfig(provider="openai-compatible", base_url="http://localhost:8080/v1", model="m")
    reply = f'{{"title": "{reply_title}", "subtitle": null}}'
    # WHY: query_llm is the boundary to the LLM server; the reply is what is under test.
    with patch("immich_memories.titles.llm_titles.query_llm", new_callable=AsyncMock) as ask:
        ask.return_value = reply
        suggestion = await generate_title_with_llm(
            memory_type="trip",
            locale=locale,
            start_date="2025-07-01",
            end_date="2025-07-14",
            duration_days=13,
            facts=MemoryTitleFacts(place=place),
            llm_config=config,
        )
    return suggestion, ask.call_args.args[0]


@pytest.mark.asyncio
async def test_the_model_is_told_the_place_and_may_name_it_in_the_films_language():
    suggestion, prompt = await _trip_title_from_model("Deux semaines en Crète", "Crete, Greece")

    assert "Crete, Greece" in prompt
    assert suggestion is not None
    assert suggestion.title == "Deux semaines en Crète"


@pytest.mark.asyncio
async def test_a_model_title_that_names_another_place_gives_way_to_the_template():
    suggestion, _prompt = await _trip_title_from_model("Un été à Santorin", "Crete, Greece")

    assert suggestion is None


def test_a_refused_model_title_leaves_the_trip_to_its_place_template(tmp_path):
    from immich_memories.cli._llm_title import resolve_cli_title
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams, _build_title_settings
    from immich_memories.timeperiod import DateRange

    config = Config()
    config.llm.model = "some-model"
    preset = {"location_name": "Crete, Greece", "trip_start": _JULY[0], "trip_end": _JULY[1]}

    title, subtitle, source = resolve_cli_title(
        enabled=True,
        title_override=None,
        clips=[],
        config=config,
        memory_type="trip",
        date_range=DateRange(start=datetime(2025, 7, 1), end=datetime(2025, 7, 14)),
        person_names=[],
        memory_preset_params=preset,
        # WHY: the title reader is an LLM call; refusing its title returns None.
        ask=lambda **_kwargs: None,
    )
    params = GenerationParams(
        clips=[],
        output_path=tmp_path / "trip.mp4",
        config=config,
        memory_type="trip",
        memory_preset_params=preset,
        title=title,
        subtitle=subtitle,
        title_source=source,
    )

    settings = _build_title_settings(params, config, [])

    assert settings is not None
    assert settings.title_source == "place"
    assert settings.trip_title_text == "TWO WEEKS IN CRETE, GREECE, JULY 2025"
