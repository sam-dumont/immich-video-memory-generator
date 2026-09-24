"""A vote bank must replay the same model question, including its positional labels."""

import json
import re
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_block_votes import judge_worthiness
from immich_memories.config_models_llm import LLMConfig


class VoteJudge:
    def __init__(self, **settings):
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a", **settings))
        self.calls = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append((stage, prompt))
        labels = re.findall(r"^(F\d+)(?: \(near home\))?:", prompt, re.MULTILINE)
        return json.dumps({"worthy": dict.fromkeys(labels, "an occasion")})


def worthiness(judge, bank, **overrides):
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
    return judge_worthiness(judge, **(kwargs | overrides))


def test_unidentified_judge_does_not_reuse_or_populate_a_persistent_bank():
    judge, bank = VoteJudge(), {}
    del judge.config
    worthiness(judge, bank)
    worthiness(judge, bank)
    assert len(judge.calls) == 4
    assert bank == {}


def test_an_adapter_can_supply_its_model_identity_explicitly():
    judge, bank = VoteJudge(), {}
    del judge.config
    worthiness(judge, bank, model_identity="adapter/model-a/settings-v1")
    worthiness(judge, bank, model_identity="adapter/model-a/settings-v1")
    assert len(judge.calls) == 2
    worthiness(judge, bank, model_identity="adapter/model-b/settings-v1")
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
