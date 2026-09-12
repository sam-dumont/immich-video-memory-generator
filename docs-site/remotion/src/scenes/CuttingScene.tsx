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

const PHASE_TITLES = [
  "Finding media",
  "Loading thumbnails",
  "Reading the pictures",
  "Editing",
  "Done",
];

const DONE = PHASE_TITLES.length - 1;

// The same six pictures the hermetic fixture serves, in capture order. The real
// page shows this strip while the per-picture pass runs, and names its count.
const PREVIEWS = [
  "library/garden-table.jpg",
  "library/garden-cake.jpg",
  "library/woods-hamper.jpg",
  "library/woods-path.jpg",
  "library/lake-tents.jpg",
  "library/lake-sunset.jpg",
];
const PREVIEW_START = 46;
const PREVIEW_END = 126;

// Where the cut is, frame by frame. The stage strings are the exact labels the
// editorial planner reports through on_stage, in the order it reaches them.
const TIMELINE = [
  { at: 0, phase: 0, detail: "" },
  { at: 28, phase: 1, detail: "" },
  { at: 52, phase: 2, detail: "Preparing source metadata" },
  { at: 80, phase: 3, detail: "Reading event evidence" },
  { at: 102, phase: 3, detail: "Reading the period account" },
  { at: 124, phase: 3, detail: "Building editorial cards" },
  { at: 138, phase: 3, detail: "Editing the memory" },
  { at: 154, phase: 3, detail: "Validating selected source timing" },
  { at: 168, phase: DONE, detail: "" },
];

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

/** The strip of pictures the cut is working on, filling in one at a time. */
const PreviewStrip: React.FC<{ prepared: number; fps: number; frame: number }> = ({
  prepared,
  fps,
  frame,
}) => (
  <div style={{ marginTop: 22 }}>
    <div style={{ display: "flex", gap: 8 }}>
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
              width: 104,
              height: 62,
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
      style={{ fontSize: 13, color: COLORS.textSecondary, marginTop: 10 }}
    >
      previews {prepared} of {PREVIEWS.length}
    </div>
    <div
      style={{
        width: 460,
        height: 4,
        borderRadius: 2,
        marginTop: 6,
        backgroundColor: COLORS.border,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${(prepared / PREVIEWS.length) * 100}%`,
          height: "100%",
          backgroundColor: COLORS.primary,
        }}
      />
    </div>
  </div>
);

export const CuttingScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const prepared = Math.min(
    PREVIEWS.length,
    Math.max(
      0,
      Math.floor(
        interpolate(frame, [PREVIEW_START, PREVIEW_END], [0, PREVIEWS.length + 0.999], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        }),
      ),
    ),
  );

  const step =
    [...TIMELINE].reverse().find((s) => frame >= s.at) ?? TIMELINE[0];
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
        <Sidebar activeStep={1} />
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
                detail={i === step.phase ? step.detail : ""}
                detailOpacity={detailOpacity}
                reveal={rowReveal(i)}
              />
            ))}
          </div>

          <PreviewStrip prepared={prepared} fps={fps} frame={frame} />

          <div
            style={{
              fontSize: 14,
              color: COLORS.textSecondary,
              marginTop: 20,
              marginBottom: 16,
            }}
          >
            Elapsed: {elapsed}s
          </div>

          <div>
            <ImButton text="Cancel" variant="secondary" icon="stop" />
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
