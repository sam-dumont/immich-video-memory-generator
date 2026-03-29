import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";

type Props = { label: string; value: string; style?: React.CSSProperties };

export const ImInput: React.FC<Props> = ({ label, value, style }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 4, ...style }}>
    <span style={{ fontSize: 12, color: COLORS.textSecondary, fontFamily }}>
      {label}
    </span>
    <div
      style={{
        backgroundColor: COLORS.bg,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 6,
        padding: "8px 12px",
        fontSize: 14,
        color: COLORS.text,
        fontFamily,
      }}
    >
      {value}
    </div>
  </div>
);
