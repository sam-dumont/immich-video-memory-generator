"""A text reader's category holds a picture only when the evidence it read supports it (#1124)."""

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
