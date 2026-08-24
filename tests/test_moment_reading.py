"""Read a moment from contact sheets: what it was, and which frames to keep.

Curation runs moment-first: get the rough idea of what a moment is, get the
best pictures out of it, analyse only those, then judge the memory as a whole.
This module is the first step — the cheap, wide pass that turns a pile of
photographs into a reading of what happened and a shortlist worth the
expensive look.

Sending ONE tiled sheet rather than N images is what makes it affordable:
measured on a real day, 215 photographs read in 14 calls and 22 seconds, where
describing each one costs about 1.5s apiece.
"""

from __future__ import annotations

from immich_memories.analysis.moment_reading import (
    SheetReading,
    read_sheet_verdict,
    sheets_of,
)


class TestReadingWhatTheModelSaid:
    def test_a_verdict_names_the_moment_and_the_frames_to_keep(self) -> None:
        raw = (
            '{"about": "a birth in an operating theatre", '
            '"subjects": ["a newborn", "two adults"], '
            '"best": [4, 7], "why": "these show the moment itself"}'
        )
        verdict = read_sheet_verdict(raw)
        assert verdict == SheetReading(
            about="a birth in an operating theatre",
            subjects=("a newborn", "two adults"),
            keep=(4, 7),
        )

    def test_a_fenced_answer_still_reads(self) -> None:
        """Thinking-mode answers arrive fenced; the pipeline sees both."""
        raw = '```json\n{"about": "a track day", "subjects": ["a car"], "best": [1]}\n```'
        verdict = read_sheet_verdict(raw)
        assert verdict is not None
        assert verdict.about == "a track day"

    def test_an_answer_without_a_reading_is_no_reading(self) -> None:
        """A truncated sheet must not read as a moment about nothing."""
        assert read_sheet_verdict('{"subjects": ["a car"], "best": [1]}') is None
        assert read_sheet_verdict("the photographs show a car on a track") is None
        assert read_sheet_verdict("") is None


class TestCuttingAMomentIntoSheets:
    def test_a_moment_is_covered_by_whole_sheets(self) -> None:
        """Every photograph reaches a sheet: coverage is the point."""
        sheets = sheets_of(list(range(35)), per_sheet=16)
        assert [len(s) for s in sheets] == [16, 16, 3]
        assert [item for sheet in sheets for _n, item in sheet] == list(range(35))

    def test_tiles_are_numbered_across_the_whole_moment(self) -> None:
        """The model answers with tile numbers, so they cannot restart per sheet."""
        sheets = sheets_of(list(range(20)), per_sheet=16)
        assert sheets[0][0][0] == 1
        assert sheets[1][0][0] == 17


class TestTilingASheet:
    def _frame(self, colour: tuple[int, int, int], size=(80, 60)):
        from PIL import Image

        return Image.new("RGB", size, colour)

    def test_a_sheet_holds_every_frame_it_was_given(self) -> None:
        """A frame that misses its sheet is a photograph the moment never saw."""
        from immich_memories.analysis.moment_reading import tile_sheet

        frames = [(n, self._frame((n * 20 % 255, 40, 60))) for n in range(1, 7)]
        sheet = tile_sheet(frames)
        assert sheet.width > 0 and sheet.height > 0
        # Six frames at four across is two rows, so the sheet is not one strip.
        assert sheet.height > sheet.width / 4

    def test_a_frame_that_could_not_be_fetched_does_not_lose_the_sheet(self) -> None:
        """Thumbnails 404 in a real library; the other fifteen still read."""
        from immich_memories.analysis.moment_reading import tile_sheet

        frames = [(1, self._frame((10, 10, 10))), (2, None), (3, self._frame((90, 10, 10)))]
        sheet = tile_sheet(frames)
        assert sheet is not None

    def test_no_frames_is_no_sheet(self) -> None:
        from immich_memories.analysis.moment_reading import tile_sheet

        assert tile_sheet([]) is None


