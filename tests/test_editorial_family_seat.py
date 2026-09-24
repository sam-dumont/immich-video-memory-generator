"""A close family member the period is full of is never left out of its film.

The month below is the shape that left a partner out of a real film: every grant went to a
favourite, and none of the favourites shows her, though she is on fifty of the month's pictures.
"""

from __future__ import annotations

from immich_memories.analysis.editorial_family_seat import (
    FamilySeatInputs,
    FamilySeatPolicy,
    seat_close_family,
)
from immich_memories.analysis.editorial_story_replies import close_family_on

PARTNER = "Person A (partner; inner circle)"
CHILD = "Person B (son; 3 months old; recurring circle)"
FRIEND = "Person C (friend; recurring circle)"


def _month(*, partner_pictures=50, relation=PARTNER):
    """Two stories. S1 holds three favourites of the child; S2 holds the partner's pictures and a
    favourite plus an ordinary shot of the child."""
    lines, rows = {}, {"S1": [], "S2": []}
    for n in range(3):
        lines[f"fav-{n}"] = f"2024-02-0{n + 1} | with {CHILD} | STARRED by the photographer"
        rows["S1"].append({"asset_id": f"fav-{n}", "story_episode": "S1", "favourite": True})
    for n in range(partner_pictures):
        lines[f"p-{n:02}"] = f"2024-02-1{n % 9} | with {relation}; {CHILD}"
        rows["S2"].append({"asset_id": f"p-{n:02}", "story_episode": "S2", "taken": f"{n:02}"})
    for n in range(100):
        lines[f"x-{n:02}"] = "2024-02-20 | a street"
        rows["S2"].append({"asset_id": f"x-{n:02}", "story_episode": "S2", "taken": f"x{n:02}"})
    lines["fav-s2"] = f"2024-02-19 | with {CHILD} | STARRED by the photographer"
    lines["plain-s2"] = f"2024-02-19 | with {CHILD}"
    rows["S2"] += [
        {"asset_id": "fav-s2", "story_episode": "S2", "favourite": True},
        {"asset_id": "plain-s2", "story_episode": "S2"},
    ]
    film = [*rows["S1"], rows["S2"][-2], rows["S2"][-1]]
    return lines, rows, film


def _inputs(lines, rows, *, room=False, refused=(), weak=(), policy=FamilySeatPolicy()):
    return FamilySeatInputs(
        stories=[{"key": "S1", "weight": "major"}, {"key": "S2", "weight": "minor"}],
        candidates_of=lambda key: rows[key],
        line_of=lambda asset: lines.get(asset, ""),
        scope=list(lines),
        stands=lambda asset, _story: asset not in weak,
        # the rules rank the partner's twelfth picture highest
        score_of=lambda asset: 2 if asset == "p-12" else 1,
        refused=lambda asset: asset in refused,
        has_room=lambda _film: room,
        policy=policy,
    )


def _shows_partner(lines, film):
    return [
        c["asset_id"] for c in film if "partner" in close_family_on(lines[c["asset_id"]]).values()
    ]


def test_a_partner_on_fifty_pictures_with_no_shot_gets_exactly_one_and_no_favourite_moves():
    lines, rows, film = _month()
    assert not _shows_partner(lines, film)

    seated, record = seat_close_family(film, _inputs(lines, rows))

    assert _shows_partner(lines, seated) == ["p-12"]
    assert [c["asset_id"] for c in seated if c.get("favourite")] == [
        c["asset_id"] for c in film if c.get("favourite")
    ]
    assert len(seated) == len(film)
    assert record["seats"] == [
        {
            "relation": "partner",
            "pictures": 50,
            "story": "S2",
            "asset_id": "p-12",
            "placed": "replaced",
            "replaced": "plain-s2",
        }
    ]


def test_a_film_with_room_takes_the_seat_without_giving_anything_up():
    lines, rows, film = _month()

    seated, _ = seat_close_family(film, _inputs(lines, rows, room=True))

    assert seated[:-1] == film
    assert seated[-1]["asset_id"] == "p-12"


