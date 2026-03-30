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
  border: "rgba(255, 255, 255, 0.10)",
  borderLight: "rgba(255, 255, 255, 0.07)",
  titleBar: "#1e1e32",
  terminalTitleBar: "#161b22",
} as const;

export const FPS = 30;

// Beat map: bar number → frame (95.7 BPM, ~2.5s/bar)
export const BEATS = {
  bar1: 0,
  bar3: 158, // 5.3s — Config
  bar5: 310, // 10.3s — Grid
  bar8: 538, // 17.9s — Analysis
  bar10: 689, // 23.0s — Options
  bar11: 765, // 25.5s — Generating
  bar13: 916, // 30.5s — Complete
  bar14: 1006, // 33.5s — CLI
  bar15: 1156, // 38.5s — Outro
  end: 1350, // 45.0s — Music fade
  musicEnd: 1500, // 50.0s
} as const;
