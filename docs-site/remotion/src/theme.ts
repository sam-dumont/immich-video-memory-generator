export const COLORS = {
  bg: "#0c0c10",
  surface: "#131318",
  elevated: "#1c1c24",
  primary: "#7B9BF0",
  primaryGlow: "rgba(123, 155, 240, 0.15)",
  text: "#e4e4e4",
  textSecondary: "#d4d4d4",
  textMuted: "#a8a8b0",
  success: "#5eea9a",
  warning: "#fcc848",
  error: "#ff7878",
  analysis: "#c084fc",
  border: "rgba(255, 255, 255, 0.10)",
  borderLight: "rgba(255, 255, 255, 0.07)",
  titleBar: "#1e1e32",
  terminalTitleBar: "#161b22",
} as const;

export const FPS = 30;

// The whole demo, in frames. Scene lengths live in Composition.tsx's D map.
export const TOTAL_FRAMES = 1330;

// Music fades out over the last 5 seconds.
export const MUSIC_FADE_START = 1180;
