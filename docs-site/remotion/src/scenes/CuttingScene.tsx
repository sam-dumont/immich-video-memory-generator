import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { PageHeader } from "../components/PageHeader";
import { ImButton } from "../components/ImButton";
import { MaterialIcon } from "../components/MaterialIcon";
import { POOL_TOTAL, SHOTS } from "../fixture";

const PHASE_TITLES = [
  "Finding media",
  "Loading thumbnails",
  "Reading the pictures",
  "Editing",
  "Done",
];

const DONE = PHASE_TITLES.length - 1;

// The strip of pictures the per-picture pass is working on, from the fixture's
// cut; the bar counts the whole pool the way the real page does.
const PREVIEWS = SHOTS.slice(0, 8).map((shot) => shot.picture);
const TOTAL = POOL_TOTAL;
const PREVIEW_START = 46;
const PREVIEW_END = 126;

// Where the cut is, frame by frame. The stage strings are the exact labels the
// editorial planner reports through on_stage, in the order it reaches them.
const TIMELINE = [
  { at: 0, phase: 0, detail: "" },
  { at: 28, phase: 1, detail: "" },
  { at: 52, phase: 2, detail: "Preparing previews: 2/6" },
  { at: 80, phase: 2, detail: "Reading dates, places and people" },
  { at: 100, phase: 3, detail: "Reading event evidence: 3/9" },
  { at: 120, phase: 3, detail: "Reading the period account" },
  { at: 136, phase: 3, detail: "Building editorial cards" },
  { at: 150, phase: 3, detail: "Editing the memory" },
  { at: 162, phase: 3, detail: "Validating selected source timing" },
  { at: 172, phase: DONE, detail: "" },
];

// The strip of pictures belongs to the per-picture passes; once the edit starts it fades.
const STRIP_FADE_AT = 100;

// The page prints the stage's own estimate beside the count. The strip spans
// PREVIEW_START..PREVIEW_END at 30fps, so what is left follows from the count.
const STAGE_SECONDS = (PREVIEW_END - PREVIEW_START) / 30;
const remainingSeconds = (prepared: number) =>
  Math.max(1, Math.ceil(STAGE_SECONDS * (1 - prepared / TOTAL)));

type Props = { bassIntensity?: number };

const PhaseRow: React.FC<{
  title: string;
  state: "done" | "active" | "upcoming";
  detail: string;
  detailOpacity: number;
  reveal: number;
}> = ({ title, state, detail, detailOpacity, reveal }) => {
  const icon =
    state === "done"
      ? "check_circle"
      : state === "active"
        ? "pending"
        : "radio_button_unchecked";
  const color =
    state === "done"
      ? COLORS.success
      : state === "active"
        ? COLORS.primary
        : COLORS.textMuted;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        height: 28,
        opacity: reveal,
        transform: `translateX(${(1 - reveal) * -10}px)`,
      }}
    >
      <MaterialIcon name={icon} size={18} color={color} />
      <span style={{ fontSize: 14, color: COLORS.text, width: 170 }}>
        {title}
      </span>
      <span
        style={{
          fontSize: 14,
          color: COLORS.textSecondary,
          opacity: detailOpacity,
        }}
      >
        {detail}
      </span>
    </div>
  );
};

/** The strip of pictures the cut has just finished, then the stage's own bar. */
const PreviewStrip: React.FC<{ prepared: number; fps: number; frame: number }> = ({
  prepared,
  fps,
  frame,
}) => (
  <div style={{ marginTop: 18 }}>
    <div style={{ display: "flex", gap: 6 }}>
      {PREVIEWS.map((picture, i) => {
        const at = PREVIEW_START + ((PREVIEW_END - PREVIEW_START) / PREVIEWS.length) * i;
        const entry = spring({
          frame: frame - at,
          fps,
          config: { damping: 22, stiffness: 190 },
        });
        return (
          <div
            key={picture}
            style={{
              width: 78,
              height: 78,
              borderRadius: 6,
              overflow: "hidden",
              opacity: i < prepared ? entry : 0,
              transform: `scale(${0.9 + entry * 0.1})`,
              backgroundColor: COLORS.border,
            }}
          >
            <Img
              src={staticFile(picture)}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          </div>
        );
      })}
    </div>
    <div
      style={{
        width: "100%",
        height: 4,
        borderRadius: 2,
        marginTop: 16,
        backgroundColor: COLORS.border,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${(prepared / TOTAL) * 100}%`,
          height: "100%",
          backgroundColor: COLORS.primary,
        }}
      />
    </div>
    <div style={{ fontSize: 13, color: COLORS.textSecondary, marginTop: 8 }}>
      previews {prepared} of {TOTAL} &middot; ~{remainingSeconds(prepared)}s left in this stage
    </div>
  </div>
);

export const CuttingScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const prepared = Math.min(
    TOTAL,
    Math.max(
      0,
      Math.floor(
        interpolate(frame, [PREVIEW_START, PREVIEW_END], [0, TOTAL + 0.999], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
      ),
    ),
  );

  const step =
    [...TIMELINE].reverse().find((s) => frame >= s.at) ?? TIMELINE[0];
  // While the previews are being prepared, the row detail carries the live count.
  const liveDetail =
    step.detail.startsWith("Preparing previews")
      ? `Preparing previews: ${prepared}/${TOTAL}`
      : step.detail;
  const finished = step.phase === DONE;

  // The stage string swaps in rather than jumping.
  const detailOpacity = interpolate(frame, [step.at, step.at + 5], [0.2, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const elapsed = Math.floor(frame / 6);

  const rowReveal = (i: number) =>
    spring({ frame, fps, config: { damping: 20, stiffness: 180 }, delay: 6 + i * 4 });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar active="Memory" />
        <div
          style={{
            flex: 1,
            padding: "20px 32px",
            overflow: "hidden",
            fontFamily,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <PageHeader title="Memory" />

          <div
            style={{
              fontSize: 24,
              fontWeight: 700,
              color: COLORS.text,
              marginBottom: 18,
            }}
          >
            Cutting the memory...
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {PHASE_TITLES.map((title, i) => (
              <PhaseRow
                key={title}
                title={title}
                state={
                  i < step.phase || (finished && i === DONE)
                    ? "done"
                    : i === step.phase
                      ? "active"
                      : "upcoming"
                }
                detail={i === step.phase ? liveDetail : ""}
                detailOpacity={detailOpacity}
                reveal={rowReveal(i)}
              />
            ))}
          </div>

          <div
            style={{
              opacity: interpolate(frame, [STRIP_FADE_AT, STRIP_FADE_AT + 10], [1, 0], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            <PreviewStrip prepared={prepared} fps={fps} frame={frame} />
          </div>

          <div
            style={{
              fontSize: 14,
              color: COLORS.textSecondary,
              marginTop: 18,
            }}
          >
            Elapsed: {elapsed}s
          </div>

          {/* The engine's own stage lines, folded away as the page folds them */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              marginTop: 14,
              marginBottom: 18,
              padding: "10px 0",
            }}
          >
            <MaterialIcon name="list" size={20} color={COLORS.textSecondary} />
            <span style={{ fontSize: 14, color: COLORS.text, flex: 1 }}>Details</span>
            <MaterialIcon
              name="keyboard_arrow_down"
              size={22}
              color={COLORS.textSecondary}
            />
          </div>

          <div>
            <ImButton text="Cancel" variant="secondary" icon="stop" />
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
