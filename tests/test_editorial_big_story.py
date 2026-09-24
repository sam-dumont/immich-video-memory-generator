"""A story is big because it is dense AND full of close family, never on picture count alone.

A dense day of strangers (a race, a fair) has the pictures and not the people; a quiet week with
the family has the people and not the pictures. Only a story with both is floored to major
without three favourites.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
from immich_memories.config_models_editorial import EditorialPeopleConfig
from tests.editorial_subject_family import film_of, her_parents, subject_people

FAMILY = "with Person A (partner; inner circle); Person B (son; 3 months old)"
STRANGERS = "with Person C (unconfirmed)"

# day of the month -> (pictures, how many of them show close family, the day's gate reading)
MONTH = {
    1: (10, 2, "maybe"),
    2: (10, 2, "maybe"),
    3: (10, 2, "maybe"),
    # a dense run of close family: the story that should be big
    6: (60, 50, "remarkable"),
    7: (60, 48, "remarkable"),
    # a quiet run of close family
    10: (10, 9, "maybe"),
    11: (10, 9, "maybe"),
    12: (10, 9, "maybe"),
    # a dense day of strangers
    15: (80, 3, "remarkable"),
    18: (10, 2, "maybe"),
    19: (10, 2, "maybe"),
}


def _read_month(*, family_line=FAMILY, film=None):
    assets, moments, annotations = {}, {}, {}
    for day, (pictures, family, _gate) in MONTH.items():
        ids = []
        for n in range(pictures):
            asset_id = f"d{day:02}-{n:03}"
            taken = datetime(2030, 2, day, 9) + timedelta(minutes=n)
            assets[asset_id] = SimpleNamespace(
                id=asset_id,
                file_created_at=taken,
                is_favorite=False,
                is_video=False,
                exif_info=None,
                people=[],
            )
            company = family_line if n < family else STRANGERS
            annotations[asset_id] = f"{taken.isoformat()} | playing | {company}"
            ids.append(asset_id)
        moments[f"M{day:02}"] = tuple(ids)
    source = SimpleNamespace(
        assets=assets,
        moment_asset_ids=moments,
        gps={},
        annotations=annotations,
        audience_annotations={},
        config=SimpleNamespace(
            trips=SimpleNamespace(homebase_latitude=0.0, homebase_longitude=0.0),
            editorial=SimpleNamespace(people=EditorialPeopleConfig()),
        ),
        intent=SimpleNamespace(product="monthly_highlights"),
        case=SimpleNamespace(product="monthly_highlights", people=()),
        people=None,
    )
    if film is not None:
        source.case, source.people = film.case, film.people
    reader = RuleStructureReader(source)

    def enrich(episodes):
        hints = {}
        for episode in episodes:
            day = int(episode.moments[0][1:])
            pictures, family, gate = MONTH[day]
            relations = {"partner": family, "son": family} if family else {}
            hints[episode.key] = {
                "day": date(2030, 2, day).isoformat(),
                "moments": 1,
                "pictures": pictures,
                "favourites": 0,
                "gate": gate,
                "relations": relations | {"unconfirmed": pictures - family},
            }
        return hints

    story = reader.read_story(None, evidence=[], enrich=enrich, record=lambda _record: None)
    weight_of = {}
    for row in story.stories:
        for key in row["episodes"]:
            day = int(next(e for e in story.episodes if e.key == key).moments[0][1:])
            weight_of[day] = row["weight"]
    return weight_of


def test_a_dense_story_full_of_close_family_is_floored_to_major():
    assert _read_month()[6] == "major"


def test_a_dense_day_of_strangers_is_not_promoted():
    assert _read_month()[15] == "minor"


def test_a_quiet_week_with_the_family_is_not_promoted():
    assert _read_month()[10] == "minor"


def test_a_person_films_big_story_counts_the_subjects_own_parents(tmp_path):
    """In a film of the owner's partner, a dense run with her parents is her family's big day;
    to the owner they are in-laws, so a month film still reads the same run as minor."""
    _people, relation = subject_people(tmp_path)
    parents = her_parents(relation)

    person = _read_month(family_line=parents, film=film_of(tmp_path / "p", "person_spotlight"))
    month = _read_month(family_line=parents, film=film_of(tmp_path / "m", "monthly_highlights"))

    assert person[6] == "major"
    assert month[6] == "minor"
