import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

type Props = {
  label: string;
  value: string;
  style?: React.CSSProperties;
  small?: boolean;
};

/**
 * Quasar-style filled select with bottom underline + dropdown chevron.
 * Matches the real NiceGUI app style.
 */
export const ImSelect: React.FC<Props> = ({ label, value, style, small }) => (
  <div style={{ display: "flex", flexDirection: "column", ...style }}>
    <div
      style={{
        backgroundColor: "rgba(255,255,255,0.05)",
        borderTopLeftRadius: 4,
        borderTopRightRadius: 4,
        padding: small ? "6px 10px 4px" : "8px 12px 6px",
        borderBottom: `2px solid ${COLORS.primary}`,
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "space-between",
      }}
    >
      <div style={{ flex: 1 }}>
        <div
          style={{
            fontSize: small ? 10 : 11,
            color: COLORS.textSecondary,
            fontFamily,
            marginBottom: 2,
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: small ? 13 : 14,
            color: COLORS.text,
            fontFamily,
            minHeight: small ? 18 : 20,
            lineHeight: small ? "18px" : "20px",
          }}
        >
          {value}
        </div>
      </div>
      <MaterialIcon
        name="arrow_drop_down"
        size={small ? 18 : 20}
        color={COLORS.textSecondary}
        style={{ marginBottom: -2 }}
      />
    </div>
  </div>
);
