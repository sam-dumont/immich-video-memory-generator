"""A text reader's category holds a picture only when the evidence it read supports it (#1124)."""

import pytest

from immich_memories.analysis import editorial_shareability as share
from tests.test_editorial_audience_evidence import Judge, activity, evidence, exposure

BIB = "A runner wears a race bib with a large printed number and a sponsor logo."


def test_a_bib_read_as_identifying_record_with_no_document_fact_does_not_hold():
    judge = Judge(activity("identifying_record"))
    result = share.check_audience(
        judge, evidence(BIB, doc_docling="photograph", nsfw_marqo="no"), "test"
    )
    assert result["verdict"] == "share"
    assert result["activity"]["finding"] == "identifying_record"
    assert result["activity"]["supported"] is False


def test_a_real_id_card_with_the_document_fact_still_holds():
    caption = "A hand holds a card with a portrait and printed text."
    judge = Judge(activity("identifying_record"))
    result = share.check_audience(
        judge, evidence(caption, doc_docling="full_page_image", nsfw_marqo="no"), "test"
    )
    assert result["verdict"] == "do_not_show"
    assert result["finding"] == "private_activity"


def test_a_caption_that_names_an_identity_document_holds_without_a_document_detector():
    caption = "A person holds a passport open at the photo page."
    judge = Judge(activity("identifying_record"))
    result = share.check_audience(judge, evidence(caption), "test")
    assert result["verdict"] == "do_not_show"


def test_a_caption_that_denies_personal_details_supports_no_record():
    caption = "visible_records: a race bib; the number is legible, no personal details shown."
    judge = Judge(activity("identifying_record"))
    result = share.check_audience(judge, evidence(caption, doc_docling="photograph"), "test")
    assert result["verdict"] == "share"


def test_an_nsfw_head_hold_is_untouched_when_the_record_is_unsupported():
    # The coverage reader calls the runner clothed; the head's hold still stands.
    judge = Judge(activity("identifying_record"), exposure({"p1": [["runner", "clothing"]]}))
    result = share.check_audience(
        judge, evidence(BIB, doc_docling="photograph", nsfw_marqo="yes"), "test"
    )
    assert result["verdict"] == "family_only"
    assert result["finding"] == "exposure_evidence"
    assert not share.allowed(result["verdict"], "sendable")


def test_ordinary_shirtlessness_read_as_adult_changing_stays_in_the_family():
    caption = "A shirtless man holds a toddler on his hip in the garden."
    judge = Judge(activity("adult_changing"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "family_only"
    assert share.allowed(result["verdict"], "family")
    assert not share.allowed(result["verdict"], "sendable")


def test_depicted_undressing_still_holds_as_adult_changing():
    caption = "An adult undresses in a changing room, private body parts exposed."
    judge = Judge(activity("adult_changing"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "do_not_show"


def test_a_striped_shirt_does_not_describe_undressing():
    caption = "A man in a striped shirt carries a child; the plane takes off behind them."
    judge = Judge(activity("adult_changing"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "family_only"


def test_a_held_baby_read_as_breastfeeding_with_no_feeding_described_does_not_hold():
    caption = "A woman holds a baby close to her chest on the sofa."
    judge = Judge(activity("breastfeeding_or_expressing_milk"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "share"
    assert result["activity"]["supported"] is False


@pytest.mark.parametrize(
    "caption",
    [
        "A mother nurses her baby under a blanket.",
        "A baby latches on while the mother sits in bed.",
        "A woman pumps breast milk in the kitchen.",
        "A woman feeds her newborn at her bare breast.",
    ],
)
def test_described_feeding_still_holds_as_breastfeeding(caption):
    judge = Judge(activity("breastfeeding_or_expressing_milk"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "do_not_show"


def test_a_breastfeeding_read_under_a_nsfw_hold_keeps_the_hold():
    caption = "A woman holds a newborn baby wrapped in a blanket."
    judge = Judge(activity("breastfeeding_or_expressing_milk"), exposure())
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="yes"), "test")
    assert not share.allowed(result["verdict"], "sendable")


@pytest.mark.parametrize(
    "caption",
    [
        "A mother gorilla is nursing her young gorillas in the jungle.",
        "A mother owl is nursing her owlets inside a nest.",
    ],
)
def test_an_animal_nursing_is_not_breastfeeding(caption):
    judge = Judge(activity("breastfeeding_or_expressing_milk"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "share"


@pytest.mark.parametrize(
    "caption",
    [
        "A bathroom with a blue shower curtain and toiletries on the shelves.",
        "A man assists a baby sitting in a small inflatable pool filled with water.",
        "A man holds a baby in a swimming pool while another man swims nearby.",
        "A woman and her child are standing in a river.",
        "A child is drinking from a stream of water.",
        "A person is washing pumpkins in a blue bucket.",
    ],
)
def test_bathing_needs_a_person_in_a_bath_or_shower(caption):
    judge = Judge(activity("bathing"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "share"
    assert result["activity"]["supported"] is False


@pytest.mark.parametrize(
    "caption",
    [
        "A young girl is sitting in a bathtub filled with water and soapy bubbles.",
        "An adult washes a child's hair under running water in a shower.",
        "A parent bathes a baby in a sink.",
    ],
)
def test_a_person_in_a_bath_or_shower_still_holds_as_bathing(caption):
    judge = Judge(activity("bathing"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "do_not_show"


@pytest.mark.parametrize(
    "caption",
    [
        "A young woman is standing outdoors, wearing a sleeveless top and holding a bag.",
        "A young girl is sitting on the floor, wearing a white tank top and leggings.",
        "A woman with dark hair and a white dress is seated on a chair.",
        "A man in a vest carries a toddler on his shoulders.",
        "A woman wears a sleeveless red dress and sandals, exposing her arms.",
    ],
)
def test_nudity_needs_an_uncovered_body_described(caption):
    judge = Judge(activity("nudity_shirtless_or_underwear"), exposure())
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "share"


@pytest.mark.parametrize(
    "caption",
    [
        "A shirtless man wearing a pink towel around his waist stands outside.",
        "A toddler in only a diaper plays on the floor.",
        "A woman in a bikini lies on a towel.",
        "A man in shorts plays with a baby, exposing his upper torso.",
    ],
)
def test_a_described_uncovered_body_still_holds_as_nudity(caption):
    judge = Judge(activity("nudity_shirtless_or_underwear"), exposure())
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "family_only"


def test_bottle_feeding_is_not_breastfeeding():
    caption = "A baby is being fed milk from a bottle held by an adult's hand."
    judge = Judge(activity("breastfeeding_or_expressing_milk"))
    result = share.check_audience(judge, evidence(caption, nsfw_marqo="no"), "test")
    assert result["verdict"] == "share"
