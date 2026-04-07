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

export const OutroScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const cta = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 10,
  });
  const url = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 25,
  });
  const tag = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 40,
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.bg,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 24,
        }}
      >
        <div
          style={{
            fontSize: 52,
            fontWeight: 700,
            color: "white",
            fontFamily,
            opacity: interpolate(cta, [0, 1], [0, 1]),
            transform: `translateY(${interpolate(cta, [0, 1], [18, 0])}px)`,
            letterSpacing: -1,
          }}
        >
          Try it yourself
        </div>
        <div
          style={{
            fontSize: 24,
            color: "#58a6ff",
            fontFamily: "monospace",
            opacity: interpolate(url, [0, 1], [0, 1]),
          }}
        >
          github.com/sam-dumont/immich-video-memory-generator
        </div>
        <div
          style={{
            fontSize: 20,
            color: COLORS.textSecondary,
            fontFamily,
            opacity: interpolate(tag, [0, 1], [0, 1]),
            letterSpacing: 2,
          }}
        >
          Open source · Self-hosted · Privacy-first
        </div>
      </div>
    </AbsoluteFill>
  );
};
