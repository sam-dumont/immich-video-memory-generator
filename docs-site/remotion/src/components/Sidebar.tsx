import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

/**
 * The app's left drawer, in the order `_NAVIGATION` lists it in
 * src/immich_memories/ui/app.py: five destinations, no step numbers and no
 * progress ticks. Config, People and Cache are Settings' own children and only
 * appear while a /settings/ page is open.
 */

export type NavPage =
  | "Memory"
  | "Suggestions"
  | "Runs"
  | "Media pool"
  | "Settings"
  /** The wizard's own pages (/step3, /step4) are not destinations: nothing lights up. */
  | "none";

type Props = { active: NavPage };

const NAVIGATION: { icon: string; label: Exclude<NavPage, "none"> }[] = [
  { icon: "auto_awesome", label: "Memory" },
  { icon: "lightbulb", label: "Suggestions" },
  { icon: "history", label: "Runs" },
  { icon: "video_library", label: "Media pool" },
  { icon: "settings", label: "Settings" },
];

const THEME_ICONS = ["light_mode", "brightness_auto", "dark_mode"];

export const Sidebar: React.FC<Props> = ({ active }) => (
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

    <div style={{ padding: "8px 0" }}>
      {NAVIGATION.map((item) => {
        const isActive = item.label === active;
        return (
          <div
            key={item.label}
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
              name={item.icon}
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
              {item.label}
            </span>
          </div>
        );
      })}
    </div>

    <div style={{ flex: 1 }} />

    <div
      style={{
        height: 1,
        backgroundColor: COLORS.border,
        margin: "0 14px 8px",
      }}
    />

    <div
      style={{
        padding: "0 14px 12px",
        display: "flex",
        gap: 4,
      }}
    >
      {THEME_ICONS.map((icon, i) => {
        const isSelected = i === 2; // dark_mode selected
        return (
          <div
            key={icon}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 30,
              height: 30,
              borderRadius: "50%",
              backgroundColor: isSelected
                ? "rgba(107, 143, 232, 0.15)"
                : "transparent",
            }}
          >
            <MaterialIcon
              name={icon}
              size={18}
              color={isSelected ? COLORS.primary : COLORS.textSecondary}
            />
          </div>
        );
      })}
    </div>
  </div>
);
