import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";

type Props = {
  text: string;
  variant: "success" | "warning" | "error" | "info";
};

const BADGE_COLORS = {
  success: { bg: "rgba(74, 222, 128, 0.15)", text: COLORS.success },
  warning: { bg: "rgba(251, 191, 36, 0.15)", text: COLORS.warning },
  error: { bg: "rgba(248, 113, 113, 0.15)", text: COLORS.error },
  info: { bg: "rgba(107, 143, 232, 0.15)", text: COLORS.primary },
};

export const ImBadge: React.FC<Props> = ({ text, variant }) => {
  const c = BADGE_COLORS[variant];
  return (
    <span
      style={{
        backgroundColor: c.bg,
        color: c.text,
        fontSize: 12,
        fontWeight: 500,
        fontFamily,
        padding: "3px 10px",
        borderRadius: 12,
        display: "inline-block",
      }}
    >
      {text}
    </span>
  );
};
