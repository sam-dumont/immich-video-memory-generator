import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

type Props = {
  label: string;
  description?: string;
  checked?: boolean;
  icon?: string;
};

export const ImToggle: React.FC<Props> = ({
  label,
  description,
  checked = false,
  icon,
}) => (
  <div
    style={{
      display: "flex",
      alignItems: "flex-start",
      gap: 12,
      padding: "8px 0",
    }}
  >
    {/* Toggle track */}
    <div
      style={{
        width: 36,
        height: 20,
        borderRadius: 10,
        backgroundColor: checked ? COLORS.primary : "rgba(255,255,255,0.15)",
        position: "relative",
        flexShrink: 0,
        marginTop: 2,
      }}
    >
      {/* Toggle thumb */}
      <div
        style={{
          width: 16,
          height: 16,
          borderRadius: "50%",
          backgroundColor: "white",
          position: "absolute",
          top: 2,
          left: checked ? 18 : 2,
        }}
      />
    </div>

    {/* Label + description */}
    <div style={{ flex: 1 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        {icon && (
          <MaterialIcon name={icon} size={16} color={COLORS.primary} />
        )}
        <span
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: COLORS.text,
            fontFamily,
          }}
        >
          {label}
        </span>
      </div>
      {description && (
        <span
          style={{
            fontSize: 11,
            color: COLORS.textSecondary,
            fontFamily,
            marginTop: 2,
            display: "block",
          }}
        >
          {description}
        </span>
      )}
    </div>
  </div>
);
