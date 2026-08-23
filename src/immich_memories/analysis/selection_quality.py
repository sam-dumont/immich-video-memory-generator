"""The quality pass over a finished selection: verify, judge, review.

Selection decides what a memory is made of; this decides whether that cut is
good enough to ship. It re-analyzes anything the LLM review would otherwise
judge blind, drops what fails a floor calibrated to the medium, and gives the
review one look at the set as a whole.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from immich_memories.analysis import selection_trace as trace
from immich_memories.analysis.source_filter import is_a_still

if TYPE_CHECKING:
    from immich_memories.analysis.clip_analyzer import ClipAnalyzer
    from immich_memories.analysis.clip_refiner import ClipRefiner
    from immich_memories.analysis.progress import ProgressTracker
    from immich_memories.analysis.provider_health import ProviderCircuit
    from immich_memories.analysis.smart_pipeline import (
        ClipWithSegment,
        PipelineConfig,
        PipelineResult,
    )
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)

# Below this share of the judge's floor a clip is not weak but unusable — a
# pocket, the ground, somebody's feet — and no amount of being the only thing
# from its day makes it worth shipping.
_UNUSABLE_SHARE_OF_FLOOR = 1.0 / 3.0


class SelectionQuality:
    """Verify, judge and review a selection until it is worth shipping.

    Holds the verify pass's memory of what it has already tried, so a clip
    whose analysis fails is not downloaded and decoded again on every entry.
    """

    def __init__(
        self,
        *,
        config: PipelineConfig,
        app_config: Config,
        analyzer: ClipAnalyzer,
        refiner: ClipRefiner,
        tracker: ProgressTracker,
        client: SyncImmichClient,
        provider_circuit: ProviderCircuit,
    ) -> None:
        self.config = config
        self.analyzer = analyzer
        self.refiner = refiner
        self.tracker = tracker
        self.client = client
        self.provider_circuit = provider_circuit
        self._app_config = app_config
        # Clips the verify pass has already looked at, across every entry.
        self._verify_attempted: set[str] = set()

    def stabilize(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment]]:
        """Verify and judge until the selection is stable (#468).

        Found live on the 2026-08-21 demo: a judge (or review) drop
        re-selects, the re-selection admits a NEW fallback-scored clip, and
        a straight verify→judge sequence ships it unverified. The stages
        iterate together until a pass changes nothing or the budget is spent.
        """
        for _ in range(max(1, self.config.max_refinement_passes)):
            result, analyzed = self.verify(analyzed, result)
            result, analyzed, dropped = self._judge(analyzed, result)
            if not dropped:
                break
        return result, analyzed

    def verify(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment]]:
        """Re-analyze any shipped fallback-scored clip and re-select (#468).

        Heavy when cold, cheap when warm: every verified clip lands in the
        analysis cache, so later runs start from real scores. A clip whose
        analysis fails keeps its fallback score but stops being re-queued,
        so the loop always terminates.
        """
        by_id = {c.clip.asset.id: c for c in analyzed}
        # On the pipeline, not the call: this method is re-entered once per
        # stabilize pass and again for the final review, and a clip whose
        # analysis fails can never come back with a description — so a
        # call-local set had it downloaded and decoded again on every entry,
        # for the same failure.
        attempted: set[str] = self._verify_attempted
        for _ in range(max(1, self.config.max_refinement_passes)):
            unverified = [
                by_id[c.asset.id]
                for c in result.selected_clips
                if c.asset.id in by_id
                and c.asset.id not in attempted
                and self._needs_a_real_look(by_id[c.asset.id])
            ]
            if not unverified:
                break
            logger.info(
                "Verify pass: analyzing %d selected clip(s) the review cannot see",
                len(unverified),
            )
            trace.record(
                "verify: analyze unseen",
                result.selected_clips,
                result.selected_clips,
                [f"{len(unverified)} clip(s) analyzed for real before judging"],
            )
            attempted.update(u.clip.asset.id for u in unverified)
            unverified = self._look_at_stills_among(unverified)
            if not unverified:
                continue
            try:
                verified = self.analyzer.phase_analyze([u.clip for u in unverified], self.tracker)
            finally:
                with contextlib.suppress(Exception):
                    self.analyzer.close()
            verified_ids = self._absorb_verified(by_id, verified, unverified)
            for u in unverified:
                if u.clip.asset.id not in verified_ids:
                    # replace, not a fresh ClipWithSegment: the dataclass
                    # lives on the pipeline that composes this service, and
                    # importing it back would close the loop.
                    by_id[u.clip.asset.id] = replace(u, analyzed=True)
            result = self.refiner.phase_refine(list(by_id.values()), self.tracker)
        return result, list(by_id.values())

    def _absorb_verified(
        self,
        by_id: dict[str, ClipWithSegment],
        verified: list[ClipWithSegment],
        unverified: list[ClipWithSegment],
    ) -> set[str]:
        """Take back what the look actually established, and nothing else.

        Only what we asked for: analysis can hand back more than it was given
        (a Live Photo expands into its components), and any extra id lands
        straight back in the pool — resurrecting a clip the judge or the
        review had just dropped.

        And only what it managed to look at. The analyzer returns a
        placeholder scored 0.0 when a download blips or a decode dies, and
        writing that over a real score sends the clip under the judge's floor,
        which drops it from the pool for good — losing a clip to a transient
        error. A failed look is not a verdict of zero.
        """
        requested = {u.clip.asset.id for u in unverified}
        kept = [v for v in verified if v.clip.asset.id in requested]
        for v in kept:
            if not v.analyzed and v.clip.asset.id in by_id:
                logger.debug(
                    "Verify pass: keeping the score for %s, the look failed", v.clip.asset.id
                )
                continue
            by_id[v.clip.asset.id] = v
        return {v.clip.asset.id for v in kept}

    def _look_at_stills_among(self, unverified: list[ClipWithSegment]) -> list[ClipWithSegment]:
        """Look at the stills here and now, and hand back the footage.

        A still's real look is the photo scorer: the video analyzer fails on a
        photograph and writes back a zero, so a photo it could not read was
        not merely unseen but ranked last.
        """
        stills = [u for u in unverified if is_a_still(u.clip.asset)]
        self._look_at_stills(stills)
        return [u for u in unverified if not is_a_still(u.clip.asset)]

    def _look_at_stills(self, stills: list[ClipWithSegment]) -> None:
        """Give the review eyes on the photographs that reached the cut."""
        if not stills:
            return
        from immich_memories.analysis.cache_projection import apply_semantic_payload
        from immich_memories.photos import photo_pipeline

        logger.info("Verify pass: looking at %d selected photo(s)", len(stills))
        try:
            payloads = photo_pipeline.look_at_selected_photos(
                [s.clip.asset for s in stills],
                config=self._app_config,
                client=self.client,
                provider_circuit=self.provider_circuit,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Photo look failed: %s", type(exc).__name__)
            return
        for member in stills:
            apply_semantic_payload(member.clip, payloads.get(member.clip.asset.id))

    def final_review_drop(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment]]:
        """One last review that drops without refilling.

        The iterating review always leaves its own last refill unjudged: it
        stops when the budget runs out, and by then it has just re-selected.
        Refilling again would only admit more unseen clips, so this pass takes
        the cut it has and removes what does not belong.

        It looks at them first. The review is told — correctly — never to drop
        a clip for missing information, since a third of a real pool has no
        analysis yet and treating silence as a verdict would gut the memory.
        The cost of that rule is that an unanalysed clip is immune to the only
        quality judgment in the pipeline, and a rendered year recap shipped
        whiteboards and desks on exactly that immunity. Nothing is judged
        blind, so the rule never has to protect anything that shipped.
        """
        if not self._app_config.content_analysis.enabled:
            return result, analyzed
        from immich_memories.analysis.selection_review import review_selection

        result, analyzed = self.verify(analyzed, result)

        by_id = {c.clip.asset.id: c for c in analyzed}
        selected = [by_id[c.asset.id] for c in result.selected_clips if c.asset.id in by_id]
        drops = set(review_selection(selected, self._app_config.llm))
        if not drops:
            return result, analyzed

        kept = [c for c in result.selected_clips if c.asset.id not in drops]
        if not kept:
            return result, analyzed
        logger.info(
            "Selection review: budget spent, dropping %d unreviewed clip(s) "
            "rather than shipping them",
            len(result.selected_clips) - len(kept),
        )
        trimmed = replace(
            result,
            selected_clips=kept,
            clip_segments={
                asset_id: seg
                for asset_id, seg in result.clip_segments.items()
                if asset_id not in drops
            },
        )
        return trimmed, [c for c in analyzed if c.clip.asset.id not in drops]

    def _needs_a_real_look(self, member: ClipWithSegment) -> bool:
        """Would the LLM review be judging this clip blind?

        A metadata guess for a score is one way to ship unseen. The other is
        subtler and was shipping: a clip carries a real visual score, so it
        counts as analyzed, but no content analysis ever ran on it, so the
        review is handed a bare line. It is then told — correctly — never to
        drop a clip for missing information, and two near-identical hotel
        mirror selfies from consecutive days both survive as a result.

        A photograph is never queued. Its real look is the VLM photo scorer,
        which has already run and now says what it saw; sending a still to the
        video analyzer fails and replaces its score with zero, so a photo the
        scorer could not describe was not merely unseen, it was ranked last.
        """
        if not member.analyzed:
            return True
        if not self._app_config.content_analysis.enabled:
            return False
        return not getattr(member.clip, "llm_description", None)

    def _judge(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment], bool]:
        """One judge sweep (#468): drop offenders, let selection refill.

        A single sweep by design — the caller re-verifies whatever the
        re-selection admitted before judging again.
        """
        by_id = {c.clip.asset.id: c for c in analyzed}
        selected = [by_id[c.asset.id] for c in result.selected_clips if c.asset.id in by_id]
        if len(selected) < 2:
            return result, analyzed, False
        offenders = self.judge_offenders(selected)
        if not offenders:
            return result, analyzed, False
        logger.info(
            "Judge: dropping %d clip(s) below the quality gate, re-selecting",
            len(offenders),
        )
        analyzed = [c for c in analyzed if c.clip.asset.id not in offenders]
        if not analyzed:
            return result, analyzed, False
        result = self.refiner.phase_refine(analyzed, self.tracker)
        return result, analyzed, True

    def _floor_for(self, member: ClipWithSegment) -> float:
        """The gate this clip answers to, on the scale its score was built on.

        photos.score_penalty says outright that a photo scores a fixed share
        of a video, so a floor calibrated on footage is that much too high for
        a still. A no-people, non-favorite photo tops out at 0.15 base + 0.05
        camera + half the LLM weight — 0.28 after the penalty, against a floor
        of 0.30. Landscapes, pets and scenery could not clear it at all, and
        were dropped as a class along with any day only they represented.
        """
        floor = self.config.judge_floor_score
        if not is_a_still(member.clip.asset):
            return floor
        return floor * (1.0 - self._app_config.photos.score_penalty)

    def spare_last_voices(
        self,
        offenders: set[str],
        selected: list[ClipWithSegment],
    ) -> set[str]:
        """Offenders to actually drop, keeping any that is a period's last voice.

        The judge removes offenders from the pool for good, so read as "this
        clip is bad" its verdict costs a month whose only clip scored low. Read
        as "we can do better than this clip" it costs nothing, because when
        there is nothing better the clip stays.

        Unusable is different from weak, and the distinction is what makes this
        safe: a shot of the ground or a pocket is not worth a month, and stays
        dropped however alone it is. Only a clip that would pass on any other
        day is worth keeping for lack of an alternative.

        A correction, not an exemption. Exempting has to guess in advance which
        clips carry a period, and both ways of guessing that switch the gate
        off under ordinary conditions — coverage ids are every clip on a pool
        with no favorites, and a distributed selection makes almost every clip
        the only one of its day.
        """
        from immich_memories.analysis.clip_distribution import _period_key, span_days_of

        unusable = self.config.judge_floor_score * _UNUSABLE_SHARE_OF_FLOOR
        span = span_days_of(selected)

        def period_of(member: ClipWithSegment) -> str | None:
            when = member.clip.asset.file_created_at
            return _period_key(when, span) if when else None

        surviving = {period_of(m) for m in selected if m.clip.asset.id not in offenders}
        spared = {
            m.clip.asset.id
            for m in selected
            if m.clip.asset.id in offenders
            and m.score >= unusable
            and period_of(m) not in surviving
        }
        if spared:
            logger.info(
                "Judge: keeping %d weak clip(s) — nothing else covers their period",
                len(spared),
            )
        return offenders - spared

    def judge_offenders(self, selected: list[ClipWithSegment]) -> set[str]:
        """Members failing the gate. Favorites are exempt from both rules —
        the user explicitly chose them, and "Starting with ALL favorites" is
        the selection's oldest contract.

        What carries a period is handled after this rather than here — see
        spare_last_voices. Exempting up front needs a guess about which clips
        carry a period, and every way of guessing switches the gate off.
        """
        spared = {s.clip.asset.id for s in selected if getattr(s.clip.asset, "is_favorite", False)}
        judgeable = [s for s in selected if s.clip.asset.id not in spared]
        # Sparing applies to the floor rule only. The ending rule below is
        # about where a clip sits, not whether it is worth keeping — a clip
        # dropped for being a weak last note is fine anywhere else, and
        # keeping it for lack of an alternative would defeat the rule.
        offenders = self.spare_last_voices(
            {s.clip.asset.id for s in judgeable if s.score < self._floor_for(s)}, selected
        )
        scores = [s.score for s in selected]
        mean_score = sum(scores) / len(scores)
        ending = max(
            selected,
            key=lambda s: s.clip.asset.file_created_at or datetime.min.replace(tzinfo=UTC),
        )
        if (
            len(selected) > 2
            and ending.clip.asset.id not in spared
            and ending.score == min(scores)
            and ending.score < mean_score * self.config.judge_boundary_ratio
        ):
            offenders.add(ending.clip.asset.id)
        return offenders

    def review(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment], bool]:
        """One LLM pass over the finished cut (#468): redundancy and feel.

        The mechanical judge sees scores; only something reading the
        descriptions can see the same birthday candles twice. Optional by
        construction — no LLM, no drops, selection unchanged.
        """
        if not self._app_config.content_analysis.enabled:
            return result, analyzed, False
        from immich_memories.analysis.selection_review import review_selection

        by_id = {c.clip.asset.id: c for c in analyzed}
        selected = [by_id[c.asset.id] for c in result.selected_clips if c.asset.id in by_id]
        drops = review_selection(selected, self._app_config.llm)
        if not drops:
            trace.record("llm review", selected, selected)
            return result, analyzed, False
        dropped = set(drops)
        trace.record(
            "llm review",
            selected,
            [c for c in selected if c.clip.asset.id not in dropped],
        )
        remaining = [c for c in analyzed if c.clip.asset.id not in dropped]
        if not remaining:
            return result, analyzed, False
        # WHY the pool shrinks too: a later stabilization re-refines from the
        # pool — returning the old one would resurrect the LLM's drops.
        return self.refiner.phase_refine(remaining, self.tracker), remaining, True
