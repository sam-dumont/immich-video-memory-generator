import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

type Props = {
  /** The nav entry the page belongs to, as the app highlights it. */
  active?: string;
};

// `_NAVIGATION` in src/immich_memories/ui/app.py, in its order. It is plain
// navigation, not a wizard: no step numbers, no completion ticks.
const NAV = [
  { icon: "auto_awesome", label: "Memory" },
  { icon: "lightbulb", label: "Suggestions" },
  { icon: "history", label: "Runs" },
  { icon: "video_library", label: "Media pool" },
  { icon: "settings", label: "Settings" },
];

const THEME_ICONS = ["light_mode", "brightness_auto", "dark_mode"];

export const Sidebar: React.FC<Props> = ({ active = "Memory" }) => {
  return (
    <div
      style={{
        width: 200,
        backgroundColor: COLORS.bg,
        borderRight: `1px solid ${COLORS.border}`,
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        height: "100%",
      }}
    >
      {/* Branding */}
      <div
        style={{
          padding: "14px 16px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          borderBottom: `1px solid ${COLORS.border}`,
        }}
      >
        <MaterialIcon name="movie" size={22} color={COLORS.primary} />
        <span
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: COLORS.text,
            fontFamily,
          }}
        >
          Immich Memories
        </span>
      </div>

      {/* Main nav */}
      <div style={{ padding: "8px 0" }}>
        {NAV.map((step) => {
          const isActive = step.label === active;

          return (
            <div
              key={step.label}
              style={{
                padding: "9px 14px",
                display: "flex",
                alignItems: "center",
                gap: 10,
                backgroundColor: isActive
                  ? "rgba(107, 143, 232, 0.1)"
                  : "transparent",
                borderLeft: isActive
                  ? `3px solid ${COLORS.primary}`
                  : "3px solid transparent",
              }}
            >
              <MaterialIcon
                name={step.icon}
                size={20}
                color={isActive ? COLORS.primary : COLORS.textSecondary}
              />
              <span
                style={{
                  fontSize: 13,
                  fontFamily,
                  fontWeight: isActive ? 600 : 400,
                  color: isActive ? COLORS.primary : COLORS.textSecondary,
                  flex: 1,
                }}
              >
                {step.label}
              </span>
            </div>
          );
        })}
      </div>

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Theme toggle buttons */}
      <div
        style={{
          padding: "8px 14px 12px",
          display: "flex",
          gap: 4,
          borderTop: `1px solid ${COLORS.border}`,
        }}
      >
        {THEME_ICONS.map((icon, i) => {
          const isSelected = i === 2; // dark_mode selected
          return (
            <div
              key={icon}
              style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: "6px 0",
                borderRadius: 6,
                backgroundColor: isSelected
                  ? "rgba(107, 143, 232, 0.15)"
                  : "transparent",
              }}
            >
              <MaterialIcon
                name={icon}
                size={18}
                color={
                  isSelected ? COLORS.primary : COLORS.textSecondary
                }
              />
            </div>
          );
        })}
      </div>
    </div>
  );
};
