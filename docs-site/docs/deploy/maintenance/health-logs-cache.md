---
sidebar_label: "Health, logs and caches"
---

# Health, logs and caches

## Health endpoints

| Endpoint | Returns | Use it for |
|---|---|---|
| `GET /health/live` | `200` while the web process answers, `{"status": "alive", "version": …}`. Never contacts Immich | liveness probe |
| `GET /health/ready` | `200` with `status: ready` when configuration and authenticated Immich access work; `503` with `status: degraded` otherwise | readiness probe, Uptime Kuma, blackbox exporter |
| `GET /health` | always `200`: a ready payload is rewritten to `ok`, a degraded one passes through as `degraded` with the same `200` | compatibility only, never a probe |

`GET /health` always returns HTTP `200` for compatibility and rewrites a ready payload to `ok`,
which is what makes it useless as a probe.

The two detailed endpoints reuse a snapshot for up to ten seconds to avoid repeating Immich
and database reads on every poll. Polling `/health` does not change the status returned by
`/health/ready`; both can be used at the same time.

All three are unauthenticated, on purpose and even with login turned on: a container runtime has no
session. They carry the version, whether config is present and whether Immich answered, and nothing
about your library. Put them behind your ingress rules if that is more than you want to publish.

An abridged readiness payload:

```json
{"status": "ready", "immich_reachable": true, "last_successful_run": "2025-12-15T10:30:00", "version": "0.77.2"}
```

The full one adds the automation, pending-delivery and scheduler blocks; `last_successful_run`
comes from the run database. The Immich probe is bounded at 5 seconds, and a degraded status does
not stop the app: the UI still serves.

## Logging

`INFO` by default. `immich-memories -v generate …` logs at `DEBUG`; `--log-level WARNING` keeps
warnings and errors. Both are root options, so they go before the subcommand and apply to `ui` as
well. In a container, `IMMICH_MEMORIES_LOG_LEVEL=DEBUG`. `generate --quiet` and `auto run --quiet`
are a different knob: they change what the terminal shows, not what is logged.

Lines look like `2025-12-15 10:30:00,123 [INFO] immich_memories.generate [abc123]: Assembling final
video...`; the bracketed run id ties every line of one run together (`-` outside a run).
`IMMICH_MEMORIES_LOG_FORMAT=json` switches to one JSON object per line with the same fields, so
`jq 'select(.run_id=="abc123")'` works. `IMMICH_MEMORIES_LOG_FILE=/path/to/file.log` writes the
same lines to a file as well as stdout; in Docker, point it at a mounted path.

## Selection usage records

Each selection attempt saves `llm-usage.json` beside its private status record under
`cache/editorial-runs/<memory>/attempts/<attempt>/`. It checkpoints measured model calls,
cache hits and token counts as progress changes, then saves the final selection totals on
success, failure or cancellation. This also works with `generate --no-render` and the web UI.
A process killed without cleanup leaves its last checkpoint; the interrupted call may be missing.
Writes replace the file atomically, so a failed write preserves the previous checkpoint.

The totals include caption controls, library captions and motion assessments, including invalid
answers and retries. `by_stage` separates `caption_controls`, `caption`, `motion` and `reader`;
`by_model` retains model names supplied by the server. The three caption control images count
too. Reusing prepared facts adds no preparation calls.

`unmetered_calls` counts recorded attempts without complete token usage. These leave
`usage_complete: false`; the token totals are the known subtotal. The terminal and saved-run
summary name this gap. A reported zero is distinct from missing usage. Reasoning tokens remain
a subset of output tokens, not an additional charge.

Provider batch lines count the same way. Every completed line is billed on the record once,
including one whose answer the stage could not read and then asked again in real time.
`batch_unmetered_calls` counts batch lines that came back without usage; while it is above zero,
`batch_usage_complete` is `false` and the batch token totals are a floor.

