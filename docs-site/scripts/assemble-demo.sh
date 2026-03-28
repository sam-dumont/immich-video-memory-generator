#!/usr/bin/env bash
# Assemble raw Playwright recordings into a polished demo video.
#
# Usage: bash scripts/assemble-demo.sh
# Expects raw section recordings in static/demo/raw/
# Produces final video at static/demo/demo.mp4

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEMO_DIR="$SCRIPT_DIR/../static/demo"
RAW_DIR="$DEMO_DIR/raw"
WORK_DIR="$DEMO_DIR/work"
OUTPUT="$DEMO_DIR/demo.mp4"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[assemble]${NC} $*"; }
ok()  { echo -e "${GREEN}[assemble]${NC} $*"; }

# ---------------------------------------------------------------------------
# Verify inputs
# ---------------------------------------------------------------------------

# Segments from Playwright recording (make demo-record)
SEGMENTS=(segment1-config segment2-navigate segment3-grid segment4-options segment5-progress)

for s in "${SEGMENTS[@]}"; do
    if [[ ! -f "$RAW_DIR/$s.webm" ]]; then
        echo "ERROR: Missing $RAW_DIR/$s.webm — run 'make demo-record' first"
        exit 1
    fi
done

mkdir -p "$WORK_DIR"

# ---------------------------------------------------------------------------
# Stage 1: Speed-ramp each section
# ---------------------------------------------------------------------------
log "Stage 1: Speed-ramping sections..."

# Segment 1 — Config: 3x speed (boring setup)
ffmpeg -y -i "$RAW_DIR/segment1-config.webm" \
    -filter:v "setpts=0.33*PTS" -an \
    -c:v libx264 -crf 20 -preset fast \
    "$WORK_DIR/s1_fast.mp4" 2>/dev/null
ok "  segment1: 3x speed"

# Segment 2 — Navigate: 3x speed (loading transition)
ffmpeg -y -i "$RAW_DIR/segment2-navigate.webm" \
    -filter:v "setpts=0.33*PTS" -an \
    -c:v libx264 -crf 20 -preset fast \
    "$WORK_DIR/s2_fast.mp4" 2>/dev/null
ok "  segment2: 3x speed"

# Segment 3 — Clip grid: 1x speed (highlight: scrolling thumbnails)
ffmpeg -y -i "$RAW_DIR/segment3-grid.webm" \
    -an -c:v libx264 -crf 20 -preset fast \
    "$WORK_DIR/s3_fast.mp4" 2>/dev/null
ok "  segment3: 1x speed (highlight)"

# Segment 4 — Options: 2x speed (settings overview)
ffmpeg -y -i "$RAW_DIR/segment4-options.webm" \
    -filter:v "setpts=0.5*PTS" -an \
    -c:v libx264 -crf 20 -preset fast \
    "$WORK_DIR/s4_fast.mp4" 2>/dev/null
ok "  segment4: 2x speed"

# Segment 5 — Progress: 1.5x speed (highlight: progress bar)
ffmpeg -y -i "$RAW_DIR/segment5-progress.webm" \
    -filter:v "setpts=0.67*PTS" -an \
    -c:v libx264 -crf 20 -preset fast \
    "$WORK_DIR/s5_fast.mp4" 2>/dev/null
ok "  segment5: 1.5x speed (highlight)"

# ---------------------------------------------------------------------------
# Stage 2: Add section title overlays (lower-third labels)
# ---------------------------------------------------------------------------
log "Stage 2: Adding section title overlays..."

TITLES=(
    "Step 1 · Configuration"
    "Step 1 · Loading Clips"
    "Step 2 · Review Clips"
    "Step 3 · Generation Options"
    "Step 4 · Generating"
)

# Use a system font that FFmpeg can find — fontfile path varies by OS
if [[ "$(uname)" == "Darwin" ]]; then
    FONT="/System/Library/Fonts/Helvetica.ttc"
else
    FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
fi

for i in 1 2 3 4 5; do
    title="${TITLES[$((i-1))]}"
    ffmpeg -y -i "$WORK_DIR/s${i}_fast.mp4" \
        -vf "drawtext=text='${title}':fontfile=${FONT}:fontsize=42:fontcolor=white:borderw=2:bordercolor=black@0.6:x=(w-tw)/2:y=h-80:enable='between(t,0,2.5)'" \
        -c:v libx264 -crf 20 -preset fast \
        "$WORK_DIR/s${i}_titled.mp4" 2>/dev/null
    ok "  s${i}: '${title}'"
done

# ---------------------------------------------------------------------------
# Stage 3: Generate intro and outro cards
# ---------------------------------------------------------------------------
log "Stage 3: Generating intro/outro cards..."

