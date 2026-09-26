export const COLORS = {
  bg: "#09090b",
  surface: "#111113",
  elevated: "#1a1a1e",
  primary: "#6B8FE8",
  primaryGlow: "rgba(123, 155, 240, 0.15)",
  text: "#dbdbdb",
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
export const TOTAL_FRAMES = 1436;

// Music fades out over the last 5 seconds.
export const MUSIC_FADE_START = 1286;
