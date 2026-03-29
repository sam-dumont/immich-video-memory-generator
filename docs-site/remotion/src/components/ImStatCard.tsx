import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";

type Props = { icon: string; value: string; label: string };

export const ImStatCard: React.FC<Props> = ({ icon, value, label }) => (
  <div
    style={{
      backgroundColor: COLORS.surface,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 8,
      padding: "12px 16px",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: 4,
      minWidth: 100,
    }}
  >
    <span style={{ fontSize: 20 }}>{icon}</span>
    <span
      style={{
        fontSize: 18,
        fontWeight: 600,
        color: COLORS.text,
        fontFamily,
      }}
    >
      {value}
    </span>
    <span
      style={{ fontSize: 11, color: COLORS.textSecondary, fontFamily }}
    >
      {label}
    </span>
  </div>
);
