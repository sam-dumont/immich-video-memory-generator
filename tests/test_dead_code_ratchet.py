"""The vulture whitelist is a ratchet, not a parking lot.

`vulture --make-whitelist` regenerates the file wholesale, so a change that
leaves something dead behind can be absolved by rerunning the command: the new
entry joins the list and the gate goes green. This is the same shape as the
complexity snapshot, and it fails the same way — quietly.

The count below is the agreed size of the backlog. It may fall. It may not rise
without someone saying so in a commit.
"""

from __future__ import annotations

from pathlib import Path

WHITELIST = Path(__file__).resolve().parent.parent / "vulture-whitelist.py"

# Lower this as entries are cleared. Raise it only for a false positive vulture
# cannot see through, and never to silence something genuinely dead. The
# MemoryType.ALBUM entry that briefly lived here is the worked example: the
# right fix turned out to be using the enum instead of a bare string, which
# made the reference visible and the whitelist line unnecessary.
# 316, down from 322: the nine @register_preset functions were whitelisted one
# by one because vulture sees a definition nobody calls -- the decorator puts
# them in a dict. Telling vulture about the decorator (`make dead-code`) removes
# the whole class of false positive, so the entries went rather than growing by
# two when HOLIDAY and THEN_AND_NOW landed.
# 306, down from 318: #502 retired the photo animation stack nobody could reach
# (PhotoAnimator, the FFmpeg filter expressions, the grouper, AnimationMode).
# Nine entries went with the code they were excusing.
# 358, up from 306: the story-first selection route arrived with the whole
# engine behind it, and the temporary "story-first port slice N" lines the
# bottom-up port used are gone.
# 332, down from 358: the 28 "public entry points the suite drives directly"
# were not entry points -- their only callers were their own tests, which makes
# them dead in the product. Each went, with those tests, and so did the three
# CLI/UI functions kept alive purely by patches asserting they were never
# called, and the get_video_metadata reader whose writer went with them.
# Six lines came back the other way: the legacy selector (SmartPipeline's
# run_analysis/run_planning_analysis/run_selection and the three _candidate_pool
# stages) lost its last caller in src/ when those CLI functions went, and was
# listed rather than deleted until the PR that removed it.
# 267, down from 332: the legacy selector went, with every module only it
# reached (the clip analyzers, the scorers, speech, the photo scorer, the
# density budget) and the config dials only they read. Fifty lines left: the
# six above, the whitelisted parts of the deleted modules, and the lines that
# had gone stale before this -- symbols already removed whose entries nobody
# had pruned, since vulture never complains about a whitelist line that names
# nothing -- and description_llm, a config section nothing read. One line came
# the other way: Asset.file_modified_at, an Immich wire field the loader fills
# that lost its last reader with the analysis cache.
# What remains is one permanent block at the end of the whitelist, in classes
# vulture cannot see through: pydantic fields and validators built from the
# schema by name; Protocol parameter names and the attributes onnxruntime's
# SessionOptions owns; frozen record fields written at construction and read
# back out of the private artifact JSON; and the attempt reader the phase-2
# review page consumes.
# 268, up from 267: ExifInfo.iso came back. It has no reader, but the sample and
# motion evidence digests hash the whole Immich asset payload, so dropping it made
# two assets Immich reports differently one evidence key and re-keyed every banked
# observation of them. It belongs with file_modified_at above: an Immich wire field
# the loader fills, kept because the digest binds it, not because code reads it.
# 253, down from 268: pydantic's validators and serializers are the same false
# positive as @register_preset above -- the schema calls them, never a name vulture
# can follow -- and they had been accumulating one method at a time. `make dead-code`
# now names those decorators too, so 16 entries went.
MAX_WHITELISTED_SYMBOLS = 253


def test_the_dead_code_whitelist_never_grows() -> None:
    entries = [
        line
        for line in WHITELIST.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(entries) <= MAX_WHITELISTED_SYMBOLS, (
        f"{len(entries)} symbols are whitelisted as dead, above the agreed "
        f"{MAX_WHITELISTED_SYMBOLS}. Regenerating the whitelist hides new dead "
        f"code rather than removing it — delete the symbol, or raise "
        f"MAX_WHITELISTED_SYMBOLS deliberately."
    )
