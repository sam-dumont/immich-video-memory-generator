"""Ask a cell's reader the three questions the run will ask, before paying for the run.

A reader cell is an hour of pictures and a bill. Run 1 spent both on four hosted
models that answered every read with HTTP 200 and content "", because a reasoning
model charges its thinking to the same budget as its answer. That is three calls'
worth of evidence, and this asks for exactly those three: the episode read, the
period read and one story pick, at the sizes the run uses, against the cell's own
configuration built the way the runner builds it.

One call per shape, deliberately. The stages wrap `query_llm` in a retry that
doubles the budget or repairs the JSON; a probe wants the first answer, not the
recovered one, and a budget of three calls per model is what keeps this free
enough to run before every cell.

    uv run python scripts/setup_matrix_probe_readers.py --cell mac-hosted-openai-luna
    uv run python scripts/setup_matrix.py --probe-readers-only --lane mac
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from setup_matrix_plan import (  # noqa: E402
    CellPlan,
    Plan,
    PlanError,
    build_plan,
    load_manifest,
    reads_with_a_model,
)

from immich_memories.analysis.editorial_json_completion import complete_final_json  # noqa: E402
from immich_memories.analysis.editorial_story_pick_contract import _read_pick  # noqa: E402
from immich_memories.analysis.llm_providers import resolved_llm_config  # noqa: E402
from immich_memories.analysis.llm_query import query_llm  # noqa: E402
from immich_memories.analysis.llm_wire import LLMTransportAttempt  # noqa: E402
from immich_memories.analysis.provider_status import watch_provider  # noqa: E402
from immich_memories.analysis.text_episode_answers import (  # noqa: E402
    _EpisodeRequestScope,
    _read_response_result,
)
from immich_memories.analysis.text_episode_reader import (  # noqa: E402
    TEXT_EPISODE_MAX_OUTPUT_TOKENS,
)
from immich_memories.analysis.text_period_insight import TEXT_PERIOD_MAX_OUTPUT_TOKENS  # noqa: E402
from immich_memories.analysis.text_period_wire import (  # noqa: E402
    _read_response,
    _response_problem,
)
from immich_memories.config_loader import Config  # noqa: E402
from immich_memories.store.episode_readings import EpisodeReadingIdentity  # noqa: E402
from immich_memories.store.period_insights import (  # noqa: E402
    PeriodEpisodeGrounding,
    PeriodInsightIdentity,
)

PROMPTS = Path(__file__).resolve().parent / "reader_probe_prompts"
# The moment grant the recorded story pick was asked under; the contract reads
# `unused_slots` against it, so a different number would reject a valid answer.
STORY_PICK_SLOTS = 8
STORY_PICK_MAX_TOKENS = max(300, 100 + 10 * STORY_PICK_SLOTS)


@dataclass(frozen=True)
class ShapeResult:
    """What one shape cost and whether the stage that asks it could read the answer."""

    shape: str
    model: str
    status: str
    finish_reason: str
    content_chars: int
    completion_tokens: int
    reasoning_tokens: int
    prompt_tokens: int
    seconds: float
    cost: str
    verdict: str

    @property
    def failed(self) -> bool:
        return not self.verdict.startswith("ok")

    def row(self) -> str:
        return (
            f"  {self.shape:<11} {self.status:<4} {self.finish_reason:<7} "
            f"{self.prompt_tokens:>6} in {self.completion_tokens:>6} out "
            f"{self.reasoning_tokens:>6} think {self.content_chars:>6} chars "
            f"{self.seconds:>6.1f}s {self.cost:>13}  {self.verdict}"
        )


HEADER = (
    "  shape       code finish   prompt    answer    thinking    written"
    "    wall           cost  what the stage's parser made of it"
)


def _episode_scopes(prompt: str) -> tuple[_EpisodeRequestScope, ...]:
    """The request the recorded episode prompt describes, as the parser is handed it.

    The aliases the answer cites are positional -- "episode 3", "asset 2" -- so
    the parser needs one scope per episode carrying as many asset ids as that
    episode offered, and nothing else about them matters here.
    """
    scopes = []
    counts: list[int] = []
    for line in prompt.splitlines():
        if re.fullmatch(r"episode \d+", line.strip()):
            counts.append(0)
        elif counts and line.strip().startswith("asset "):
            counts[-1] += 1
    for number, assets in enumerate(counts, start=1):
        ids = tuple(f"episode-{number}-asset-{index}" for index in range(1, assets + 1))
        scopes.append(
            _EpisodeRequestScope(
                identity=EpisodeReadingIdentity(
                    group_id=f"probe-episode-{number}",
                    producer_key="reader-probe",
                    evidence_key=f"probe-evidence-{number}",
                ),
                full_asset_ids=ids,
                page_asset_ids=ids,
                page_number=1,
                page_count=1,
            )
        )
    return tuple(scopes)


def _period_grounding(prompt: str) -> tuple[PeriodEpisodeGrounding, ...]:
    """One grounding row per numbered table row, so cited aliases resolve as they would."""
    rows = []
    for line in prompt.splitlines():
        match = re.match(r"^(\d+)\t", line)
        if match is None:
            continue
        number = int(match.group(1))
        rows.append(
            PeriodEpisodeGrounding(
                episode_id=f"probe-episode-{number}",
                evidence_key=f"probe-evidence-{number}",
                rendered_line=line,
                representative_asset_ids=(f"probe-asset-{number}",),
            )
        )
    return tuple(rows)


def _story_pick_labels(prompt: str) -> set[str]:
    return set(re.findall(r"^(M\d+) \|", prompt, flags=re.MULTILINE))


def _episode_verdict(raw: str, prompt: str) -> str:
    scopes = _episode_scopes(prompt)
    result = _read_response_result(raw, scopes)
    if not result.readings:
        return "parser: no episode read from the answer"
    return f"ok ({len(result.readings)}/{len(scopes)} episodes read)"


def _period_verdict(raw: str, prompt: str) -> str:
    grounding = _period_grounding(prompt)
    identity = PeriodInsightIdentity.from_grounding(producer_key="reader-probe", episodes=grounding)
    reading = _read_response(raw, identity, grounding)
    if reading is None:
        return f"parser: {_response_problem(raw, grounding)}"
    return f"ok ({len(reading.evidence)} evidence rows)"


def _story_pick_verdict(raw: str, prompt: str) -> str:
    try:
        decoded = complete_final_json(raw)
        kept, unused, _why = _read_pick(
            decoded, labels=_story_pick_labels(prompt), count=STORY_PICK_SLOTS, allow_fewer=True
        )
    except ValueError as exc:
        return f"parser: {exc}"
    return f"ok ({len(kept)} kept, {unused} unused)"


SHAPES = (
    ("episodes", "episodes.txt", TEXT_EPISODE_MAX_OUTPUT_TOKENS, True, _episode_verdict),
    ("period", "period.txt", TEXT_PERIOD_MAX_OUTPUT_TOKENS, True, _period_verdict),
    ("story-pick", "story-pick.txt", STORY_PICK_MAX_TOKENS, False, _story_pick_verdict),
)


def _price(pricing: dict, reader: str, model: str, billed: LLMTransportAttempt | None) -> str:
    """This call at the manifest's list price, in the currency that shop publishes in."""
    table = (pricing.get(reader) or {}).get(model)
    if table is None or billed is None or billed.prompt_tokens is None:
        return "unpriced"
    spend = (billed.prompt_tokens * float(table["input_per_million"])) + (
        (billed.completion_tokens or 0) * float(table["output_per_million"])
    )
    return f"{pricing[reader]['currency']} {spend / 1_000_000:.5f}"


