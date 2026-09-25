"""The no-pictures guard: pictures are read once, at ingest, and never at film time."""

import pytest


def refuse_pictures(monkeypatch) -> list[int]:
    """Every model request passes this one dispatch; a picture in one fails the test.

    Pictures are read once, at ingest. A film-time request carrying one is a defect whatever
    stage sent it (selection, render or music), so the guard sits where every stage's request
    meets the wire. The list holds how many pictures each request carried.
    """
    from immich_memories.analysis import llm_query

    sent: list[int] = []
    real = llm_query._dispatch

    async def dispatch(prompt, llm_config, temperature, max_tokens, timeout, thinking, images, *a):
        sent.append(len(images))
        if images:
            pytest.fail(f"a film-time request sent {len(images)} picture(s) to the reader")
        return await real(
            prompt, llm_config, temperature, max_tokens, timeout, thinking, images, *a
        )

    # WHY: the provider wire; this sees what would leave the machine and refuses pictures.
    monkeypatch.setattr(llm_query, "_dispatch", dispatch)
    return sent