def test_a_story_of_favourites_only_gives_no_seat_when_the_film_is_full():
    lines, rows, film = _month()
    film = [c for c in film if c["asset_id"] != "plain-s2"]

    seated, record = seat_close_family(film, _inputs(lines, rows))

    assert seated == film
    assert record["seats"][0]["placed"] is None


def test_a_held_or_weak_frame_is_never_the_seat():
    lines, rows, film = _month()
    held = {"p-12"} | {f"p-{n:02}" for n in range(0, 50, 2)}

    seated, _ = seat_close_family(film, _inputs(lines, rows, refused=held, weak={"p-01"}))

    (seat,) = _shows_partner(lines, seated)
    assert seat not in held | {"p-01"}


def test_nobody_is_seated_when_every_frame_is_held():
    lines, rows, film = _month()

    seated, _ = seat_close_family(
        film, _inputs(lines, rows, refused={f"p-{n:02}" for n in range(50)})
    )

    assert seated == film


def test_a_small_share_owes_no_seat_and_five_percent_of_the_period_does():
    lines, rows, film = _month(partner_pictures=8)  # 8 of 113 pictures: 7 %

    strict, _ = seat_close_family(
        film, _inputs(lines, rows, policy=FamilySeatPolicy(min_pictures=20, min_share=0.08))
    )
    seated, _ = seat_close_family(film, _inputs(lines, rows))

    assert not _shows_partner(lines, strict)
    assert len(_shows_partner(lines, seated)) == 1


def test_a_friend_is_not_close_family():
    lines, rows, film = _month(relation=FRIEND)

    seated, record = seat_close_family(film, _inputs(lines, rows))

    assert seated == film
    assert record["seats"] == []


def _planned_month(tmp_path, *, seat: bool, seconds: float = 60, scene_print=None):
    """A rules-read month shaped like the one that left a partner out. In her week every moment's
    starred frame shows the child alone and the partner is on its other five pictures, so each
    moment she is in goes to a favourite; each day also has a moment of the child alone. The
    next week is the child's."""
    from dataclasses import replace
    from datetime import date

    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from immich_memories.config_models_editorial import EditorialPeopleConfig
    from tests.editorial_film_fixtures import film_source, home_days

    days = [*home_days(date(2030, 2, 3), 5), *home_days(date(2030, 2, 12), 5)]
    days = [replace(day, moments=3) for day in days]
    source = film_source(
        tmp_path, days, seconds=seconds, span=(date(2030, 2, 1), date(2030, 2, 28)), pictures=6
    )
    for asset_id, line in list(source.annotations.items()):
        day, moment, picture = (int(part[1:]) for part in asset_id.split("-"))
        hers = day < 5 and moment < 2 and picture > 0
        company = f"{PARTNER}; {CHILD}" if hers else CHILD
        source.annotations[asset_id] = line.replace(
            " | activity=", f" | with {company} | activity="
        )
        source.assets[asset_id].is_favorite = moment < 2 and picture == 0
    if not seat:
        editorial = source.config.editorial.model_copy(
            update={"people": EditorialPeopleConfig(seat_min_pictures=10**6, seat_min_share=1.0)}
        )
        source = replace(source, config=source.config.model_copy(update={"editorial": editorial}))
    plan = plan_structure(
        source,
        StructurePlannerPorts(
            judge=NoModelJudge(),
            thumbnail_hash=lambda _asset: None,
            rules=RuleStructureReader(source),
            scene_print=scene_print,
        ),
    ).plan
    shots = [c["asset_id"] for c in plan["carriers"]]
    starred = [a for a in shots if source.assets[a].is_favorite]
    partner = [a for a in shots if "partner" in close_family_on(source.annotations[a]).values()]
    return shots, starred, partner


def test_the_rules_draft_gives_a_partner_left_out_by_the_favourites_exactly_one_shot(tmp_path):
    _shots, starred_today, partner_today = _planned_month(tmp_path / "today", seat=False)
    shots, starred, partner = _planned_month(tmp_path / "seated", seat=True)

    assert partner_today == []
    assert len(partner) == 1
    assert starred == starred_today


