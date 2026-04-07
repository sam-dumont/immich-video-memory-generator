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
import { ImBadge } from "../components/ImBadge";
import { ImButton } from "../components/ImButton";
import { ImProgressBar } from "../components/ImProgressBar";

type Props = { bassIntensity?: number };

/* ------------------------------------------------------------------ */
/*  Phase label pill — "1. Clustering", "2. Filtering", etc.           */
/* ------------------------------------------------------------------ */
const PhaseLabel: React.FC<{
  num: number;
  label: string;
  active: boolean;
  done: boolean;
  opacity: number;
}> = ({ num, label, active, done, opacity }) => (
  <span
    style={{
      fontSize: 12,
      fontFamily,
      fontWeight: active ? 700 : 400,
      color: done
        ? COLORS.success
        : active
          ? COLORS.primary
          : COLORS.textSecondary,
      opacity,
      marginRight: 12,
    }}
  >
    {num}. {label}
  </span>
);

/* ------------------------------------------------------------------ */
/*  Stat pill — "3/18 clips", "Elapsed: 2s", etc.                     */
/* ------------------------------------------------------------------ */
const StatPill: React.FC<{ text: string }> = ({ text }) => (
  <span
    style={{
      fontSize: 12,
      fontFamily,
      color: COLORS.textSecondary,
    }}
  >
    {text}
  </span>
);

/* ------------------------------------------------------------------ */
/*  Result stat box — large number with label                          */
/* ------------------------------------------------------------------ */
const ResultStat: React.FC<{
  label: string;
  value: string;
  opacity: number;
  translateY: number;
}> = ({ label, value, opacity, translateY }) => (
  <div
    style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: 4,
      opacity,
      transform: `translateY(${translateY}px)`,
    }}
  >
    <span style={{ fontSize: 13, color: COLORS.textSecondary, fontFamily }}>
      {label}
    </span>
    <span
      style={{
        fontSize: 24,
        fontWeight: 700,
        color: COLORS.text,
        fontFamily,
      }}
    >
      {value}
    </span>
  </div>
);

/* ================================================================== */
/*  Scene                                                              */
/* ================================================================== */

const PHASE_SWITCH = 80; // frame where pipeline "completes"

