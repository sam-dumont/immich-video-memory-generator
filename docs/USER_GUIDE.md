# User Guide

This guide walks you through using Immich Memories to create video compilations from your Immich photo library.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Configuration (Step 1)](#configuration-step-1)
3. [Clip Review (Step 2)](#clip-review-step-2)
4. [Generation Options (Step 3)](#generation-options-step-3)
5. [Preview & Export (Step 4)](#preview--export-step-4)
6. [Tips and Best Practices](#tips-and-best-practices)
7. [Troubleshooting](#troubleshooting)

---

## Getting Started

### Launch the UI

```bash
immich-memories ui
```

This opens a web interface at `http://localhost:8080`.

Authentication is disabled by default. If the UI binds beyond loopback, anyone who can reach the
port can use it. Enable authentication before exposing it. The UI is single-user,
single-replica; run one instance.

### First-Time Setup

1. Enter your Immich server URL (e.g., `https://photos.example.com`)
2. Enter your Immich API key (get it from Immich → Account Settings → API Keys)
3. Click "Test Connection" to verify

---

## Configuration (Step 1)

### Immich Connection

- **Server URL**: Your Immich server address
- **API Key**: Your personal API key from Immich
- **Test Connection**: Verifies your credentials work (runs automatically on load when credentials are pre-filled)
- **Save Config**: Saves settings for future sessions

Once connected, the panel collapses to "Immich Connection — *your name*". To check FFmpeg, hardware
acceleration and the LLM/music servers, run `immich-memories preflight` from a terminal.

Immich Memories supports **Immich v2 and v3**. Leave API selection on its default:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

`auto` detects the server major version at runtime and selects the right API contract. You do not
need to choose a version for each run. Explicit `v2` and `v3` are manual troubleshooting
overrides—escape hatches for unusual proxies or deployments where version detection is wrong;
each one forces that contract.

The app normalizes v2 duration strings and v3 millisecond durations to seconds, selects the
correct upload fields before sending a generated video, and sends timezone-aware asset search
dates accepted by both majors.

To test detection and authentication without generating or uploading anything, run the read-only
check:

```bash
immich-memories config test
```

### Memory Type

Eight cards: **Year in Review**, **Season**, **Person Spotlight**, **Multi-Person**, **Monthly
Highlights**, **On This Day**, **Trip**, **Custom**. Each preset sets the date range and target
duration and shows only the parameters it needs (Year; Season + Hemisphere; Person with a
"Birthday to birthday" checkbox; People (select 2+); Month; a "Select a trip" list detected from
GPS data). See the [Web UI docs](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/step1-configuration) for the full table.

### Custom date range

Choosing **Custom** shows three tabs:

#### Year
- Select a calendar year (January 1 - December 31)
- Or click **From Birthday** (instead of **Calendar Year**) to run birthday to birthday

#### Duration
- Set a duration (1-24 months or years)
- Choose a **Starting from** date
- Good for seasonal compilations

#### Custom Range
- Pick exact **Start date** and **End date**

### Person Filter (Custom only)

- **All people**: Include videos with anyone
- A named person: Only include videos featuring that recognized person (Immich face recognition)
- Next to it, **Target Duration (minutes)** — auto-filled at about 10 minutes per year of range

### Options

Four controls: **Prioritize Favorites**, **Include Photos**, **Include Live Photos** (switches) and
**Analysis Depth** (Auto (recommended) / Fast (favorites first) / Thorough (every eligible clip)).

### Config and Cache pages

The sidebar has two extra entries below the steps:

- **Config** (`/settings/config`): read-only view of the active configuration, section by section,
  with API keys redacted, and a **Reload from Disk** button.
- **Cache** (`/settings/cache`): disk usage for each cache (analysis database, video files,
  thumbnails, preview clips) with a **Clear** button per cache and **Clear all caches**.

### Target Duration

For trip memories, **Auto** is the default after discovery. It starts at 30 seconds plus 10 seconds
per active day, bounded to 60–300 seconds when there is enough media. It then checks usable
excerpts—not raw source lengths—and shortens sparse trips rather than producing filler. A dense
seven-day trip is about 1:40; a dense 12-day trip is 2:30. Turn off the **Auto duration** switch in
Step 2 to type an exact **Target duration (min)**.

Other memory types use preset defaults:
- Year in Review: 10 minutes
- Multi-Person: 5 minutes
- Season: 2 minutes 15 seconds
- Person Spotlight: 2 minutes
- Monthly Highlights: 1 minute
- On This Day: 45 seconds
- Custom: about 10 minutes per year of range, pro-rated (a half year is 5, a month is 1)

The target is the finished video's duration, including the opening, dividers, ending, and
transition overlap. The selector reserves those seconds before choosing media. If normal quality
and photo-balance filters leave the timeline short, it backfills from unused eligible clips and
may relax the photo ratio before giving up. It does not relax hard safety, date, person, or
duplicate constraints. Final duration can vary by less than one transition because video is cut on
frame boundaries.

---

## Clip Review (Step 2)

After clicking "Next: Review Clips", you'll see all available videos.

### Summary Metrics

- **Selected Clips**: How many reviewed media items are checked
- **Total Duration**: Combined length of all video content

(**Target** and **Difference** appear later, in Review & Refine mode.)

### Analysis Settings

| Setting | Description |
|---------|-------------|
| **Auto duration** | Switch (on by default). Shows "Auto · Xm YYs"; off shows a **Target duration (min)** field |
| **Avg seconds per clip** | How much to use from each video (default: 5s) |
| **Clips needed** | Auto-calculated based on target duration |
| **HDR clips only** | Only use HDR videos (if available) |
| **Prioritize favorites** | Include favorite videos first |
| **Preferred max non-favorites** | Favor this percentage; Auto may exceed it to fill the video |

Analysis depth is chosen once in Step 1:

- **Auto (recommended):** LLM-analyzes every eligible clip when at most 60 clips need fresh
  analysis. Larger libraries use a time-balanced shortlist. Current-model cache hits do not count
  toward that limit.
- **Fast:** Uses the time-balanced shortlist and reserves LLM analysis for favorites.
- **Thorough:** LLM-analyzes every eligible clip, unconditionally.

Current cache results appear directly on clip cards. Cache entries from an unknown or different
model are stale: the app ignores them and starts fresh analysis.

#### Understanding "Max Non-Favorites"

When you have a short time period with many videos, you don't want the compilation filled with random clips. The non-favorite slider sets a preference, not a destructive cap.

For example, with 25% max:
- If you select 20 clips total
- The selector tries to keep non-favorites near 5
- If the favorites cannot fill the timeline, eligible non-favorites are added instead of leaving it short

### The Analysis Pipeline

When you click **Generate Memories**, the system runs 4 phases:

1. **Clustering**: Groups similar videos together (avoids duplicates)
2. **Filtering**: Applies hard eligibility and builds a cost-bounded deep-analysis shortlist
3. **Analyzing**: Deeply scores the shortlist; checked leftovers retain cached/metadata fallback segments
4. **Refining**: Picks final clips and optimal segments

Photos follow the same rule: every checked photo gets a metadata score, a distributed shortlist
gets VLM scoring, and the enhanced results are merged back into the full pool. The final selector
prefers two photos per day, balanced photo and non-favorite ratios, and temporal spacing. If those
preferences leave a duration hole, it relaxes them in stages while keeping hard exclusions intact.

### During Analysis

You'll see:
- Currently processing video (thumbnail)
- Last completed video (preview clip)
- LLM analysis results (if enabled)
- Progress bar with time estimate

### Selection toolbar (before analysis)

Above the media list: **Select All**, **Deselect All**, **Invert Selection**, and a grid/list
view toggle. Duplicate copies are detected and the lower-quality one is auto-deselected.

### Review & Refine (after analysis)

Click **Review & Refine Selected Clips** to adjust what the pipeline chose:

- Metrics: Selected Clips, Total Duration, Target, Difference
- Bulk actions: **Set all to first 5s**, **Set all to middle 5s**, or **Custom seconds** + **Apply**
- Per clip: **Include in compilation** checkbox, **Select range** slider, **Preview** / **First 5s** /
  **Last 5s** / **Middle 5s** / **Full clip** buttons, **Rotation** (Auto, 0°, 90° CW, 180°, 90° CCW)
- Navigation: **Back to Selection**, **Re-run Analysis**, **Continue to Generation**

---

## Generation Options (Step 3)

After reviewing your clips, this step configures how the final video gets assembled.

### Output Settings

Resolution and Output Format are always visible; the rest sit under a collapsed **Advanced
options** expansion.

| Setting | Options | Default |
|---------|---------|---------|
| **Resolution** | Auto (match clips), 4K, 1080p, 720p | Auto |
| **Output Format** | MP4 (H.264), MP4 (H.265), MOV (H.264), MOV (H.265), MOV (ProRes) | follows `output.codec`/`output.format` (MP4 (H.264) out of the box) |
| **Orientation** | Auto (detect from clips), Landscape (16:9), Portrait (9:16), Square (1:1) | Auto |
| **Scaling Mode** | Smart Crop (keep faces), Fill (crop), Fit (letterbox), Blur (blurred background) | Smart Crop in the UI (`blur` in config/CLI) |
| **Transition Style** | Smart (mix of fades and cuts), Crossfade, Cut, None | Smart |
| **Date overlay** | Checkbox to burn date text into the video | Off |
| **Keep intermediate files** | Saves temporary files for debugging | Off |
| **Photo duration (seconds)** | 1-10, shown when photos are enabled | 4 |

The table above describes the UI. For CLI and scheduled runs, omitting `--resolution` uses
`output.resolution` from your config; pass `--resolution auto` explicitly to match the source
clips. Output quality comes from the configured CRF (or the `quality` shorthand when CRF is not
set) for software H.264/H.265 and Apple VideoToolbox. Other hardware backends retain their
existing quality policies. On Apple, `encoder_preset` controls speed/effort, not image quality.

### Title

**Title** and **Subtitle** fields (pre-filled with the pipeline's suggestion — LLM-generated when
content analysis is on, template otherwise), a **Language** select and a **Regenerate** button.

### Music

The **Background music** select offers:

- **None**: No background music.
- **Upload file**: Upload your own MP3, M4A, or WAV file. Volume slider controls how loud the music plays relative to original clip audio.
- **AI Generated**: Only listed when `ace_step.enabled` or `musicgen.enabled` is set (then it is the default). Generates a soundtrack based on the mood of your clips. ACE-Step supports direct local generation on Apple Silicon/CUDA and a hosted REST server. In local mode it automatically uses the configured ACE-Step server if the local package is unavailable. MusicGen is an alternative generator when ACE-Step is disabled, and can also provide remote Demucs stem separation. Click **Generate Music** to preview one track before rendering; **Regenerate Music** tries again.

For ACE-Step v0.1.8 on a machine with at least 20GB of GPU/unified memory, use
`model_variant: "acestep-v15-xl-turbo"` with `lm_model_size: "4B"`. These select two different
components: the XL 4B audio model and the 4B planning model. In hosted `api` mode, select both on
the ACE-Step server; the app cannot replace an already-loaded server model. Avoid unattended
`xl-sft`/`xl-base` use on v0.1.8 until DCW is explicitly disabled for those non-turbo models or the
upstream model-aware API fix is released.

Both upload and AI options include a volume slider (0-100%).

### Summary

Shows a quick overview before you proceed: clip count, total duration, selected resolution, and music source.

---

## Preview & Export (Step 4)

This is where the video gets built.

### Output

The filename defaults to `{person}_{daterange}_memories.mp4` and saves under `output.directory`
(default `~/Videos/Memories/`) in a per-run folder named `{filename}_{run-id}/`. You can change the
filename before generating.

The same card has an **Upload after generation** switch and an **Album name** field: when on, the
finished video is uploaded to Immich and added to that album (created if missing).

### Generating

Click **Generate Video** to start the pipeline. The status line follows the run: downloading clips
and extracting the selected segments, rendering photos (if any), assembling with transitions and
title screens, then "Applying music" or "Music disabled" (background music is mixed with automatic
ducking), "Uploading to Immich" or "Delivery not requested", and "Complete".

A progress bar, a live frame preview and a **Cancel** button (stops after the current phase) are
shown while it runs.

### After Generation

"Your memory video is ready!" — the finished video plays directly in the browser, with **Saved to:**
(path and size) and an **Immich delivery:** status line (Delivered / Pending / Not Requested). There
is no download button; use the path shown.

From here you can:
- **Back to Generation Options**: Change settings and re-generate
- **Start New Project**: Reset everything and start fresh

---

## Tips and Best Practices

### For Best Results

1. **Mark favorites in Immich**: The algorithm prioritizes favorites
2. **Use face recognition**: Create compilations for specific people
3. **Start with a year**: Full years have more content to choose from
4. **Enable LLM analysis**: Better segment selection (any OpenAI-compatible server: mlx-vlm, Ollama, vLLM, Groq, etc.)

### Performance Tips

1. **Start small**: Try a 1-month compilation first
2. **Use video cache**: Analysis results are cached for speed
3. **Hardware acceleration**: Use GPU encoding if available
4. **Close other apps**: Video processing needs RAM

### Quality Tips

1. **Review favorites**: Ensure your best videos are marked as favorites
2. **Check segments**: The auto-selected segments may not be perfect
3. **Adjust target duration**: Longer compilations may include less interesting clips

---

## Troubleshooting

### Connection Issues

**"Connection failed"**
- Verify your Immich URL is correct (include `https://`)
- Check your API key is valid
- Ensure Immich server is running
- Run the read-only `immich-memories config test` check. It reports the resolved `v2` or `v3`
  API contract on success.

### No Videos Found

**"0 clips found"**
- Check the date range includes videos
- If filtering by person, ensure they have recognized videos
- Try "All people" to see if videos exist

### Slow Analysis

**Analysis taking too long**
- Choose **Fast** analysis depth for the smallest LLM workload
- Video analysis is cached - subsequent runs are faster
- Check if hardware acceleration is enabled (`immich-memories hardware`)

### Memory Issues

**Out of memory errors**
- Close other applications
- Process fewer videos at once
- Use the CLI for large batches

### LLM Not Working

**LLM analysis shows default values**

The LLM config lives in a shared `llm:` section of `config.yaml`. Two providers are supported:

- **`openai-compatible`** (default): works with mlx-vlm, vLLM, Groq, OpenAI, LM Studio, llama.cpp, or any server that speaks `/v1/chat/completions`. Check your server is reachable: `curl http://localhost:8080/v1/models`
- **`ollama`**: uses Ollama's native `/api/generate` endpoint. Check it's running: `curl http://localhost:11434/api/tags`

Run `immich-memories preflight` to test the connection. Check logs for specific errors.

### Target Exceeds Content

**"Target exceeds available content"**
- Reduce target duration
- Expand date range
- Include more people

---

## Getting Help

- **GitHub Issues**: Report bugs or request features
- **GitHub Discussions**: Ask questions
- **README**: Full technical documentation