def test_the_duplicate_review_never_takes_a_seated_partner_out_of_the_film(tmp_path):
    """Every frame of the month reads as one scene, so the final review sees each non-favourite
    as a repeat of a favourite: the partner's only shot must survive it."""
    import numpy as np

    _shots, _starred, partner = _planned_month(
        tmp_path, seat=True, scene_print=lambda _asset: np.array([1.0, 0.0])
    )

    assert len(partner) == 1


def test_a_partner_who_lost_her_only_shot_after_the_draft_is_seated_again():
    """Whatever took her shot after the seat ran (a review, the audience gate, the trim), the
    finished film is checked again and the frame that gives up its place is on the cut record."""
    from types import SimpleNamespace

    from immich_memories.analysis.editorial_structure_finishing import (
        PlanRun,
        seat_again_after_review,
    )

    lines, rows, film = _month()
    run = PlanRun(carriers=list(film))

    seat_again_after_review(
        run,
        SimpleNamespace(resolve_motion=None),
        seat=lambda cut: seat_close_family(cut, _inputs(lines, rows))[0],
    )

    assert _shows_partner(lines, run.carriers) == ["p-12"]
    assert [(c["asset_id"], c["review_stage"]) for c in run.cut_carriers] == [
        ("plain-s2", "family-seat")
    ]
    assert run.carriers == sorted(run.carriers, key=lambda c: str(c.get("taken", "")))


def test_a_frame_the_audience_gate_holds_gives_the_seat_to_her_next_best():
    lines, rows, film = _month()
    asked = []

    def held(asset):
        asked.append(asset)
        return asset == "p-12"

    inputs = FamilySeatInputs(**{**_inputs(lines, rows).__dict__, "held": held})
    seated, _ = seat_close_family(film, inputs)

    assert _shows_partner(lines, seated) == ["p-00"]
    assert asked == ["p-12", "p-00"]


def test_a_full_film_whose_stories_of_her_hold_no_shot_seats_her_in_place_of_its_weakest():
    """Her story got no grant, so it holds nothing to replace: the film's weakest non-favourite
    anywhere gives up its place, never the only shot of a story."""
    lines, rows, film = _month()
    film = [*rows["S1"], rows["S2"][-2], {"asset_id": "x-00", "story_episode": "S3"}]
    film += [{"asset_id": "x-01", "story_episode": "S3"}]
    rows["S2"] = [r for r in rows["S2"] if r["asset_id"] != "plain-s2"]

    seated, record = seat_close_family(film, _inputs(lines, rows))

    assert _shows_partner(lines, seated) == ["p-12"]
    assert record["seats"][0]["placed"] == "replaced"
    assert record["seats"][0]["replaced"] in {"x-00", "x-01"}
    assert {c["asset_id"] for c in seated} >= {"fav-0", "fav-1", "fav-2", "fav-s2"}


def test_someone_whose_every_picture_is_refused_is_owed_nothing_and_the_record_says_why():
    lines, rows, film = _month()

    seated, record = seat_close_family(
        film, _inputs(lines, rows, refused={f"p-{n:02}" for n in range(50)})
    )

    assert seated == film
    assert record["seats"] == [
        {
            "relation": "partner",
            "pictures": 50,
            "showable": 0,
            "placed": None,
            "reason": "every picture of them in this film is refused as a carrier",
        }
    ]


_PEOPLE_FILE = """
version: 1
owner: {person_id: owner-id, identified: confirmed}
people:
  - {ids: [owner-id], name: Owner}
  - name: Subject
    ids: [subject-id]
    confirmed:
      role: partner
      links:
        - {kind: partner-of, with: owner-id}
        - {kind: child-of, with: her-father-id}
  - {ids: [her-father-id], name: Her Father}
  - name: Her Mother
    ids: [her-mother-id]
    confirmed: {links: [{kind: mother-of, with: subject-id}]}
"""


