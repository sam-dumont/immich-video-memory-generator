import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

type Props = {
  icon: string;
  value: string;
  label: string;
  style?: React.CSSProperties;
};

/**
 * Stat card matching the real NiceGUI app: icon top-left, big value, label below.
 * Used in Summary sections on Step 3 and Step 4.
 */
export const ImStatCard: React.FC<Props> = ({ icon, value, label, style }) => (
  <div
    style={{
      backgroundColor: COLORS.surface,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 8,
      padding: "12px 16px",
      flex: 1,
      minWidth: 0,
      ...style,
    }}
  >
    <MaterialIcon
      name={icon}
      size={20}
      color={COLORS.primary}
      style={{ marginBottom: 6, display: "block" }}
    />
    <div
      style={{
        fontSize: 18,
        fontWeight: 700,
        color: COLORS.text,
        fontFamily,
        lineHeight: 1.2,
      }}
    >
      {value}
    </div>
    <div
      style={{
        fontSize: 11,
        color: COLORS.textSecondary,
        fontFamily,
        marginTop: 2,
      }}
    >
      {label}
    </div>
  </div>
);
