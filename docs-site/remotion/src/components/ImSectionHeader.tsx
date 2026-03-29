import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";

type Props = { icon: string; title: string };

export const ImSectionHeader: React.FC<Props> = ({ icon, title }) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      gap: 8,
      marginBottom: 12,
    }}
  >
    <span style={{ fontSize: 18, color: COLORS.primary }}>{icon}</span>
    <span
      style={{
        fontSize: 16,
        fontWeight: 600,
        color: COLORS.text,
        fontFamily,
      }}
    >
      {title}
    </span>
  </div>
);
