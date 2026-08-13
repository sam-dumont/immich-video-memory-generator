# All-or-None Month Dividers Design

**Date:** 2026-08-13

**Status:** Proposed for implementation

**Scope:** Chronological month dividers and final-duration planning. Trip cards, year dividers,
music configuration, and ACE-Step versions are out of scope.

## Problem

The 60-second Emile person spotlight selected content from six months but rendered only two month
dividers. The result is internally inconsistent: April and May are announced while later months
are not.

This happens because the current planner runs before final selection. It reserves 3.5 seconds for
the opening and 4 seconds for the ending inside a strict 12-second title allowance, leaving room
for only two 2-second dividers. The renderer then obeys that partial cap. A second filter excludes
months represented by only one selected clip.

The final artifact was 51.77 seconds because crossfades and content-backed title transitions
overlap media. Treating the 60-second request as a hard title-allocation ceiling therefore produced
a shorter, less coherent video even though there was enough practical runtime for every selected
month.

## Product Rule

For chronological month-divider flows:

1. The opening title covers the first selected month.
2. Every subsequent selected month gets a divider, including a month represented by one clip.
3. The complete set is used or no month dividers are used. Partial month coverage is forbidden.
4. Selected content is preserved. Dividers do not remove clips after selection.
5. Requested duration is a target. A coherent result may exceed it within a bounded soft envelope.

Trip location cards and year dividers retain their existing policies. The Somme trip output is the
reference for unchanged trip behavior.

## Duration Policy

Before selection, reserve only the opening and ending durations. This gives content the remaining
base target instead of reserving space for an arbitrary number of dividers.

After selection, calculate:

```text
base titles = opening + ending
all divider time = number of subsequent selected months * configured divider duration
raw estimate = selected content duration + base titles + all divider time
soft overrun = min(10 seconds, requested duration * 20%)
soft maximum = requested duration + soft overrun
```

If the raw estimate is at or below the soft maximum, include every eligible month divider. If it
is above the soft maximum, include none. Transition overlap is deliberately not deducted from the
estimate; that keeps the decision conservative and avoids coupling planning to a particular
transition implementation.

For a 60-second request the maximum is 70 seconds. Emile's selected months should fit with all
dividers. Actual output will normally be shorter than the raw estimate because transitions overlap.

Short requests scale proportionally: a 15-second request may use at most 3 seconds of overrun, not
the full 10 seconds.

## Architecture and Data Flow

### Stage 1: selection budget

`plan_timeline()` remains the pre-selection planner. For chronological month mode it budgets the
opening and ending only and sets `max_dividers` to zero. Smart selection consumes the resulting
content budget.

Trip and year modes continue using their current pre-selection divider budgeting.

### Stage 2: selected-timeline finalization

After `SmartPipeline.run_selection()` returns, a pure finalization function receives:

- the preliminary timeline plan;
- the selected clips in chronological order;
- their selected segment durations;
- title settings and memory type.

For chronological month mode it counts every distinct selected month after the first, ignoring the
old per-month clip-count threshold. It applies the soft-envelope rule and returns a final immutable
`TimelinePlan` with `max_dividers` set to either the complete count or zero. Opening, ending,
divider duration, and selection content budget remain explicit.

The finalized plan is the only plan passed to:

- dry-run preview;
- title generation and insertion;
- final generation parameters;
- duration/status logging.

No renderer independently chooses or truncates month dividers.

## Observable Output

Dry-run output reports one of:

```text
Month dividers: all 5 selected month changes
```

or:

```text
Month dividers: none (complete set would exceed 70.0s soft maximum)
```

The existing title-card count and estimated final duration use the finalized plan. Logs must not
say "2 divider max" for an all-or-none month flow.

## Music

This change does not alter music resolution or ACE-Step configuration.

- Unit, integration, and real-library smoke tests use `--no-music` for speed and determinism.
- Normal runs without `--no-music` keep the configured automatic music behavior.
- ACE-Step version review or upgrades are a separate task.

## Error Handling and Compatibility

- Missing or invalid clip dates do not create divider entries.
- A one-month selection needs no divider and remains valid.
- If selected content plus opening and ending already exceeds the soft maximum, month dividers are
  omitted; selected content is not trimmed a second time.
- Explicitly disabled title screens or month dividers remain disabled.
- Trip, year, monthly-single-month, upload, notification, and music behavior remain unchanged.

## Test Strategy

Implementation follows vertical red-green TDD.

1. A public timeline test proves six selected months produce five dividers after finalization and
   no partial cap.
2. A boundary test proves a complete divider set above the soft maximum produces zero dividers.
3. A test proves a month with one selected clip is included.
4. A test proves the first month is covered by the opening and is not duplicated.
5. Trip and year regression tests prove their current policies are unchanged.
6. CLI dry-run integration proves the displayed divider policy and estimated duration come from
   the finalized plan.
7. A real Emile smoke render uses `--no-music`; ffprobe checks 1280x720, H.264/AAC, and duration at
   or below the 70-second soft maximum. Frame inspection verifies every subsequent selected month
   divider appears and photo aspect-fit treatment remains stable.

## Acceptance Criteria

- Emile shows the opening plus every subsequent selected month, or no month dividers at all.
- No output can contain an arbitrary prefix such as April and May while omitting June and July.
- For a 60-second request, the divider decision never intentionally raises the raw estimate above
  70 seconds.
- Selected content is not removed during post-selection divider finalization.
- Dry-run describes the actual all-or-none decision.
- Smoke and automated tests generate no music; production auto-music behavior is unchanged.
- Somme trip behavior remains unchanged.
