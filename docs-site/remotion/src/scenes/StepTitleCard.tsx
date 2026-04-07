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

type Props = {
  step: number;
  title: string;
};

/**
 * Full-screen interstitial title card shown between wizard steps.
 * Centered text with a refined fade-in, hold, fade-out.
 * Uses the app's primary blue for the step number and white for the title.
 */
export const StepTitleCard: React.FC<Props> = ({ step, title }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Fade in over first 8 frames, hold, fade out over last 8
  const totalFrames = 30; // 1 second
  const fadeIn = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(frame, [totalFrames - 8, totalFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = Math.min(fadeIn, fadeOut);

  // Subtle upward drift
  const y = interpolate(frame, [0, totalFrames], [6, -6], {
    extrapolateRight: "clamp",
  });

  // Step number scales in with spring
  const numScale = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 140 },
    delay: 2,
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.bg,
        justifyContent: "center",
        alignItems: "center",
        opacity,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 12,
          transform: `translateY(${y}px)`,
        }}
      >
        {/* Step number pill */}
        <div
          style={{
            fontSize: 14,
            fontWeight: 600,
            fontFamily,
            color: COLORS.primary,
            letterSpacing: 3,
            textTransform: "uppercase",
            opacity: interpolate(numScale, [0, 1], [0, 1]),
            transform: `scale(${interpolate(numScale, [0, 1], [0.8, 1])})`,
          }}
        >
          Step {step}
        </div>

        {/* Title */}
        <div
          style={{
            fontSize: 44,
            fontWeight: 700,
            fontFamily,
            color: COLORS.text,
            letterSpacing: -1,
          }}
        >
          {title}
        </div>

        {/* Thin accent line */}
        <div
          style={{
            width: 48,
            height: 2,
            backgroundColor: COLORS.primary,
            borderRadius: 1,
            marginTop: 4,
            opacity: interpolate(numScale, [0, 1], [0, 0.6]),
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
