import React from "react";
import {
  AbsoluteFill,
  interpolate,
  OffthreadVideo,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";

export const CliScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const entry = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 80 },
  });
  const entryY = interpolate(entry, [0, 1], [50, 0]);
  const entryScale = interpolate(entry, [0, 1], [0.93, 1]);
  const entryOpacity = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });
  const floatY = 0;

  // "Also available as CLI" label
  const labelOpacity = interpolate(frame, [8, 25], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <div
        style={{
          position: "absolute",
          top: 30,
          left: 0,
          right: 0,
          textAlign: "center",
          opacity: labelOpacity,
        }}
      >
        <span
          style={{
            fontSize: 18,
            color: COLORS.textSecondary,
            fontFamily,
            fontWeight: 500,
            letterSpacing: 1,
          }}
        >
          Also available as CLI
        </span>
      </div>

      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          width: 1500,
          height: 850,
          marginLeft: -750,
          marginTop: -425,
          borderRadius: 12,
          overflow: "hidden",
          opacity: entryOpacity,
          boxShadow: "0 30px 70px rgba(0,0,0,0.5)",
          transform: `translateY(${entryY + floatY}px) scale(${entryScale})`,
        }}
      >
        <div
          style={{
            height: 36,
            backgroundColor: COLORS.terminalTitleBar,
            display: "flex",
            alignItems: "center",
            paddingLeft: 14,
            gap: 7,
          }}
        >
          <div
            style={{
              width: 11,
              height: 11,
              borderRadius: "50%",
              backgroundColor: "#ff5f57",
            }}
          />
          <div
            style={{
              width: 11,
              height: 11,
              borderRadius: "50%",
              backgroundColor: "#febc2e",
            }}
          />
          <div
            style={{
              width: 11,
              height: 11,
              borderRadius: "50%",
              backgroundColor: "#28c840",
            }}
          />
          <div
            style={{
              flex: 1,
              textAlign: "center",
              fontSize: 12,
              color: "#6e7681",
              fontFamily: "monospace",
              marginRight: 60,
            }}
          >
            Terminal — immich-memories generate
          </div>
        </div>

        <OffthreadVideo
          src={staticFile("cli-demo.mp4")}
          style={{
            width: "100%",
            height: "calc(100% - 36px)",
            objectFit: "cover",
          }}
          muted
          playbackRate={5}
        />
      </div>
    </AbsoluteFill>
  );
};
