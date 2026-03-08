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

### Target Duration

The suggested duration scales with your time period:
- Full year: 10 minutes
- Half year: 6 minutes
- Quarter: 4 minutes
- Month: 2 minutes
- Less than a month: 1 minute

---

## Clip Review (Step 2)

After clicking "Next: Review Clips", you'll see all available videos.

### Summary Metrics

- **Selected Clips**: How many videos are selected
- **Total Duration**: Combined length of all video content
- **Target Duration**: Your goal for the final compilation

### Analysis Settings

| Setting | Description |
|---------|-------------|
| **Avg seconds per clip** | How much to use from each video (default: 5s) |
| **Clips needed** | Auto-calculated based on target duration |
| **HDR clips only** | Only use HDR videos (if available) |
| **Prioritize favorites** | Include favorite videos first |
| **Max non-favorites** | Limit non-favorite videos to this percentage (default: 25%) |
| **Analyze all videos** | Slower but more thorough analysis |

#### Understanding "Max Non-Favorites"

When you have a short time period with many videos, you don't want the compilation filled with random clips. The "Max non-favorites" slider limits how many non-favorite videos can be included.

For example, with 25% max:
- If you select 20 clips total
- At most 5 will be non-favorites
- At least 15 will be favorites (if available)

### The Analysis Pipeline

When you click "Analyze", the system runs 4 phases:

1. **Clustering**: Groups similar videos together (avoids duplicates)
2. **Filtering**: Applies your preferences (HDR, favorites, etc.)
3. **Analyzing**: Downloads and scores each video
4. **Refining**: Picks final clips and optimal segments

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

### Music

Three options for background music:

- **None**: No background music.
- **Upload file**: Upload your own MP3, M4A, or WAV file. Volume slider controls how loud the music plays relative to original clip audio.
- **AI Generated (MusicGen)**: Generates a soundtrack based on the mood of your clips. Requires a MusicGen server configured in your settings. You can generate 1-3 versions and pick the best one.

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
4. **Enable LLM analysis**: Better segment selection (requires Ollama or OpenAI)

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
- Check Ollama is running: `curl http://localhost:11434/api/tags`
- Pull a vision model: `ollama pull moondream`
- Check logs for specific errors

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
