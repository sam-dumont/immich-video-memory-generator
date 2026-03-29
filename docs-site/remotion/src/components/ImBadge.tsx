import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

type Props = {
  text: string;
  variant: "success" | "warning" | "error" | "info";
  icon?: string;
};

const BADGE_COLORS = {
  success: { bg: "rgba(74, 222, 128, 0.15)", text: COLORS.success },
  warning: { bg: "rgba(251, 191, 36, 0.15)", text: COLORS.warning },
  error: { bg: "rgba(248, 113, 113, 0.15)", text: COLORS.error },
  info: { bg: "rgba(107, 143, 232, 0.15)", text: COLORS.primary },
};

export const ImBadge: React.FC<Props> = ({ text, variant, icon }) => {
  const c = BADGE_COLORS[variant];
  return (
    <span
      style={{
        backgroundColor: c.bg,
        color: c.text,
        fontSize: 12,
        fontWeight: 500,
        fontFamily,
        padding: "4px 12px",
        borderRadius: 12,
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      {icon && <MaterialIcon name={icon} size={14} color={c.text} />}
      {text}
    </span>
  );
};
