import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS } from "../theme";

type CursorStep = {
  frame: number; // when to arrive at this position
  x: number; // target X (px from left of parent)
  y: number; // target Y (px from top of parent)
  click?: boolean; // pulse on arrival
};

type Props = {
  steps: CursorStep[];
};

/**
 * Animated cursor dot that moves between positions and "clicks".
 * Renders as a small white circle with blue glow that pulses on click.
 * Position is absolute within the parent container.
 */
export const AnimatedCursor: React.FC<Props> = ({ steps }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  if (steps.length === 0) return null;

  // Find current and previous step
  let currentIdx = 0;
  for (let i = steps.length - 1; i >= 0; i--) {
    if (frame >= steps[i].frame - 15) {
      currentIdx = i;
      break;
    }
  }

  const current = steps[currentIdx];
  const prev = currentIdx > 0 ? steps[currentIdx - 1] : current;

  // Smooth movement via spring
  const moveProgress = spring({
    frame: Math.max(0, frame - (current.frame - 15)),
    fps,
    config: { damping: 20, stiffness: 100 },
  });

  const x = interpolate(moveProgress, [0, 1], [prev.x, current.x]);
  const y = interpolate(moveProgress, [0, 1], [prev.y, current.y]);

  // Click pulse
  let clickScale = 1;
  let glowOpacity = 0.3;
  if (current.click && frame >= current.frame && frame < current.frame + 12) {
    const clickProgress = (frame - current.frame) / 12;
    clickScale = 1 + 0.6 * Math.sin(clickProgress * Math.PI);
    glowOpacity = 0.3 + 0.5 * Math.sin(clickProgress * Math.PI);
  }

  // Only visible after first step
  const visible = frame >= steps[0].frame - 15;
  if (!visible) return null;

  // Fade in
  const fadeIn = interpolate(
    frame,
    [steps[0].frame - 15, steps[0].frame - 5],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <div
      style={{
        position: "absolute",
        left: x - 7,
        top: y - 7,
        width: 14,
        height: 14,
        borderRadius: "50%",
        backgroundColor: "white",
        border: `2px solid ${COLORS.primary}`,
        boxShadow: `0 0 ${8 + clickScale * 6}px rgba(107, 143, 232, ${glowOpacity})`,
        transform: `scale(${clickScale})`,
        opacity: fadeIn,
        pointerEvents: "none",
        zIndex: 9999,
      }}
    />
  );
};
