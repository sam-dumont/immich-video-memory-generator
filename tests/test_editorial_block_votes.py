"""A vote bank must replay the same model question, including its positional labels."""

import json
import re
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_block_votes import judge_worthiness
from immich_memories.analysis.editorial_standing_vote import judge_standing
from immich_memories.config_models_llm import LLMConfig


class VoteJudge:
    def __init__(self, **settings):
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a", **settings))
        self.calls = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append((stage, prompt))
        if stage.startswith("standing"):
            weak = re.findall(r"^(P\d+): .*weak object", prompt, re.MULTILINE)
            return json.dumps({"weak": dict.fromkeys(weak, "an object")})
        labels = re.findall(r"^(F\d+)(?: \(near home\))?:", prompt, re.MULTILINE)
        return json.dumps({"worthy": dict.fromkeys(labels, "an occasion")})


def standing(judge, bank, *, pictures=("a",), subject=""):
    return judge_standing(
        judge,
        pictures=pictures,
        line_of=lambda asset: "weak object" if asset == "a" else f"people at {asset}",
        subject=subject,
        bank=bank,
    )


def test_standing_keeps_a_pictures_rejection_when_its_company_changes():
    judge, bank = VoteJudge(), {}
    assert standing(judge, bank)["a"][0] == 0
    assert standing(judge, bank)["a"][0] == 0
    assert len(judge.calls) == 2

    shifted = (*[f"b{i}" for i in range(12)], "a")
    assert standing(judge, bank, pictures=shifted)["a"][0] == 0
    # The vote is banked under the picture's own line, not under the label it wore, so the
    # twelve newcomers are one block (nothing in it doubted, so no check) and "a" is not asked
    # a third time.
    assert len(judge.calls) == 3
    assert all("weak object" not in prompt for _stage, prompt in judge.calls[2:])


@pytest.mark.parametrize("change", ["subject", "model", "endpoint", "provider", "dialect"])
def test_standing_bank_includes_the_full_question_and_model_settings(change):
    judge, bank = VoteJudge(), {}
    subject = ""
    standing(judge, bank, subject=subject)
    if change == "subject":
        subject = "the garden we built"
    else:
        settings = {
            "model": {"model": "model-b"},
            "endpoint": {"base_url": "https://other.example/v1"},
            "provider": {"provider": "ollama"},
            "dialect": {"extra_params": {"top_k": 20}},
        }[change]
        judge.config.llm = judge.config.llm.model_copy(update=settings)
    standing(judge, bank, subject=subject)
    assert len(judge.calls) == 4


def test_unidentified_judge_does_not_reuse_or_populate_a_persistent_bank():
    judge, bank = VoteJudge(), {}
    del judge.config
    standing(judge, bank)
    standing(judge, bank)
    assert len(judge.calls) == 4
    assert bank == {}


def test_an_adapter_can_supply_its_model_identity_explicitly():
    judge, bank = VoteJudge(), {}
    del judge.config
    kwargs = {
        "pictures": ["a"],
        "line_of": lambda _: "weak object",
        "bank": bank,
        "model_identity": "adapter/model-a/settings-v1",
    }
    judge_standing(judge, **kwargs)
    judge_standing(judge, **kwargs)
    assert len(judge.calls) == 2
    kwargs["model_identity"] = "adapter/model-b/settings-v1"
    judge_standing(judge, **kwargs)
    assert len(judge.calls) == 4


@pytest.mark.parametrize("change", ["near_home", "period", "criterion", "model"])
def test_worthiness_bank_includes_effective_context_and_model(change):
    judge, bank = VoteJudge(), {}
    kwargs = {
        "happenings": ["a"],
        "label_of": {"a": "F01"},
        "text_of": lambda _: "A family outing",
        "near_home": lambda _: False,
        "contract": "contract",
        "contract_key": "contract hash",
        "criterion": "Pick occasions",
        "marker": "",
        "period_label": "period",
        "bank": bank,
    }
    judge_worthiness(judge, **kwargs)
    judge_worthiness(judge, **kwargs)
    assert len(judge.calls) == 2
    if change == "near_home":
        kwargs["near_home"] = lambda _: True
    elif change == "period":
        kwargs["period_label"] = "another period"
    elif change == "criterion":
        kwargs["criterion"] = "Pick subject stages"
    else:
        judge.config.llm = judge.config.llm.model_copy(update={"model": "model-b"})
    judge_worthiness(judge, **kwargs)
    assert len(judge.calls) == 4


@pytest.mark.parametrize("labels", [("F03", "F07", "F08"), ("F13", "F17", "F18")])
def test_worthiness_answer_instructions_do_not_nominate_real_or_foreign_choices(labels):
    judge = VoteJudge()
    happenings = ("visit", "home", "outing")
    judge_worthiness(
        judge,
        happenings=happenings,
        label_of=dict(zip(happenings, labels, strict=True)),
        text_of=lambda key: f"People at {key}",
        near_home=lambda _: None,
        contract="Keep distinct occasions",
        contract_key="test",
        criterion="Judge each happening",
        marker="",
        period_label="one month",
    )
    assert len(judge.calls) == 2
    for _, prompt in judge.calls:
        # The answer shape now sits above the happenings, so find it by its own words
        # rather than by its position in the prompt.
        instructions = next(block for block in prompt.split("\n\n") if "empty mapping" in block)
        assert not re.search(r"F\d+", instructions), (
            "A format example must not suggest any candidates"
        )
        assert set(re.findall(r"^(F\d+):", prompt, re.MULTILINE)) == set(labels)


def test_a_banked_picture_is_answered_without_asking_it_in_new_company():
    # WHY: VoteJudge stands in for the reader; the test counts its calls and reads the prompts
    # it was handed, so no model is involved.
    judge, bank = VoteJudge(), {}
    first = standing(judge, bank, pictures=("a", "b", "c"))
    assert len(judge.calls) == 2

    second = standing(judge, bank, pictures=("c", "d"))
    assert len(judge.calls) == 3, "only the unbanked picture is worth a block"
    asked = [prompt for _stage, prompt in judge.calls[2:]]
    assert all("people at d" in prompt for prompt in asked)
    assert all("people at c" not in prompt for prompt in asked)
    assert second["c"] == first["c"]


EXAMPLE = re.compile(r'exactly once: (\{"weak":\{.*\}\})$', re.MULTILINE)


def offered_and_example(prompt):
    offered = set(re.findall(r"^(P\d+): ", prompt, re.MULTILINE))
    example = set(json.loads(EXAMPLE.search(prompt).group(1))["weak"])
    return offered, example


def test_a_standing_block_far_from_the_first_labels_shows_an_example_of_its_own_labels():
    """A year's ninth block offered P97-P108 and was shown P03/P07; the reader copied them."""
    judge = VoteJudge()
    standing(judge, {}, pictures=tuple(f"asset-{n}" for n in range(30)))

    late = [prompt for _stage, prompt in judge.calls if "P25: " in prompt]
    assert late
    for prompt in late:
        offered, example = offered_and_example(prompt)
        assert example and example <= offered


def test_a_standing_example_never_names_a_label_its_block_did_not_offer():
    judge = VoteJudge()
    standing(judge, {}, pictures=tuple(f"asset-{n}" for n in range(30)))
    standing(VoteJudge(), {}, pictures=("lonely",))

    for _stage, prompt in judge.calls:
        offered, example = offered_and_example(prompt)
        assert example <= offered