export const PipelineScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Phase 1 running state (0-80)
  const isPhase1 = frame < PHASE_SWITCH;
  const phase1Opacity = interpolate(
    frame,
    [PHASE_SWITCH - 12, PHASE_SWITCH],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const phase2Opacity = interpolate(
    frame,
    [PHASE_SWITCH, PHASE_SWITCH + 12],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Progress bar fills over phase 1
  const progress = interpolate(frame, [5, PHASE_SWITCH - 5], [0, 100], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Phase label progression: which phase is "current"
  const currentPhase =
    frame < 20 ? 1 : frame < 40 ? 2 : frame < 65 ? 3 : 4;

  // Stagger for phase 1 elements
  const p1Reveal = (delay: number) => {
    const s = spring({ frame, fps, config: { damping: 18, stiffness: 160 }, delay });
    return {
      opacity: interpolate(s, [0, 1], [0, 1]),
      translateY: interpolate(s, [0, 1], [12, 0]),
    };
  };

  // Stagger for phase 2 elements
  const p2Reveal = (delay: number) => {
    const d = delay + PHASE_SWITCH;
    const s = spring({ frame, fps, config: { damping: 18, stiffness: 160 }, delay: d });
    return {
      opacity: interpolate(s, [0, 1], [0, 1]),
      translateY: interpolate(s, [0, 1], [12, 0]),
    };
  };

  const r0 = p1Reveal(0);
  const r1 = p1Reveal(5);
  const r2 = p1Reveal(10);
  const r3 = p1Reveal(15);
  const r4 = p1Reveal(20);
  const r5 = p1Reveal(25);

  const p0 = p2Reveal(0);
  const p1 = p2Reveal(5);
  const p2 = p2Reveal(10);
  const p3 = p2Reveal(15);

  // Card slide-in for "Currently Analyzing" / "Last Analyzed"
  const cardLeft = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 120 },
    delay: 20,
  });
  const cardRight = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 120 },
    delay: 28,
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={2} completedSteps={[1]} />
        <div
          style={{
            flex: 1,
            overflow: "hidden",
            position: "relative",
            padding: "24px 32px",
            fontFamily,
          }}
        >
          {/* Page title — always visible */}
          <h1
            style={{
              fontSize: 22,
              fontWeight: 700,
              color: COLORS.text,
              margin: 0,
              fontFamily,
            }}
          >
            Clip Review
          </h1>
          <div
            style={{
              fontSize: 13,
              color: COLORS.textSecondary,
              marginTop: 4,
              marginBottom: 20,
              fontFamily,
            }}
          >
            Person Spotlight — 2025
          </div>

          {/* ============ PHASE 1: Pipeline running ============ */}
          <div style={{ opacity: phase1Opacity }}>
            {/* "Generating Memories..." title */}
            <div
              style={{
                fontSize: 18,
                fontWeight: 700,
                color: COLORS.text,
                marginBottom: 16,
                opacity: r0.opacity,
                transform: `translateY(${r0.translateY}px)`,
                fontFamily,
              }}
            >
              Generating Memories....
            </div>

            {/* Phase labels row */}
            <div
              style={{
                display: "flex",
                marginBottom: 16,
                opacity: r1.opacity,
                transform: `translateY(${r1.translateY}px)`,
              }}
            >
              <PhaseLabel
                num={1}
                label="Clustering"
                active={currentPhase === 1}
                done={currentPhase > 1}
                opacity={1}
              />
              <PhaseLabel
                num={2}
                label="Filtering"
                active={currentPhase === 2}
                done={currentPhase > 2}
                opacity={1}
              />
              <PhaseLabel
                num={3}
                label="Analyzing"
                active={currentPhase === 3}
                done={currentPhase > 3}
                opacity={1}
              />
              <PhaseLabel
                num={4}
                label="Refining"
                active={currentPhase === 4}
                done={false}
                opacity={1}
              />
            </div>

            {/* Progress bar */}
            <div
              style={{
                marginBottom: 12,
                opacity: r2.opacity,
                transform: `translateY(${r2.translateY}px)`,
              }}
            >
              <ImProgressBar progress={progress} />
            </div>

            {/* Stats row */}
            <div
              style={{
                display: "flex",
                gap: 24,
                marginBottom: 12,
                opacity: r3.opacity,
                transform: `translateY(${r3.translateY}px)`,
              }}
            >
              <StatPill text="3/18 clips" />
              <StatPill text="Elapsed: 2s" />
              <StatPill text="Speed: 18.7x realtime" />
              <StatPill text="~0.3s/clip" />
              <StatPill text="ETA: 4s" />
            </div>

            {/* Status text */}
            <div
              style={{
                fontSize: 13,
                color: COLORS.textSecondary,
                marginBottom: 16,
                opacity: r4.opacity,
                transform: `translateY(${r4.translateY}px)`,
                fontFamily,
              }}
            >
              Analyzing Selected Clips: IMG_3678.MOV
            </div>

            {/* Two side-by-side cards */}
            <div
              style={{
                display: "flex",
                gap: 16,
                opacity: r5.opacity,
                transform: `translateY(${r5.translateY}px)`,
              }}
            >
              {/* Currently Analyzing */}
              <div
                style={{
                  flex: 1,
                  backgroundColor: COLORS.surface,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: 8,
                  overflow: "hidden",
                  opacity: interpolate(cardLeft, [0, 1], [0, 1]),
                  transform: `translateX(${interpolate(cardLeft, [0, 1], [-20, 0])}px)`,
                }}
              >
                <div
                  style={{
                    padding: "8px 12px",
                    fontSize: 12,
                    fontWeight: 600,
                    color: COLORS.success,
                    fontFamily,
                  }}
                >
                  Currently Analyzing
                </div>
                <Img
                  src={staticFile("stock/thumb-5.jpg")}
                  style={{
                    width: "100%",
                    height: 180,
                    objectFit: "cover",
                  }}
                />
                <div
                  style={{
                    padding: "8px 12px",
                    fontSize: 12,
                    color: COLORS.textSecondary,
                    fontFamily,
                  }}
                >
                  IMG_3678.MOV
                </div>
                {/* Analyzing indicator */}
                <div
                  style={{
                    padding: "4px 12px 10px",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    opacity: interpolate(
                      (frame % 30) / 30,
                      [0, 0.5, 1],
                      [0.4, 1, 0.4],
                    ),
                  }}
                >
                  <span
                    style={{
                      fontSize: 14,
                      display: "inline-block",
                      transform: `rotate(${(frame % 30) * 12}deg)`,
                    }}
                  >
                    &#x27F3;
                  </span>
                  <span
                    style={{
                      fontSize: 11,
                      color: COLORS.primary,
                      fontFamily,
                      fontWeight: 500,
                    }}
                  >
                    Analyzing...
                  </span>
                </div>
              </div>

              {/* Last Analyzed */}
              <div
                style={{
                  flex: 1,
                  backgroundColor: COLORS.surface,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: 8,
                  overflow: "hidden",
                  opacity: interpolate(cardRight, [0, 1], [0, 1]),
                  transform: `translateX(${interpolate(cardRight, [0, 1], [20, 0])}px)`,
                }}
              >
                <div
                  style={{
                    padding: "8px 12px",
                    fontSize: 12,
                    fontWeight: 600,
                    color: COLORS.textSecondary,
                    fontFamily,
                  }}
                >
                  Last Analyzed
                </div>
                <Img
                  src={staticFile("stock/thumb-8.jpg")}
                  style={{
                    width: "100%",
                    height: 180,
                    objectFit: "cover",
                  }}
                />
                <div
                  style={{
                    padding: "8px 12px",
                    fontSize: 12,
                    color: COLORS.textSecondary,
                    fontFamily,
                  }}
                >
                  IMG_3452.MOV
                </div>
                {/* Analysis results */}
                <div style={{ padding: "4px 12px 10px" }}>
                  {/* LLM description */}
                  <div
                    style={{
                      fontSize: 11,
                      color: COLORS.text,
                      fontFamily,
                      lineHeight: 1.4,
                      marginBottom: 8,
                      opacity: p1Reveal(35).opacity,
                      transform: `translateY(${p1Reveal(35).translateY}px)`,
                    }}
                  >
                    Outdoor scene with natural lighting, high visual quality
                  </div>
                  {/* Badges */}
                  <div
                    style={{
                      display: "flex",
                      gap: 6,
                      marginBottom: 6,
                      opacity: p1Reveal(45).opacity,
                      transform: `translateY(${p1Reveal(45).translateY}px)`,
                    }}
                  >
                    <ImBadge text="emotion=calm" variant="success" />
                    <ImBadge text="score=0.82" variant="info" />
                  </div>
                  {/* Face detection */}
                  <div
                    style={{
                      fontSize: 10,
                      color: COLORS.textSecondary,
                      fontFamily,
                      opacity: p1Reveal(50).opacity,
                      transform: `translateY(${p1Reveal(50).translateY}px)`,
                    }}
                  >
                    Scene analysis complete
                  </div>
                </div>
              </div>
            </div>

            {/* Cancel button */}
            <div style={{ marginTop: 16, display: "flex", justifyContent: "center" }}>
              <ImButton text="CANCEL" variant="secondary" icon="close" />
            </div>
          </div>

          {/* ============ PHASE 2: Pipeline complete ============ */}
          {!isPhase1 && (
            <div
              style={{
                position: "absolute",
                top: 90,
                left: 32,
                right: 32,
                opacity: phase2Opacity,
              }}
            >
              {/* Success card */}
              <div
                style={{
                  backgroundColor: COLORS.surface,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: 10,
                  padding: "20px 24px",
                  marginBottom: 20,
                  opacity: p0.opacity,
                  transform: `translateY(${p0.translateY}px)`,
                }}
              >
                {/* Green success text */}
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 600,
                    color: COLORS.success,
                    marginBottom: 20,
                    fontFamily,
                    opacity: p1.opacity,
                    transform: `translateY(${p1.translateY}px)`,
                  }}
                >
                  Pipeline complete! Selected 15 clips from 18 analyzed.
                </div>

                {/* 3 stat boxes */}
                <div
                  style={{
                    display: "flex",
                    gap: 48,
                    opacity: p2.opacity,
                    transform: `translateY(${p2.translateY}px)`,
                  }}
                >
                  <ResultStat
                    label="Clips Selected"
                    value="15"
                    opacity={1}
                    translateY={0}
                  />
                  <ResultStat
                    label="Clips Analyzed"
                    value="18"
                    opacity={1}
                    translateY={0}
                  />
                  <ResultStat
                    label="Time Elapsed"
                    value="26s"
                    opacity={1}
                    translateY={0}
                  />
                </div>
              </div>

              {/* Action buttons */}
              <div
                style={{
                  display: "flex",
                  gap: 12,
                  opacity: p3.opacity,
                  transform: `translateY(${p3.translateY}px)`,
                }}
              >
                <ImButton
                  text="REVIEW & REFINE SELECTED CLIPS"
                  variant="primary"
                  icon="edit"
                />
                <ImButton
                  text="START OVER (SELECT DIFFERENT CLIPS)"
                  variant="secondary"
                  icon="refresh"
                />
              </div>
            </div>
          )}
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
