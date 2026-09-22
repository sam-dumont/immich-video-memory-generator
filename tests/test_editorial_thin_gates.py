"""Every shot of a rules draft, put to the gates a model install can ask."""

from __future__ import annotations

from immich_memories.analysis.editorial_thin_gates import ThinGates


class Standing:
    """# WHY: the production standing gate puts one picture at a time to the reader; this states
    the answers instead, so each rule below is read against a verdict the test chose."""

    def __init__(self, scores, thin_stories=()):
        self.scores = scores
        self.thin_stories = set(thin_stories)
        self.ensured: list[str] = []

    def ensure(self, assets):
        self.ensured.extend(assets)

    def stands(self, asset, weight, story_key=""):
        needed = 2 if weight == "glimpse" or story_key in self.thin_stories else 1
        return self.scores.get(asset, 2) >= needed


class Audience:
    """# WHY: the production audience gate reads detector rows and a picture-evidence overlay
    this fixture has no source for; it has its own tests."""

    def __init__(self, verdicts=None):
        self.verdicts = verdicts or {}
        self.asked: list[str] = []

    def verdict_of(self, unit):
        self.asked.append(unit["asset_id"])
        return self.verdicts.get(unit["asset_id"], "share")


def shot(asset, *, story="S001", taken="2024-02-01T09:00:00", moment=None, favourite=False):
    return {
        "asset_id": asset,
        "story_episode": story,
        "taken": taken,
        "moment": moment or f"m-{asset}",
        "seconds": 4.0,
        "kind": "still",
        "favourite": favourite,
    }


def gates(standing=None, audience=None, hashes=None):
    return ThinGates(
        standing=standing or Standing({}),
        audience=audience or Audience(),
        thumbnail_hash=(hashes or {}).get,
    )


TIERS = {"S001": "remarkable", "S002": "background"}


def test_a_shot_the_model_says_does_not_stand_leaves_the_draft():
    standing = Standing({"weak": 0})
    kept, refused = gates(standing=standing).admit([shot("weak"), shot("fine")], tier_of=TIERS)
    assert [c["asset_id"] for c in kept] == ["fine"]
    assert [(r.asset_id, r.rule) for r in refused] == [("weak", "standing")]
    assert sorted(standing.ensured) == ["fine", "weak"]


def test_the_worthiness_tier_decides_how_hard_the_standing_gate_asks():
    """A background story's shot is a glimpse: it has to stand entirely alone."""
    standing = Standing({"one-vote": 1})
    kept, _refused = gates(standing=standing).admit([shot("one-vote", story="S001")], tier_of=TIERS)
    assert [c["asset_id"] for c in kept] == ["one-vote"]
    kept, refused = gates(standing=Standing({"one-vote": 1})).admit(
        [shot("one-vote", story="S002")], tier_of=TIERS
    )
    assert kept == []
    assert refused[0].detail == "as a glimpse story's shot"


def test_a_starred_picture_the_catalogue_records_survives_a_standing_zero():
    """The favourite wins its moment: the audience gate still decides, the text alone does not."""
    starred = dict(shot("starred", favourite=True), notable_record="the first of them")
    kept, refused = gates(standing=Standing({"starred": 0})).admit([starred], tier_of=TIERS)
    assert [c["asset_id"] for c in kept] == ["starred"]
    assert refused == []
    # without the record it is an ordinary star, and the standing answer stands
    kept, refused = gates(standing=Standing({"starred": 0})).admit(
        [shot("starred", favourite=True)], tier_of=TIERS
    )
    assert kept == [] and refused[0].rule == "standing"


def test_a_shot_the_audience_gate_refuses_leaves_and_says_which_verdict():
    audience = Audience({"private": "do_not_show"})
    kept, refused = gates(audience=audience).admit([shot("private"), shot("fine")], tier_of=TIERS)
    assert [c["asset_id"] for c in kept] == ["fine"]
    assert [(r.asset_id, r.rule, r.detail) for r in refused] == [
        ("private", "audience", "do_not_show")
    ]


def test_the_audience_gate_is_never_asked_about_a_shot_standing_already_took():
    audience = Audience()
    gates(standing=Standing({"weak": 0}), audience=audience).admit(
        [shot("weak"), shot("fine")], tier_of=TIERS
    )
    assert audience.asked == ["fine"]


def test_two_captures_of_one_moment_inside_five_minutes_are_one_shot():
    kept, refused = gates().admit(
        [
            shot("first", moment="m1", taken="2024-02-01T09:00:00"),
            shot("second", moment="m1", taken="2024-02-01T09:01:00"),
            shot("later", moment="m1", taken="2024-02-01T09:30:00"),
        ],
        tier_of=TIERS,
    )
    assert [c["asset_id"] for c in kept] == ["first", "later"]
    assert [(r.asset_id, r.rule) for r in refused] == [("second", "capture spacing")]


def test_a_frame_that_repeats_one_the_cut_holds_is_refused_by_its_cached_hash():
    same = "1" * 64
    kept, refused = gates(hashes={"keeper": same, "twin": same, "other": "0" * 64}).admit(
        [
            shot("keeper", taken="2024-02-01T09:00:00", moment="m1"),
            shot("twin", taken="2024-02-01T10:00:00", moment="m2"),
            shot("other", taken="2024-02-01T11:00:00", moment="m3"),
        ],
        tier_of=TIERS,
    )
    assert [c["asset_id"] for c in kept] == ["keeper", "other"]
    assert [(r.asset_id, r.rule) for r in refused] == [("twin", "look-alike")]
    assert refused[0].detail.startswith("repeats keeper")


def test_a_shot_the_owner_ticked_is_never_taken_by_the_look_alike_review():
    same = "1" * 64
    kept, refused = gates(hashes={"ticked": same, "twin": same}).admit(
        [
            shot("twin", taken="2024-02-01T09:00:00", moment="m1"),
            shot("ticked", taken="2024-02-01T10:00:00", moment="m2"),
        ],
        tier_of=TIERS,
        protected=["ticked"],
    )
    assert "ticked" in [c["asset_id"] for c in kept]
    assert [r.asset_id for r in refused] == ["twin"]