class _Billing:
    """The provider's own account of the reply, kept beside the stage's announcements."""

    def __init__(self, config) -> None:
        self.attempt: LLMTransportAttempt | None = None
        self.status: int | None = None
        self._watch = watch_provider("reader", config)

    def __call__(self, attempt: LLMTransportAttempt) -> None:
        if attempt.status_code is not None:
            self.status = attempt.status_code
        if attempt.finish_reason is not None:
            self.attempt = attempt
        self._watch(attempt)


def _ask(prompt: str, config, *, max_tokens: int, require_complete: bool, billing) -> str:
    return asyncio.run(
        query_llm(
            prompt,
            config,
            temperature=0.0,
            max_tokens=max_tokens,
            timeout_seconds=int(config.timeout_seconds),
            thinking=False,
            cache_path=None,
            transport_observer=billing,
            require_complete=require_complete,
        )
    )


def probe_shape(shape, config, *, reader: str, pricing: dict) -> ShapeResult:
    name, filename, max_tokens, require_complete, verdict_of = shape
    prompt = (PROMPTS / filename).read_text()
    billing = _Billing(config)
    started = time.monotonic()
    failure = ""
    raw = ""
    try:
        raw = _ask(
            prompt,
            config,
            max_tokens=max_tokens,
            require_complete=require_complete,
            billing=billing,
        )
    except Exception as exc:  # The probe reports every refusal rather than raising one.
        failure = f"transport: {type(exc).__name__}: {str(exc).strip()[:200]}"
    seconds = time.monotonic() - started
    billed = billing.attempt
    return ShapeResult(
        shape=name,
        model=config.model,
        status=str(billing.status or "-"),
        finish_reason=(billed.finish_reason if billed else "") or "-",
        content_chars=len(raw),
        completion_tokens=(billed.completion_tokens if billed else 0) or 0,
        reasoning_tokens=(billed.reasoning_tokens if billed else 0) or 0,
        prompt_tokens=(billed.prompt_tokens if billed else 0) or 0,
        seconds=seconds,
        cost=_price(pricing, reader, config.model, billed),
        verdict=failure or verdict_of(raw, prompt),
    )


