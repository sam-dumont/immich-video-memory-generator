import React from "react";
import {
  AbsoluteFill,
  interpolate,
  OffthreadVideo,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { fontFamily } from "../fonts";

/**
 * The real rendered memory, at 3x speed. It is cut by the product itself on the
 * hermetic launch, over the six CC0 stock photographs in tests/e2e/fixtures --
 * see `make demo-output`. Nothing in it is anyone's library.
 */
export const OutputPreviewScene: React.FC = () => {
  const frame = useCurrentFrame();

  // 5x speed badge — fade in, hold, fade out at end
  const badgeOpacity = interpolate(
    frame,
    [5, 15, 195, 225],
    [0, 0.85, 0.85, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Thin progress line at bottom
  const progress = (frame / 235) * 100;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {/* The actual rendered memory video at 5x speed */}
      <OffthreadVideo
        src={staticFile("output-preview.mp4")}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "contain",
        }}
        playbackRate={3}
        volume={0}
      />

      {/* 5x speed badge */}
      <div
        style={{
          position: "absolute",
          top: 24,
          right: 24,
          backgroundColor: "rgba(0,0,0,0.6)",
          backdropFilter: "blur(8px)",
          borderRadius: 8,
          padding: "6px 14px",
          display: "flex",
          alignItems: "center",
          gap: 6,
          opacity: badgeOpacity,
        }}
      >
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "rgba(255,255,255,0.9)",
            fontFamily,
            letterSpacing: 0.5,
          }}
        >
          3× speed
        </span>
      </div>

      {/* Progress line at bottom */}
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          height: 3,
          backgroundColor: "rgba(123, 155, 240, 0.8)",
          width: `${progress}%`,
          borderRadius: 2,
        }}
      />
    </AbsoluteFill>
  );
};
