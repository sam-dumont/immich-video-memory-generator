import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

/**
 * Screen coordinates of the padded content origin inside WindowFrame, for
 * scenes that place AnimatedCursor steps over their own layout: the 1600x900
 * window is centred in 1920x1080, its title bar is 36px, the sidebar 200px,
 * and every page pane uses "20px 32px" padding.
 */
export const CONTENT_X = 160 + 200 + 32;
export const CONTENT_Y = 90 + 36 + 20;

/** The page header the real app draws: drawer toggle, then the page's name. */
export const PageHeader: React.FC<{ title: string }> = ({ title }) => (
  <div
    style={{
      height: 30,
      display: "flex",
      alignItems: "center",
      gap: 14,
      marginBottom: 16,
    }}
  >
    <MaterialIcon name="menu" size={22} color={COLORS.primary} />
    <span
      style={{
        fontSize: 22,
        fontWeight: 700,
        color: COLORS.text,
        fontFamily,
      }}
    >
      {title}
    </span>
  </div>
);
