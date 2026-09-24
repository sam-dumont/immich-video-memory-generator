"""Probe a reader's text contracts, cost and latency before a full cell.

A reader can return HTTP 200 and no usable answer, or answer correctly at an
unacceptable cost. Send an episode read and a story pick using the production
contracts, then project the library's call envelope against its time and cost
ceilings. The reader is never sent a picture: pictures are read once, at ingest.

One call per shape, deliberately. The stages wrap `query_llm` in a retry that
doubles the budget or repairs the JSON; a probe wants the first answer, not the
recovered one. Stop on the first failed shape.

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
from dataclasses import dataclass, replace
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from setup_matrix_plan import (  # noqa: E402
    PROBE_READERS,
    CellPlan,
    Plan,
    PlanError,
    build_plan,
    load_manifest,
    reads_with_a_model,
)

from immich_memories.analysis.editorial_json_completion import complete_final_json  # noqa: E402
from immich_memories.analysis.editorial_story_pick_contract import _read_pick  # noqa: E402
from immich_memories.analysis.llm_providers import (  # noqa: E402
    reader_concurrency,
    resolved_llm_config,
)
from immich_memories.analysis.llm_query import query_llm  # noqa: E402
from immich_memories.analysis.llm_wire import LLMTransportAttempt  # noqa: E402
from immich_memories.analysis.provider_status import watch_provider  # noqa: E402
from immich_memories.analysis.text_episode_answers import (  # noqa: E402
    _EpisodeRequestScope,
    _read_response_result,
)
from immich_memories.analysis.text_episode_paging import (  # noqa: E402
    TEXT_EPISODE_MAX_OUTPUT_TOKENS,
)
from immich_memories.config_loader import Config  # noqa: E402
from immich_memories.store.episode_readings import EpisodeReadingIdentity  # noqa: E402

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
    usage_measured: bool = False

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


def _story_pick_labels(prompt: str) -> set[str]:
    return set(re.findall(r"^(M\d+) \|", prompt, flags=re.MULTILINE))


def _episode_verdict(raw: str, prompt: str) -> str:
    scopes = _episode_scopes(prompt)
    result = _read_response_result(raw, scopes)
    if not result.readings:
        return "parser: no episode read from the answer"
    return f"ok ({len(result.readings)}/{len(scopes)} episodes read)"


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
        usage_measured=(
            billed is not None
            and billed.prompt_tokens is not None
            and billed.completion_tokens is not None
        ),
    )


def check_budget(results, *, budget: dict, config, reader: str, pricing: dict) -> list[str]:
    """Project stage envelopes from measured calls; cost counts every concurrent call."""
    if not budget:
        return ["no reader budget declared for this library"]
    measured = {result.shape: result for result in results}
    seconds = spend = 0.0
    rate = (pricing.get(reader) or {}).get(config.model)
    currency = (pricing.get(reader) or {}).get("currency", "")
    local = reader == "local_model"
    priced = local or rate is not None
    for name, stage in budget["calls"].items():
        result = measured.get(name)
        if result is None:
            return [f"no measurement for projected stage {name}"]
        count = int(stage["count"])
        parallel = reader_concurrency(config) if stage.get("parallel") else 1
        elapsed = ceil(count / parallel) * result.seconds
        seconds += elapsed
        print(f"  project {name}: {count} calls, concurrency {parallel}, {elapsed:.0f}s")
        if rate and result.usage_measured:
            spend += (
                count
                * (
                    result.prompt_tokens * float(rate["input_per_million"])
                    + result.completion_tokens * float(rate["output_per_million"])
                )
                / 1_000_000
            )
        elif not local:
            priced = False
    ceiling = float(budget["max_seconds"])
    print(f"  projected reader time: {seconds:.0f}s / {ceiling:.0f}s ceiling")
    problems = []
    if seconds > ceiling:
        problems.append("projected time exceeds the library ceiling")
    if local:
        print("  projected token cost: local model, no token bill")
    elif not priced:
        problems.append("cannot project hosted cost: missing prices or token usage")
    elif currency not in budget["max_cost"]:
        problems.append(f"no cost ceiling declared in {currency}")
    else:
        cost_ceiling = float(budget["max_cost"][currency])
        print(f"  projected token cost: {currency} {spend:.5f} / {cost_ceiling:.5f} ceiling")
        if spend > cost_ceiling:
            problems.append("projected cost exceeds the library ceiling")
    return problems


def configure_reader_probes(plan, *, config_source, env_files, budget_overrides):
    """Give each probe the runner's actual config and explicit per-cell overrides."""
    unknown = set(budget_overrides) - {item.cell.id for item in plan.cells}
    if unknown:
        raise PlanError(f"budget override names unselected cell(s): {', '.join(sorted(unknown))}")
    common = ("--config", str(config_source.resolve())) if config_source else ()
    for path in env_files:
        common += ("--env-file", str(path.resolve()))
    cells = []
    for item in plan.cells:
        extra = common
        if item.cell.id in budget_overrides:
            extra += ("--allow-reader-budget-overrun", item.cell.id)
        steps = tuple(
            replace(step, command=(*step.command, *extra)) if step.name == PROBE_READERS else step
            for step in item.steps
        )
        cells.append(replace(item, steps=steps))
    return replace(plan, cells=tuple(cells))


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
    results = []
    for shape in SHAPES:
        result = probe_shape(shape, llm, reader=item.cell.reader, pricing=pricing)
        results.append(result)
        print(result.row(), flush=True)
        if result.failed:
            break
    return results


def probe_cells(
    plan: Plan, config_source: Path | None, pricing: dict, *, budget: dict, budget_overrides=()
) -> int:
    """0 when every shape of every probed cell came back readable, 1 otherwise."""
    probed = [item for item in plan.runnable if reads_with_a_model(item.cell)]
    if not probed:
        print("no reader cell in this request; nothing to probe")
        return 0
    from setup_matrix import load_operator_credentials  # Imported here: the runner imports us.

    # Both entry points pass here: this module's `main` and the runner's
    # `--probe-readers-only`. The February hosted rows of 09-13/14 were empty
    # because neither had loaded the credentials the cell's config needs.
    load_operator_credentials(plan, probed, config_source)
    failures = []
    for item in probed:
        results = probe_cell(item, plan, config_source, pricing)
        broken = [result for result in results if result.failed]
        if broken:
            failures.extend(f"{item.cell.id} {result.shape}: {result.verdict}" for result in broken)
            continue
        problems = check_budget(
            results,
            budget=budget,
            config=cell_llm_config(item, plan, config_source),
            reader=item.cell.reader,
            pricing=pricing,
        )
        if problems and item.cell.id in budget_overrides:
            print(f"  {item.cell.id}: explicit budget override: {'; '.join(problems)}")
        else:
            failures.extend(f"{item.cell.id}: {problem}" for problem in problems)
    if failures:
        print(f"\n{len(failures)} reader refusal(s):", file=sys.stderr)
        for result in failures:
            print(f"  {result}", file=sys.stderr)
        return 1
    print("\nevery reader answered every shape and passed its budget gate")
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
    parser.add_argument(
        "--allow-reader-budget-overrun", action="append", default=[], metavar="CELL"
    )
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
        if set(opts.allow_reader_budget_overrun) - {item.cell.id for item in plan.cells}:
            print("budget override must name a selected cell", file=sys.stderr)
            return 2
        return probe_cells(
            plan,
            opts.config,
            manifest.get("pricing") or {},
            budget=manifest["libraries"][plan.library].get("reader_budget", {}),
            budget_overrides=opts.allow_reader_budget_overrun,
        )


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(main())
