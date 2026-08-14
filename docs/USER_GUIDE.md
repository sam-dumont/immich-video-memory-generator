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
- **Test Connection**: Verifies your credentials work
- **Save Config**: Saves settings for future sessions
- **Preflight Check**: Tests all dependencies (FFmpeg, etc.)

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

### Time Period Selection

Choose how to select which videos to include:

#### Year Mode
- Select a calendar year (January 1 - December 31)
- Or select "Birthday Year" to use a person's birthday as the start

#### Duration Mode
- Set a duration (e.g., 1 month, 6 months)
- Choose a start date
- Good for seasonal compilations

#### Custom Range Mode
- Pick exact start and end dates
- Maximum flexibility

### Person Filter

- **All People**: Include videos with anyone
- **Specific Person**: Only include videos featuring a recognized person
- Uses Immich's face recognition

### Cache Management

An expandable "Cache Management" panel at the bottom of Step 1 shows disk usage for each cache type (analysis database, video files, thumbnails, preview clips). You can clear individual caches or all of them at once. Useful when you want a fresh start or need to reclaim disk space.

### Target Duration

For trip memories, **Auto** is the default after discovery. It starts at 30 seconds plus 10 seconds
per active day, bounded to 60–300 seconds when there is enough media. It then checks usable
excerpts—not raw source lengths—and shortens sparse trips rather than producing filler. A dense
seven-day trip is about 1:40; a dense 12-day trip is 2:30. Switch to **Manual** for an exact target.

Other memory types use preset durations that scale with their time period:
- Full year: 10 minutes
- Half year: 6 minutes
- Quarter: 4 minutes
- Month: 2 minutes
- Less than a month: 1 minute

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
- **Target Duration**: Your goal for the final compilation

### Analysis Settings

| Setting | Description |
|---------|-------------|
| **Avg seconds per clip** | How much to use from each video (default: 5s) |
| **Clips needed** | Auto-calculated based on target duration |
| **HDR clips only** | Only use HDR videos (if available) |
| **Prioritize favorites** | Include favorite videos first |
| **Preferred max non-favorites** | Favor this percentage; Auto may exceed it to fill the video |
| **Analyze all videos** | Slower but more thorough analysis |

#### Understanding "Max Non-Favorites"

When you have a short time period with many videos, you don't want the compilation filled with random clips. The non-favorite slider sets a preference, not a destructive cap.

For example, with 25% max:
- If you select 20 clips total
- The selector tries to keep non-favorites near 5
- If the favorites cannot fill the timeline, eligible non-favorites are added instead of leaving it short

### The Analysis Pipeline

When you click "Analyze", the system runs 4 phases:

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

### Review Mode

After analysis completes, you can review and refine the selected clips:

- Toggle inclusion with checkboxes
- Adjust start/end times with range sliders
- Preview each clip inline
- View LLM analysis results (if enabled)

Bulk actions: Select All, Deselect All, Invert Selection.

---

## Generation Options (Step 3)

After reviewing your clips, this step configures how the final video gets assembled.

### Output Settings

| Setting | Options | Default |
|---------|---------|---------|
| **Orientation** | Auto (detect from clips), Landscape (16:9), Portrait (9:16), Square (1:1) | Auto |
| **Scaling Mode** | Smart Crop (keeps faces centered), Fill (crops to fit), Fit (letterbox) | Smart Crop |
| **Transition Style** | Smart (mix of fades and cuts), Crossfade, Cut, None | Smart |
| **Resolution** | Auto (match clips), 4K, 1080p, 720p | Auto |
| **Output Format** | MP4 (H.264), MOV (ProRes) | MP4 |
| **Date overlay** | Checkbox to burn date text into the video | Off |
| **Keep intermediate files** | Saves temporary files for debugging | Off |

The table above describes the UI. For CLI and scheduled runs, omitting `--resolution` uses
`output.resolution` from your config; pass `--resolution auto` explicitly to match the source
clips. Output quality comes from the configured CRF (or the `quality` shorthand when CRF is not
set) for software H.264/H.265 and Apple VideoToolbox. Other hardware backends retain their
existing quality policies. On Apple, `encoder_preset` controls speed/effort, not image quality.

### Music

Three options for background music:

- **None**: No background music.
- **Upload file**: Upload your own MP3, M4A, or WAV file. Volume slider controls how loud the music plays relative to original clip audio.
- **AI Generated**: Generates a soundtrack based on the mood of your clips. ACE-Step supports direct local generation on Apple Silicon/CUDA and a hosted REST server. In local mode it automatically uses the configured ACE-Step server if the local package is unavailable. MusicGen is an alternative generator when ACE-Step is disabled, and can also provide remote Demucs stem separation. You can generate 1-3 versions and pick the best one.

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

The filename defaults to `{person}_{daterange}_memories.mp4` and saves to `~/Videos/Memories/`. You can change the filename before generating.

### Generating

Click **Generate Video** to start the pipeline. Three phases run in sequence:

1. **Downloading and extracting segments** (0-70%): Downloads each clip from Immich, extracts the selected time range
2. **Assembling** (70-85%): Combines all segments with transitions, applies resolution and orientation settings
3. **Music** (85-100%): If music is enabled, generates or mixes in the background track with automatic audio ducking

A progress bar and status label update in real time.

### After Generation

The finished video plays directly in the browser. The file path is shown below the player.

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
- Disable "Analyze all videos" for faster processing
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

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `J` | Rewind |
| `K` | Pause |
| `L` | Forward |
| `I` | Set in-point |
| `O` | Set out-point |
| `Space` | Play/Pause |
| `←` / `→` | Frame step |

---

## Getting Help

- **GitHub Issues**: Report bugs or request features
- **GitHub Discussions**: Ask questions
- **README**: Full technical documentation
