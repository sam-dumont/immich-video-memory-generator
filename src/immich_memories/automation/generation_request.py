"""Typed boundary from automation candidates to the public generate CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from immich_memories.automation.candidates import CandidateCategory, MemoryCandidate


@dataclass(frozen=True)
class GenerationRequest:
    """An immutable, exhaustively mapped automation generation request."""

    memory_type: str
    category: CandidateCategory
    memory_key: str
    start: date
    end: date
    people: tuple[str, ...] = ()
    upload: bool = False
    album_name: str | None = None
    automation_attempt_id: str | None = None
    config_path: Path | None = None

    @classmethod
    def from_candidate(
        cls,
        candidate: MemoryCandidate,
        upload: bool,
        automation_attempt_id: str | None = None,
        config_path: Path | None = None,
        album_name: str | None = None,
    ) -> GenerationRequest:
        """Validate a candidate category and choose its rendering preset."""
        match candidate.category:
            case CandidateCategory.MONTHLY_REVIEW | CandidateCategory.ACTIVITY_BURST:
                memory_type = "monthly_highlights"
            case CandidateCategory.YEAR_IN_REVIEW:
                memory_type = "year_in_review"
            case CandidateCategory.PERSON_SPOTLIGHT | CandidateCategory.BIRTHDAY:
                memory_type = "person_spotlight"
            case CandidateCategory.MULTI_PERSON:
                memory_type = "multi_person"
            case CandidateCategory.ON_THIS_DAY:
                memory_type = "on_this_day"
            case CandidateCategory.TRIP:
                memory_type = "trip"
            case CandidateCategory.EMERGENT_DAY:
                memory_type = "special_day"
            case _:
                raise ValueError(f"Unsupported automation category: {candidate.category!r}")

        return cls(
            memory_type=memory_type,
            category=candidate.category,
            memory_key=candidate.memory_key,
            start=candidate.date_range_start,
            end=candidate.date_range_end,
            people=tuple(candidate.person_names),
            upload=upload,
            album_name=album_name,
            automation_attempt_id=automation_attempt_id,
            config_path=config_path,
        )

    def to_argv(self) -> list[str]:
        """Build shell-safe argv for the public generate command."""
        argv = ["immich-memories"]
        if self.config_path is not None:
            argv.extend(["--config", str(self.config_path)])
        argv.extend(["generate", "--memory-type", self.memory_type])

        match self.category:
            case CandidateCategory.MONTHLY_REVIEW | CandidateCategory.ACTIVITY_BURST:
                argv.extend(["--year", str(self.start.year), "--month", str(self.start.month)])
            case CandidateCategory.YEAR_IN_REVIEW:
                argv.extend(["--year", str(self.start.year)])
            case CandidateCategory.PERSON_SPOTLIGHT:
                argv.extend(["--year", str(self.start.year)])
                argv.extend(f"--person={name}" for name in self.people)
            case CandidateCategory.BIRTHDAY:
                # WHY end and not start: --year names the birthday being
                # celebrated, and a birthday memory is the year *leading up to*
                # it -- so the year the window ends in is the one to ask for.
                argv.extend(["--year", str(self.end.year), "--birthday"])
                argv.extend(f"--person={name}" for name in self.people)
            case CandidateCategory.MULTI_PERSON:
                argv.extend(["--year", str(self.start.year)])
                argv.extend(f"--person={name}" for name in self.people)
            case CandidateCategory.ON_THIS_DAY:
                argv.append(f"--automation-target-date={self.start.isoformat()}")
            case CandidateCategory.TRIP:
                argv.extend(
                    [
                        "--year",
                        str(self.start.year),
                        "--start",
                        self.start.isoformat(),
                        "--end",
                        self.end.isoformat(),
                    ]
                )
            case CandidateCategory.EMERGENT_DAY:
                # A date, and nothing else. The child re-reads the catalogue for
                # the day's name: this argv is logged in full and is readable in
                # `ps`, and the catalogue's titles name real people and places.
                argv.extend(["--day", self.start.isoformat()])
            case _:
                raise ValueError(f"Unsupported automation category: {self.category!r}")

        argv.extend(
            [
                "--source=auto",
                f"--memory-key={self.memory_key}",
                f"--memory-category={self.category.value}",
            ]
        )
        if self.upload:
            argv.append("--upload-to-immich")
            if self.album_name is not None:
                argv.extend(["--album", self.album_name])
        if self.automation_attempt_id is not None:
            argv.append(f"--automation-attempt-id={self.automation_attempt_id}")
        return argv
