import React from "react";
import { COLORS } from "../theme";

type Props = {
  progress: number; // 0-100, driven by interpolate() in the scene
  label?: string;
};

export const ImProgressBar: React.FC<Props> = ({ progress, label }) => (
  <div>
    {label && (
      <div
        style={{
          fontSize: 13,
          color: COLORS.textSecondary,
          marginBottom: 6,
        }}
      >
        {label}
      </div>
    )}
    <div
      style={{
        width: "100%",
        height: 6,
        backgroundColor: "rgba(255,255,255,0.06)",
        borderRadius: 3,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${Math.min(progress, 100)}%`,
          height: "100%",
          backgroundColor:
            progress >= 100 ? COLORS.success : COLORS.primary,
          borderRadius: 3,
        }}
      />
    </div>
    <div
      style={{
        fontSize: 12,
        color: COLORS.textSecondary,
        marginTop: 4,
        textAlign: "right",
      }}
    >
      {Math.round(progress)}%
    </div>
  </div>
);