The setup matrix leaves cost unpriced when usage is incomplete or the total includes preparation:
local caption compute cannot be priced using the hosted reader's rate. Counts remain available
by stage and model for separate pricing. A successful rendered CLI run replaces the file with
the wider run total already collected by the CLI.

## Caches

Everything lives under `~/.immich-memories/cache/` (or `cache.directory`):

| Directory or file | What it holds | Cap |
|---|---|---|
| `annotations.sqlite` | every caption, head answer, detector verdict, measurement and reading the editor banked, keyed by producer and exact input | none; this is the file to keep |
| `thumbnails/` | one Immich preview per candidate a memory's scope can reach | `thumbnail_cache_max_size_mb`, 10 GB |
| `video-cache/` | downloaded Immich clips | `video_cache_max_size_gb` 10 GB, `video_cache_max_age_days` 7 |
| `preview-cache/`, `previews/` | clip previews for the web UI | `preview_cache_max_size_mb`, 2 GB |
| `../cache.db` (one level up) | run history, automation state, and the retired scorer's table | none |

```yaml
cache:
  directory: ~/.immich-memories/cache
  database: ~/.immich-memories/cache.db
  video_cache_enabled: true
  video_cache_max_size_gb: 10.0
  video_cache_max_age_days: 7
  thumbnail_cache_max_size_mb: 10000
  preview_cache_max_size_mb: 2000
```

`cache.max_age_days` is still accepted and nothing reads it.

### What a second cut asks again

Nothing in `annotations.sqlite` is keyed to a run. The period reading is one page per calendar month
and no page carries anything from the page before it, so the bank answers a month it has already
read: a year read twice asks no page again, and a monthly cut after a yearly one asks nothing again
for that month. Standing votes are banked per picture rather than per block, so a moved candidate
set still hits. A warm cut therefore asks the model nothing, and its remaining minutes are the video
work after the cut. Measured, selection only, on a 60-second February 2024: 3.7 minutes cold and
40 seconds warm. Deleting that file re-asks all of it.

### After an upgrade that re-reads the episodes

The episode readings are keyed by the prompt that produced them. When a release changes that prompt,
as the one that stopped a reading from taking a name off a banner did, every banked episode is asked
again on the first cut over its period, and so is everything read from those sentences: the month
pages of the period account and the story decisions above them. A second cut over the same period is
warm again. Captions, head answers and detector verdicts are keyed by their own producers and stay
warm through it, so the preparation is never repeated.

The cull's standing verdicts go with them. Which pictures are screens, documents, failed frames or
saved imagery is remembered per picture in `editorial_verdicts`, and that row is keyed by the same
reading, so a prompt change retires the old verdicts rather than adding them to the new reading's
answer. Releases up to 0.102.0 keyed those rows on a constant maintained by hand: when the episode
prompt moved without it, one catalogued day carried both readings' rejects at once, 94 of its 120
pictures were culled, and a seven-carrier film became three and would not render. Rows written
before the fix are never read again, so they cost disk and nothing else. To reclaim the space:

```bash
sqlite3 ~/.immich-memories/cache/annotations.sqlite \
  "DELETE FROM editorial_verdicts WHERE pass_version='pass-1-cull-v3'"
```

The next cut over those pictures asks the cull again.

### The facts a cut measures

Three facts can only be measured once a cut has chosen a picture: a Live Photo's motion residual,
where the speech sits in a clip, and how the clocks of a Live burst's companion videos line up. All
three are banked in `annotations.sqlite`, beside the captions and the motion sentences.

| Table | What it holds | Written when |
|---|---|---|
| `motion_residuals` | the optical flow measured on one Live Photo's companion video, the residual the 1.5 discriminant reads included | a cut measures a chosen Live carrier |
| `speech_regions` | the utterances a clip holds, in its own seconds. An empty list is an answer: the detector listened and heard none | a cut measures a retained video or a playing Live Photo |
| `live_clock_offsets` | how much later one companion's clock starts than the previous one's, per pair of companions in a burst. An empty answer is an answer: the two files share no content the stitch can trust, so the burst ships as its photograph | a cut keeps a burst of two or more Live Photos |