class TestWhoWasThere:
    """Immich knows who is in a photograph; the model should not have to guess.

    Left to read a wristband it misread the name. Told who is present it gets
    it right, and told how old they were that day it stops describing a
    newborn as one of the parents.
    """

    def _asset(self, when, people):
        from types import SimpleNamespace

        return SimpleNamespace(file_created_at=when, people=people)

    def _person(self, name, born=None):
        from types import SimpleNamespace

        return SimpleNamespace(name=name, birth_date=born)

    def test_names_reach_the_reading_against_their_tile(self) -> None:
        from datetime import UTC, datetime

        from immich_memories.analysis.moment_reading import who_was_there

        when = datetime(2020, 5, 1, tzinfo=UTC)
        block = who_was_there([(3, self._asset(when, [self._person("A Name")]))])
        assert "photo 3" in block
        assert "A Name" in block

    def test_a_newborn_is_described_as_one(self) -> None:
        """Without the age it read the baby as a parent."""
        from datetime import UTC, datetime

        from immich_memories.analysis.moment_reading import who_was_there

        when = datetime(2020, 5, 1, tzinfo=UTC)
        block = who_was_there([(1, self._asset(when, [self._person("A Name", born=when)]))])
        assert "newborn" in block

    def test_nobody_identified_says_so_rather_than_nothing(self) -> None:
        """An empty block reads as "no instruction"; the model then guesses."""
        from datetime import UTC, datetime

        from immich_memories.analysis.moment_reading import who_was_there

        block = who_was_there([(1, self._asset(datetime(2020, 5, 1, tzinfo=UTC), []))])
        assert block.strip()
        assert "photo 1" not in block


class TestReadingAMoment:
    """The whole cheap pass: sheets in, a reading and a shortlist out."""

    def _asset(self, n):
        from datetime import UTC, datetime, timedelta
        from types import SimpleNamespace

        return SimpleNamespace(
            id=f"a{n}",
            people=[],
            file_created_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=n),
        )

    def _frames(self, n):
        from PIL import Image

        return {f"a{i}": Image.new("RGB", (40, 30), (i * 7 % 255, 20, 20)) for i in range(n)}

    def test_a_moment_reads_as_its_sheets_and_keeps_what_they_chose(self) -> None:
        from unittest.mock import AsyncMock, patch

        from immich_memories.analysis.moment_reading import read_moment
        from immich_memories.config_models_llm import LLMConfig

        assets = [self._asset(i) for i in range(20)]
        answer = '{"about": "a walk", "subjects": ["a dog"], "best": [2, 18]}'

        # WHY: the model is the external boundary; this asserts what the pass does with an answer,
        with patch(
            "immich_memories.analysis.llm_query.query_llm",
            new=AsyncMock(return_value=answer),
        ) as asked:
            reading = read_moment(assets, self._frames(20), LLMConfig(model="m"), keep_cap=None)

        # 20 photographs is two sheets, so two calls — not twenty.
        assert asked.await_count == 2
        assert reading.about == "a walk"
        # Tiles are 1-based, and each sheet answers only for its own numbers:
        # both sheets said [2, 18], so sheet one contributes tile 2 and sheet
        # two contributes tile 18 — neither reaches into the other.
        assert [a.id for a in reading.keep] == ["a1", "a17"]

    def test_a_moment_no_sheet_could_read_keeps_nothing(self) -> None:
        """Better an empty shortlist than a moment invented from a failure."""
        from unittest.mock import AsyncMock, patch

        from immich_memories.analysis.moment_reading import read_moment
        from immich_memories.config_models_llm import LLMConfig

        # WHY: the model is the external boundary; an unreadable answer is what a truncated or ref
        with patch(
            "immich_memories.analysis.llm_query.query_llm",
            new=AsyncMock(return_value="the server said something else"),
        ):
            reading = read_moment(
                [self._asset(0)], self._frames(1), LLMConfig(model="m"), keep_cap=None
            )

        assert reading.about == ""
        assert reading.keep == ()
