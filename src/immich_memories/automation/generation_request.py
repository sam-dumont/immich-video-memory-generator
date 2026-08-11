"""Typed boundary from automation candidates to the public generate CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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
    automation_attempt_id: str | None = None

    @classmethod
    def from_candidate(
        cls,
        candidate: MemoryCandidate,
        upload: bool,
        automation_attempt_id: str | None = None,
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
            automation_attempt_id=automation_attempt_id,
        )

    def to_argv(self) -> list[str]:
        """Build shell-safe argv for the public generate command."""
        argv = ["immich-memories", "generate", "--memory-type", self.memory_type]

        match self.category:
            case CandidateCategory.MONTHLY_REVIEW | CandidateCategory.ACTIVITY_BURST:
                argv.extend(["--year", str(self.start.year), "--month", str(self.start.month)])
            case CandidateCategory.YEAR_IN_REVIEW:
                argv.extend(["--year", str(self.start.year)])
            case CandidateCategory.PERSON_SPOTLIGHT:
                argv.extend(["--year", str(self.start.year)])
                argv.extend(f"--person={name}" for name in self.people)
            case CandidateCategory.BIRTHDAY:
                argv.extend(["--year", str(self.start.year), "--birthday"])
                argv.extend(f"--person={name}" for name in self.people)
            case CandidateCategory.MULTI_PERSON:
                argv.extend(["--year", str(self.start.year)])
                argv.extend(f"--person={name}" for name in self.people)
            case CandidateCategory.ON_THIS_DAY:
                pass
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
        if self.automation_attempt_id is not None:
            argv.append(f"--automation-attempt-id={self.automation_attempt_id}")
        return argv
