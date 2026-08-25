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

if TYPE_CHECKING:
    from pathlib import Path

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


def looks_like_a_photograph(asset: object) -> bool:
    """Whether the photo look is the right way to see this asset.

    Any image, Live Photo or not. `is_a_still` deliberately excludes a Live
    Photo because every rule about footage applies to how it is RENDERED — but
    the question here is which analyser can read it, and the answer for
    anything with a still is the photo scorer. Sent to the video analyser
    instead, a burst carrier fails in milliseconds, is marked attempted, and
    is never looked at again: the label its burst already carries never
    reaches the review, and it ships undescribed.
    """
    from immich_memories.api.models import AssetType

    return getattr(asset, "type", None) == AssetType.IMAGE


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

    @property
    def _verdicts(self) -> Path:
        """Where a judgement about an identical selection is kept.

        Its own file beside the analysis cache rather than a table inside it:
        that database carries a SCHEMA_VERSION users' stored analysis keys off,
        and this is derived data that may be deleted at any time.
        """
        from immich_memories.cache.judgment_cache import verdicts_beside

        return verdicts_beside(self._app_config.cache.cache_path)

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
            unverified, result = self._settle_the_stills(unverified, by_id, result)
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

    def _settle_the_stills(
        self,
        unverified: list[ClipWithSegment],
        by_id: dict[str, ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[list[ClipWithSegment], PipelineResult]:
        """Look at the stills, let their days reconsider, hand back the footage.

        The look moves their scores as well as their descriptions, so a day
        may now prefer a frame it could not judge before — and re-selection
        has to hear that, or the look changed only the caption.
        """
        from immich_memories.analysis import photo_look

        stills = [u for u in unverified if looks_like_a_photograph(u.clip.asset)]
        deps = {
            "config": self._app_config,
            "client": self.client,
            "provider_circuit": self.provider_circuit,
        }
        photo_look.look_at_stills(stills, **deps)
        if stills:
            photo_look.repick_days(
                selected=[by_id[c.asset.id] for c in result.selected_clips if c.asset.id in by_id],
                pool=list(by_id.values()),
                **deps,
            )
            result = self.refiner.phase_refine(list(by_id.values()), self.tracker)
        return [u for u in unverified if not looks_like_a_photograph(u.clip.asset)], result

    def cut(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment]]:
        """The one holistic pass, and its answer stands (#764).

        This used to be two methods and a loop. The review vetoed a finished
        cut — at most a fifth of it per round — then selection refilled the
        gap, and the refill had never been judged, so the whole thing ran
        again. One real month took eight rounds to remove what the first round
        had already named, and a clip it named three rounds running was capped
        away twice before it went.

        The pass now makes the cut. What it removes is not replaced: a memory
        four seconds short beats one topped up with whatever ranked next,
        which was where a games console and a shelf came from.

        It looks at everything first. The review is told — correctly — never
        to drop a clip for missing information, since a third of a real pool
        has no analysis yet and treating silence as a verdict would gut the
        memory. The cost of that rule is that an unanalysed clip is immune to
        the only quality judgment in the pipeline. Nothing is judged blind, so
        the rule never has to protect anything that ships.

        The pool comes back whole. Only the cut changed, and every clip left
        out of it has its reason on the record — the material is still there
        for a later pass to reconsider.
        """
        if not self._app_config.content_analysis.enabled:
            return result, analyzed
        from immich_memories.analysis.selection_review import review_selection

        result, analyzed = self.verify(analyzed, result)

        by_id = {c.clip.asset.id: c for c in analyzed}
        selected = [by_id[c.asset.id] for c in result.selected_clips if c.asset.id in by_id]
        verdict = review_selection(
            selected,
            self._app_config.llm,
            cache_path=self._verdicts,
            unreadable_ids=self._unreadable_ids(selected),
        )
        if verdict.unanswered:
            # The pass is fail-open and it is the only quality judgment left.
            # Without this the trace reads "llm review, 0 dropped", which is
            # what an approved cut looks like — and the whole uncut selection
            # ships in silence.
            trace.warn(
                f"the fine cut never ran — {verdict.unanswered}. "
                "Nothing was cut; this is NOT an approved cut."
            )
        drops = set(verdict.drops)
        kept = [c for c in result.selected_clips if c.asset.id not in drops]
        if not drops or not kept:
            trace.record("llm review", selected, selected, verdict.fates)
            return result, analyzed

        logger.info(
            "Selection review: the cut is %d clip(s) of %d", len(kept), len(result.selected_clips)
        )
        trace.record(
            "llm review",
            selected,
            [c for c in selected if c.clip.asset.id not in drops],
            verdict.fates,
            verdict.reasons,
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
        return trimmed, analyzed

    def _unreadable_ids(self, selected: list[ClipWithSegment]) -> frozenset[str]:
        """Clips verification has already tried, and still cannot describe.

        Verify never re-queues an attempt — deliberately, so a clip whose
        analysis fails cannot loop forever. That termination guarantee is also
        a blind spot: the clip keeps reaching the review as a bare line, and
        the rule protecting clips nobody has looked at protects it too. Naming
        them lets the review tell the two silences apart.
        """
        return frozenset(
            member.clip.asset.id
            for member in selected
            if member.clip.asset.id in self._verify_attempted and self._needs_a_real_look(member)
        )

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
            {s.clip.asset.id for s in judgeable if s.score < self.config.judge_floor_score},
            selected,
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
