import React from "react";

type Props = {
  name: string;
  size?: number;
  color?: string;
  style?: React.CSSProperties;
};

export const MaterialIcon: React.FC<Props> = ({
  name,
  size = 24,
  color,
  style,
}) => (
  <span
    style={{
      fontFamily: "Material Icons",
      fontWeight: "normal",
      fontStyle: "normal",
      fontSize: size,
      lineHeight: 1,
      letterSpacing: "normal",
      textTransform: "none",
      display: "inline-block",
      whiteSpace: "nowrap",
      wordWrap: "normal",
      direction: "ltr",
      WebkitFontSmoothing: "antialiased",
      color,
      ...style,
    }}
  >
    {name}
  </span>
);
