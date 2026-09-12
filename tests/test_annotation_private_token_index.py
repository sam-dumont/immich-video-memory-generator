"""Indexed annotation privacy checks preserve literal substring decisions."""

import random
import string

import pytest

from immich_memories.analysis.annotation_lines import _private_token_matcher


@pytest.mark.parametrize(
    ("tokens", "text", "expected"),
    [
        ([], "anything", False),
        ([""], "", True),
        (["abcdef12"], "prefixABCDEF12suffix", True),
        (["abcdefgh-one", "abcdefgh-two"], "abcdefgh-three", False),
        (["abcdefgh-one", "abcdefgh-two"], "xabcdefgh-two!", True),
        (["abcdefgh", "abcdefgh-long"], "abcdefgh-short", True),
        (["strasse-identifier"], "Die STRAẞE-IDENTIFIER!", True),
        (["12345678"], "1234567", False),
        (["12345678"], "one 12345678", True),
        (["aaaabbbbcccc"], "aaaaaaaabbbbcccc", True),
    ],
)
def test_literal_matching_boundaries(tokens, text, expected):
    assert _private_token_matcher(frozenset(tokens))(text) is expected


def test_index_matches_original_search_across_overlaps_casefold_and_token_lengths():
    rng = random.Random(2024)
    alphabet = string.ascii_lowercase + string.digits + "-_ßé"
    for _ in range(60):
        tokens = frozenset(
            "".join(rng.choices(alphabet, k=rng.randrange(0, 45))).casefold() for _ in range(35)
        )
        contains = _private_token_matcher(tokens)
        for _ in range(20):
            text = "".join(rng.choices(alphabet, k=90))
            if rng.random() < 0.5:
                text = text[:30] + rng.choice(sorted(tokens)).upper() + text[30:]
            assert contains(text) == any(token in text.casefold() for token in tokens)
