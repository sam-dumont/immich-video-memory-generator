import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";

type Props = {
  label: string;
  value: string;
  placeholder?: string;
  style?: React.CSSProperties;
};

/**
 * Quasar-style filled input with bottom underline.
 * Matches the real NiceGUI app: dark fill, small label, underline accent.
 */
export const ImInput: React.FC<Props> = ({
  label,
  value,
  placeholder,
  style,
}) => (
  <div style={{ display: "flex", flexDirection: "column", ...style }}>
    <div
      style={{
        backgroundColor: "rgba(255,255,255,0.05)",
        borderTopLeftRadius: 4,
        borderTopRightRadius: 4,
        padding: "8px 12px 6px",
        borderBottom: `2px solid ${COLORS.primary}`,
      }}
    >
      <div
        style={{
          fontSize: 11,
          color: COLORS.textSecondary,
          fontFamily,
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 14,
          color: value ? COLORS.text : COLORS.textSecondary,
          fontFamily,
          minHeight: 20,
          lineHeight: "20px",
        }}
      >
        {value || placeholder || ""}
      </div>
    </div>
  </div>
);
