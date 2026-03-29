import React from "react";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { MaterialIcon } from "./MaterialIcon";

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
    <MaterialIcon name={icon} size={20} color={COLORS.primary} />
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
