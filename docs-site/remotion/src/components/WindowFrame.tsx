import React from "react";
import {
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";

type CameraZoom = {
  targetX: number;
  targetY: number;
  scale: number;
  startFrame: number;
  durationFrames: number;
};

type Props = {
  children: React.ReactNode;
  zoom?: CameraZoom;
  bassIntensity?: number;
};

const W = 1600;
const H = 900;
const TITLE_H = 36;

export const WindowFrame: React.FC<Props> = ({
  children,
  zoom,
  bassIntensity = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Entry: float up with spring
  const entry = spring({
    frame,
    fps,
    config: { damping: 15, stiffness: 80, mass: 1.2 },
  });
  const entryY = interpolate(entry, [0, 1], [60, 0]);
  const entryScale = interpolate(entry, [0, 1], [0.92, 1]);
  const entryOpacity = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });

  // No float — stable window
  const floatY = 0;

  // Camera zoom (whole window scales)
  let camScale = 1;
  let camX = 0;
  let camY = 0;
  if (zoom) {
    const progress = interpolate(
      frame,
      [zoom.startFrame, zoom.startFrame + zoom.durationFrames],
      [0, 1],
      { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
    );
    const eased = Easing.inOut(Easing.cubic)(progress);
    camScale = 1 + (zoom.scale - 1) * eased;
    const cx = W / 2;
    const cy = H / 2;
    const tx = zoom.targetX * W;
    const ty = zoom.targetY * H;
    camX = ((cx - tx) * (camScale - 1)) / camScale;
    camY = ((cy - ty) * (camScale - 1)) / camScale;
  }

  // Bass-reactive shadow
  const shadowY = 30 + bassIntensity * 20;
  const shadowBlur = 60 + bassIntensity * 40;
  const shadowAlpha = 0.4 + bassIntensity * 0.2;

  return (
    <div
      style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        width: W,
        height: H,
        marginLeft: -W / 2,
        marginTop: -H / 2,
        borderRadius: 12,
        overflow: "hidden",
        opacity: entryOpacity,
        boxShadow: `0 ${shadowY}px ${shadowBlur}px rgba(0,0,0,${shadowAlpha})`,
        transform: `translateY(${entryY + floatY}px) scale(${entryScale * camScale}) translate(${camX}px, ${camY}px)`,
        transformOrigin: "center center",
      }}
    >
      {/* Title bar */}
      <div
        style={{
          height: TITLE_H,
          backgroundColor: COLORS.titleBar,
          display: "flex",
          alignItems: "center",
          paddingLeft: 14,
          gap: 7,
          flexShrink: 0,
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
            fontFamily,
            marginRight: 60,
          }}
        >
          Immich Memories — localhost:8099
        </div>
      </div>

      {/* Content */}
      <div
        style={{
          width: W,
          height: H - TITLE_H,
          display: "flex",
          overflow: "hidden",
          backgroundColor: COLORS.bg,
        }}
      >
        {children}
      </div>
    </div>
  );
};
