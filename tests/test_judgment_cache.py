"""A judgement about an identical question should not be paid for twice.

A second run of the same memory asks the model the same things again. In
reasoning mode each of those costs 5-10x the latency and 10-20x the tokens of a
fast call, so the bank keys every verdict by the exact question asked.
"""


def test_concurrent_banking_from_many_threads_keeps_every_answer(tmp_path) -> None:
    """Readers hammer one cache from a thread pool; nothing may be lost."""
    from concurrent.futures import ThreadPoolExecutor

    from immich_memories.cache.judgment_cache import JudgmentCache

    cache = JudgmentCache(tmp_path / "judgments.db")

    def bank(worker: int) -> None:
        for index in range(25):
            key = f"k-{worker}-{index}"
            cache.remember(key, f"answer-{worker}-{index}")
            assert cache.answer_for(key) == f"answer-{worker}-{index}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(bank, range(8)))

    assert cache.answer_for("k-7-24") == "answer-7-24"
    cache.close()


def test_a_closed_cache_reopens_on_the_next_question(tmp_path) -> None:
    """close() releases the file; a later question quietly reconnects (fail-open)."""
    from immich_memories.cache.judgment_cache import JudgmentCache

    cache = JudgmentCache(tmp_path / "judgments.db")
    cache.remember("k", "a")
    cache.close()

    assert cache.answer_for("k") == "a"
    cache.remember("k2", "b")
    assert cache.answer_for("k2") == "b"
    cache.close()


def test_reasoning_dialect_is_part_of_the_question_even_at_the_same_prompt() -> None:
    """Two budgets are two different answers; only one of them may be replayed."""
    from immich_memories.cache.judgment_cache import judgment_key

    base = judgment_key(model="qwen", prompt="same evidence", thinking=True)

    assert base != judgment_key(model="qwen", prompt="same evidence", thinking=False)
    assert base != judgment_key(model="llama", prompt="same evidence", thinking=True)
    assert base != judgment_key(model="qwen", prompt="other evidence", thinking=True)
    assert base != judgment_key(
        model="qwen", prompt="same evidence", thinking=True, thinking_identity="budget=256"
    )


def test_a_bounded_failure_replays_without_ever_being_served_as_an_answer(tmp_path) -> None:
    """The stage must fall back again rather than read an exhausted attempt as a verdict."""
    from immich_memories.cache.judgment_cache import JudgmentCache

    cache = JudgmentCache(tmp_path / "judgments.db")
    record = {"schema_version": "bounded-text-completion-v1", "attempts": [{"raw": ""}]}
    cache.remember_completion_failure("k", record)

    try:
        assert cache.completion_failure_for("k") == record
        assert cache.answer_for("k") is None
        assert cache.completion_failure_for("never-asked") is None
    finally:
        cache.close()


def test_a_cache_that_cannot_be_opened_forgets_failures_too(tmp_path) -> None:
    """Losing the cache is a cost, never a failure — including on the failure table."""
    from immich_memories.cache.judgment_cache import JudgmentCache

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")

    cache = JudgmentCache(blocked / "judgments.db")
    cache.remember_completion_failure("k", {"attempts": []})

    assert cache.completion_failure_for("k") is None
