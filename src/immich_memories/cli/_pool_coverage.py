"""Say, on the CLI, when a selection ranked most of its pool without looking.

Its own module because the caller is `run_pipeline_and_generate`, which is at
its complexity ceiling: this owns the whole decision — including whether to
speak at all — so adding it costs that function one unconditional call and no
branch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from immich_memories.cli._helpers import print_info

if TYPE_CHECKING:
    from collections.abc import Callable

    from immich_memories.analysis.selection_coverage import AnalysisCoverage

__all__ = ["report_pool_coverage"]


def report_pool_coverage(
    coverage: AnalysisCoverage,
    *,
    emit: Callable[[str], None] = print_info,
) -> None:
    """Print the one-line coverage warning, when the run has earned one.

    Nothing is printed for a well-analyzed pool — a line on every run is a
    line nobody reads on the run that needed it.
    """
    from immich_memories.analysis.selection_coverage import thin_coverage_notice

    notice = thin_coverage_notice(coverage)
    if notice:
        emit(notice)
