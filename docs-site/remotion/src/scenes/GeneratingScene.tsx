import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { ImCard } from "../components/ImCard";
import { ImProgressBar } from "../components/ImProgressBar";

const PHASES = [
  { at: 0, label: "Analyzing clips..." },
  { at: 15, label: "Rendering title screen..." },
  { at: 40, label: "Encoding video..." },
  { at: 85, label: "Mixing audio..." },
  { at: 100, label: "Complete!" },
];

const LOG_LINES = [
  "Phase 1: Clustered 177 → 173 clips (4 duplicates)",
  "Quality gate: removed 101 clips (below 1425px)",
  "TaichiTitleRenderer: 2160×3840 @ 60fps",
  "Streaming assembly: 12 clips at 1920×1080",
  "HLG HDR preservation enabled",
  "Assembly complete: 12 clips → output.mp4",
];

type Props = { bassIntensity?: number };

export const GeneratingScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = fps * 5; // 5 seconds

  // Exponential progress: 5 → 10 → 25 → 55 → 100
  const progress = interpolate(
    frame,
    [
      0,
      totalFrames * 0.15,
      totalFrames * 0.35,
      totalFrames * 0.6,
      totalFrames * 0.8,
      totalFrames * 0.95,
    ],
    [0, 5, 10, 25, 55, 100],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.quad),
    },
  );

  const currentPhase =
    PHASES.findLast((p) => progress >= p.at) ?? PHASES[0];

  // Log lines appear progressively
  const logCount = Math.min(
    LOG_LINES.length,
    Math.floor(
      interpolate(
        frame,
        [10, totalFrames * 0.9],
        [0, LOG_LINES.length],
        {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        },
      ),
    ),
  );
  const visibleLogs = LOG_LINES.slice(
    Math.max(0, logCount - 3),
    logCount,
  );

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame
        bassIntensity={bassIntensity}
        zoom={{
          targetX: 0.55,
          targetY: 0.4,
          scale: 1.4,
          startFrame: 10,
          durationFrames: 50,
        }}
      >
        <Sidebar activeStep={4} completedSteps={[1, 2, 3]} />
        <div
          style={{
            flex: 1,
            padding: 24,
            fontFamily,
            overflow: "hidden",
          }}
        >
          <ImCard>
            <div
              style={{
                marginBottom: 16,
                fontSize: 15,
                fontWeight: 600,
                color: COLORS.text,
              }}
            >
              {currentPhase.label}
            </div>
            <ImProgressBar progress={progress} />

            {/* Log lines */}
            <div style={{ marginTop: 16 }}>
              {visibleLogs.map((log) => (
                <div
                  key={log}
                  style={{
                    fontSize: 12,
                    color: COLORS.textSecondary,
                    lineHeight: 1.8,
                    fontFamily: "monospace",
                  }}
                >
                  │ {log}
                </div>
              ))}
            </div>
          </ImCard>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
