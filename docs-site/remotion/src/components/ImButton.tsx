import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

type Props = {
  text: string;
  variant?: "primary" | "secondary" | "ghost";
  icon?: string;
  fullWidth?: boolean;
  disabled?: boolean;
  style?: React.CSSProperties;
};

export const ImButton: React.FC<Props> = ({
  text,
  variant = "primary",
  icon,
  fullWidth,
  disabled,
  style,
}) => {
  const base: React.CSSProperties = {
    fontFamily,
    fontSize: 13,
    fontWeight: 600,
    letterSpacing: 0.5,
    padding: "10px 20px",
    borderRadius: 8,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    width: fullWidth ? "100%" : undefined,
    opacity: disabled ? 0.5 : 1,
    cursor: "default",
    textTransform: "uppercase",
  };

  const variants: Record<string, React.CSSProperties> = {
    primary: {
      backgroundColor: COLORS.primary,
      color: "white",
      border: "none",
    },
    secondary: {
      backgroundColor: "transparent",
      color: COLORS.primary,
      border: `1px solid ${COLORS.primary}`,
    },
    ghost: {
      backgroundColor: "transparent",
      color: COLORS.textSecondary,
      border: "none",
    },
  };

  return (
    <div style={{ ...base, ...variants[variant], ...style }}>
      {icon && (
        <MaterialIcon
          name={icon}
          size={18}
          color={variant === "primary" ? "white" : COLORS.primary}
        />
      )}
      {text}
    </div>
  );
};