def _planned_person_film(tmp_path, *, product: str, scene_print=None, full=False):
    """A rules-read period of the owner's partner. Every moment's starred frame shows her alone,
    and her father and mother are on the other pictures of her first week, so every moment they
    are in goes to a favourite. The people file links her parents to her, not to the owner."""
    from dataclasses import replace
    from datetime import date

    from immich_memories.analysis.editorial_people import adapt_editorial_people
    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from immich_memories.people.context import load_people_prompt_context
    from tests.editorial_film_fixtures import film_source, home_days

    people_file = tmp_path / "people.yaml"
    people_file.parent.mkdir(parents=True, exist_ok=True)
    people_file.write_text(_PEOPLE_FILE)
    context = load_people_prompt_context(people_file, include_derived=True)
    relation = {c.name: c.relationship for c in context.values()}
    days = [*home_days(date(2030, 2, 3), 5), *home_days(date(2030, 2, 12), 5)]
    days = [replace(day, moments=3) for day in days]
    source = film_source(
        tmp_path,
        days,
        seconds=60,
        span=(date(2030, 2, 1), date(2030, 2, 28)),
        pictures=6,
        product=product,
    )
    subject = f"Subject ({relation['Subject']})"
    parents = "; ".join(f"{name} ({relation[name]})" for name in ("Her Father", "Her Mother"))
    for asset_id, line in list(source.annotations.items()):
        day, moment, picture = (int(part[1:]) for part in asset_id.split("-"))
        with_parents = day < 5 and moment < 2 and picture > 0
        company = f"{parents}; {subject}" if with_parents else subject
        source.annotations[asset_id] = line.replace(
            " | activity=", f" | with {company} | activity="
        )
        source.assets[asset_id].is_favorite = moment < 2 and picture == 0
    source = replace(
        source,
        case=replace(source.case, people=("Subject",)),
        people=adapt_editorial_people(context),
    )
    plan = plan_structure(
        source,
        StructurePlannerPorts(
            judge=NoModelJudge(),
            thumbnail_hash=lambda _asset: None,
            rules=RuleStructureReader(source),
            scene_print=scene_print,
        ),
    ).plan
    shots = [c["asset_id"] for c in plan["carriers"]]
    fathers = [a for a in shots if "Her Father" in source.annotations[a]]
    return (fathers, plan, source) if full else fathers


def test_a_person_film_seats_its_subjects_parents_though_the_owner_calls_them_in_laws(tmp_path):
    assert len(_planned_person_film(tmp_path / "person", product="person_spotlight")) == 1


def test_the_review_keeps_the_subjects_parents_only_shot_beside_its_look_alike(tmp_path):
    """Every frame of the month reads as one scene, so the parents' seated shot is a look-alike of
    a favourite. The owner calls them in-laws, so only the film's own close family protects it:
    the review keeps it, and the second seat has nobody left to seat."""
    import json

    import numpy as np

    fathers, plan, source = _planned_person_film(
        tmp_path,
        product="person_spotlight",
        scene_print=lambda _asset: np.array([1.0, 0.0]),
        full=True,
    )

    assert len(fathers) == 1
    assert plan["final_duplicate_review"]["kept_only_shots"] == fathers
    after = source.artifact_dir / "derived-decisions" / "family-seat-after-review.private.json"
    assert json.loads(after.read_text())["seats"] == []


def test_a_month_film_keeps_close_family_relative_to_the_owner(tmp_path):
    assert _planned_person_film(tmp_path / "month", product="monthly_highlights") == []


def test_the_owner_whose_note_nests_a_parenthesis_is_read_as_a_person():
    from immich_memories.analysis.editorial_story_replies import people_on

    line = (
        "2030-02-03 | with Owner (library owner (inferred); aged 38; inner circle); "
        "Person B (son; 3 months old)"
    )

    assert people_on(line) == {"Owner": "library owner (inferred)", "Person B": "son"}
    assert close_family_on(line) == {"Person B": "son"}
