import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
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

export const CuttingScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

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