Each row is keyed by the picture, the exact source metadata it was measured from, and a producer
version carrying what produced it: `motion-residual-v1@median-flow-v1-12frames-320x240` for the
residual, `live-clock-offset-v1@motion-diff-v1-15fps-36px` for the offsets (keyed by both
companions of the pair), `speech-regions-v1@firered-aed-utterances-v1/` plus a digest of the
detector settings for the speech, which is where `speech.vad_threshold` and `speech.min_silence_ms` land. Change the
picture in Immich, the method, or those settings, and the old row stops being an answer: the next
cut measures again and writes the new one in its place.

The next cut reads them before it plans. A Live Photo whose banked residual is under 1.5 is planned
as a still from the start, rather than planned as motion and found out at the cut, and a clip whose
speech is banked carries its sentence boundaries into the shortlist and the shave. A picture with no
row is not measured, which is not the same answer as measured as nothing: the planner treats it
exactly as it did before and the cut measures it.

Nothing measures these ahead of time for a whole library. Only the pictures a cut reaches are
measured, and what one cut pays for, every later cut reads for free.

Releases up to 0.102.0 kept the residual in `structure-banks/demanded-motion.sqlite` and the speech
in `structure-banks/speech-facts/`, keyed so that only the resolver that wrote them could read them.
Neither is read any more, and both are safe to delete:

```bash
rm -f ~/.immich-memories/cache/structure-banks/demanded-motion.sqlite
rm -rf ~/.immich-memories/cache/structure-banks/speech-facts
```

The first cut over those pictures measures them again, into the bank the planner reads.

### The preview cache scales with your library

Generating a memory reads each candidate's preview several times (sharpness, the heads, the
contact sheets, the caption). One preview is about 315 KB, so size it as
`thumbnail_cache_max_size_mb ≈ 0.35 × pictures a memory's scope can reach`; the 10 GB default holds
about 31,000. Previews the current run uses are never evicted, so a run that does not fit overflows
the cap rather than losing facts. The price lands on the next overlapping run, which re-downloads
every preview and re-captions the pictures whose banked caption failure no longer matches the
bytes it was recorded against. One `WARNING` per run says how far over you are and names the
setting. The clip and video caches hold one cut's worth of files however big the library is, so
their caps are plain caps.

### Video cache mechanics

Files sit at `{id[:2]}/{id}{ext}`. A hit is `ffprobe`d first; an unreadable file is deleted and
fetched again. A download streams into `{id}{ext}.part` and is renamed into place only when
complete, so a run killed mid-download leaves nothing the next run would trust; `.part` files idle
for an hour are removed at the next start. Age eviction runs at the start of every run; size
eviction runs after each download (sparing files the run already handed out) and once more at the
end with nothing spared.

### Clearing

The UI's Cache page (sidebar, Cache) shows usage and has per-cache **Clear** buttons and **Clear
all**. From a shell, the video and thumbnail caches are plain directories, safe to delete while the
app is idle:

```bash
rm -rf ~/.immich-memories/cache/video-cache
rm -rf ~/.immich-memories/cache/thumbnails
```

Do not point `rm -rf` at `~/.immich-memories/cache` itself: `annotations.sqlite` is inside it, and
deleting it re-asks the model everything about your library.

### The CLI cache commands are not for the banks

`immich-memories cache stats|backup|export|import` read and write `asset_scores`, the retired
per-clip scorer's table, which nothing writes any more. They do not touch `annotations.sqlite`.
To move an installation, copy `~/.immich-memories` (Docker: the config volume).

`cache.db` has a versioned schema migrator that runs when it is first opened. `annotations.sqlite`
creates tables when missing and adds columns additively. Neither runs at process start.
