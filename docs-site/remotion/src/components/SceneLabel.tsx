import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { fontFamily } from "../fonts";

type Props = {
  text: string;
  fadeInFrame?: number;
  fadeOutFrame?: number;
};

export const SceneLabel: React.FC<Props> = ({
  text,
  fadeInFrame = 5,
  fadeOutFrame = 80,
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    frame,
    [fadeInFrame, fadeInFrame + 12, fadeOutFrame, fadeOutFrame + 12],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const y = interpolate(frame, [fadeInFrame, fadeInFrame + 12], [12, 0], {
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        position: "absolute",
        bottom: 28,
        left: 0,
        right: 0,
        textAlign: "center",
        opacity,
        transform: `translateY(${y}px)`,
      }}
    >
      <span
        style={{
          fontSize: 20,
          color: "white",
          fontFamily,
          fontWeight: 500,
          backgroundColor: "rgba(0,0,0,0.65)",
          padding: "7px 22px",
          borderRadius: 8,
        }}
      >
        {text}
      </span>
    </div>
  );
};