@contextlib.contextmanager
def _cell_credentials(item: CellPlan, plan: Plan):
    """The env the runner hands the cell's child process, for as long as its config is read.

    `${NAME}` in a pinned config is expanded out of the environment at load
    time, so the key has to be there and nowhere else: it is put back exactly as
    it was on the way out, and never printed.
    """
    passthrough = {
        name: plan.environment[name] for name in item.app_credentials if name in plan.environment
    }
    previous = {name: os.environ.get(name) for name in passthrough}
    os.environ.update(passthrough)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def cell_llm_config(item: CellPlan, plan: Plan, config_source: Path | None):
    """The reader settings this cell will actually use, built the way the runner builds them."""
    from setup_matrix import _write_cell_config  # Imported here: the runner imports this module.

    with tempfile.TemporaryDirectory(prefix="reader-probe-") as directory:
        out = Path(directory)
        _write_cell_config(item, plan, out, config_source)
        with _cell_credentials(item, plan):
            return resolved_llm_config(Config.from_yaml(out / item.cell.id / "config.yaml").llm)


def probe_cell(item: CellPlan, plan: Plan, config_source: Path | None, pricing: dict) -> list:
    """Every shape against one cell's own llm settings, built the way the runner builds them."""
    llm = cell_llm_config(item, plan, config_source)
    print(f"{item.cell.id}: {llm.model} at {llm.base_url}", flush=True)
    print(HEADER, flush=True)
    results = [
        probe_shape(shape, llm, reader=item.cell.reader, pricing=pricing) for shape in SHAPES
    ]
    for result in results:
        print(result.row(), flush=True)
    return results


def probe_cells(plan: Plan, config_source: Path | None, pricing: dict) -> int:
    """0 when every shape of every probed cell came back readable, 1 otherwise."""
    probed = [item for item in plan.runnable if reads_with_a_model(item.cell)]
    if not probed:
        print("no reader cell in this request; nothing to probe")
        return 0
    failures = []
    for item in probed:
        failures.extend(
            result for result in probe_cell(item, plan, config_source, pricing) if result.failed
        )
    if failures:
        print(f"\n{len(failures)} reader shape(s) failed:", file=sys.stderr)
        for result in failures:
            print(f"  {result.model} {result.shape}: {result.verdict}", file=sys.stderr)
        return 1
    print("\nevery reader answered every shape")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cell", action="append", default=[], help="cell id, repeatable")
    parser.add_argument("--lane", action="append", default=[], choices=["mac", "nas", "k8s"])
    parser.add_argument("--library", default="demo")
    parser.add_argument("--month", default=None)
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--config", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from setup_matrix import DEFAULT_ENV_FILES, IMAGE_REPO, read_env_files

    opts = _parse_args(argv)
    environment = read_env_files(tuple(opts.env_file) or DEFAULT_ENV_FILES)
    manifest = load_manifest()
    with tempfile.TemporaryDirectory(prefix="reader-probe-plan-") as directory:
        try:
            plan = build_plan(
                manifest=manifest,
                library=opts.library,
                month=opts.month,
                lanes=tuple(opts.lane),
                cell_ids=tuple(opts.cell),
                out_dir=Path(directory),
                image=f"{IMAGE_REPO}:probe",
                environment=environment,
                fresh_cache=False,
            )
        except PlanError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        for item in plan.skipped:
            print(f"{item.cell.id}: skipped, {item.skip_reason}")
        return probe_cells(plan, opts.config, manifest.get("pricing") or {})


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(main())