# Intro card: dark background with project name (2.5 seconds)
ffmpeg -y -f lavfi \
    -i "color=c=0x1a1a2e:s=1920x1080:d=2.5:r=30" \
    -vf "drawtext=text='Immich Memories':fontfile=${FONT}:fontsize=72:fontcolor=white:x=(w-tw)/2:y=(h-th)/2-40,drawtext=text='Turn your photo library into cinematic recap videos':fontfile=${FONT}:fontsize=28:fontcolor=0xaaaaaa:x=(w-tw)/2:y=(h-th)/2+50" \
    -c:v libx264 -crf 18 -preset fast -pix_fmt yuv420p \
    "$WORK_DIR/intro.mp4" 2>/dev/null
ok "  intro card (2.5s)"

# Outro card: dark background with call-to-action (3 seconds)
ffmpeg -y -f lavfi \
    -i "color=c=0x1a1a2e:s=1920x1080:d=3:r=30" \
    -vf "drawtext=text='Try it yourself':fontfile=${FONT}:fontsize=56:fontcolor=white:x=(w-tw)/2:y=(h-th)/2-60,drawtext=text='github.com/sam-dumont/immich-video-memory-generator':fontfile=${FONT}:fontsize=32:fontcolor=0x6C8EBF:x=(w-tw)/2:y=(h-th)/2+20,drawtext=text='Open source · Self-hosted · Privacy-first':fontfile=${FONT}:fontsize=24:fontcolor=0x888888:x=(w-tw)/2:y=(h-th)/2+80" \
    -c:v libx264 -crf 18 -preset fast -pix_fmt yuv420p \
    "$WORK_DIR/outro.mp4" 2>/dev/null
ok "  outro card (3s)"

# ---------------------------------------------------------------------------
# Stage 4: Concatenate with crossfade transitions
# ---------------------------------------------------------------------------
log "Stage 4: Concatenating sections with crossfade..."

FADE_DUR=0.5

# Build the complex filter graph for crossfades.
# Approach: chain xfade filters between consecutive segments.
# intro → s1 → s2 → s3 → s4 → outro

INPUTS=(
    "$WORK_DIR/intro.mp4"
    "$WORK_DIR/s1_titled.mp4"
    "$WORK_DIR/s2_titled.mp4"
    "$WORK_DIR/s3_titled.mp4"
    "$WORK_DIR/s4_titled.mp4"
    "$WORK_DIR/s5_titled.mp4"
    "$WORK_DIR/outro.mp4"
)

# Get durations of each input
durations=()
for f in "${INPUTS[@]}"; do
    dur=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$f")
    durations+=("$dur")
done

# Build input args
input_args=""
for f in "${INPUTS[@]}"; do
    input_args="$input_args -i $f"
done

# Build xfade filter chain
# Each xfade reduces total duration by FADE_DUR
# offset = cumulative_duration - (num_previous_fades * FADE_DUR)
filter=""
n=${#INPUTS[@]}
cumulative=0
prev_label="[0:v]"

for ((i=1; i<n; i++)); do
    # Offset = where this crossfade starts
    cumulative=$(echo "$cumulative + ${durations[$((i-1))]} - $FADE_DUR" | bc)
    out_label="[xf${i}]"
    # Last output doesn't need a label for the filter graph
    if ((i == n-1)); then
        out_label="[vout]"
    fi
    filter="${filter}${prev_label}[${i}:v]xfade=transition=fade:duration=${FADE_DUR}:offset=${cumulative}${out_label};"
    prev_label="$out_label"
done

# Remove trailing semicolon
filter="${filter%;}"

ffmpeg -y $input_args \
    -filter_complex "$filter" \
    -map "[vout]" \
    -c:v libx264 -crf 18 -preset fast -pix_fmt yuv420p \
    "$WORK_DIR/concatenated.mp4" 2>/dev/null
ok "  concatenated with ${FADE_DUR}s crossfades"

# ---------------------------------------------------------------------------
# Stage 5: Final encode (optimized for web)
# ---------------------------------------------------------------------------
log "Stage 5: Final encode..."

ffmpeg -y -i "$WORK_DIR/concatenated.mp4" \
    -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p \
    -movflags +faststart \
    -an \
    "$OUTPUT" 2>/dev/null

# Get final file size and duration
FILESIZE=$(du -h "$OUTPUT" | cut -f1)
DURATION=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$OUTPUT" | xargs printf "%.1f")

ok "Done! ${OUTPUT}"
ok "  Duration: ${DURATION}s  Size: ${FILESIZE}"
echo ""

# ---------------------------------------------------------------------------
# Cleanup work directory (keep raw recordings for re-assembly)
# ---------------------------------------------------------------------------
log "Cleaning up work directory..."
rm -rf "$WORK_DIR"
ok "Removed $WORK_DIR"
