import React from "react";
import { COLORS } from "../theme";

type Props = { children: React.ReactNode; style?: React.CSSProperties };

export const ImCard: React.FC<Props> = ({ children, style }) => (
  <div
    style={{
      backgroundColor: COLORS.surface,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 10,
      padding: 20,
      ...style,
    }}
  >
    {children}
  </div>
);
