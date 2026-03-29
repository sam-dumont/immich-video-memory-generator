export const COLORS = {
  bg: "#09090b",
  surface: "#111113",
  primary: "#6B8FE8",
  text: "#dbdbdb",
  textSecondary: "#a1a1aa",
  success: "#4ade80",
  warning: "#fbbf24",
  error: "#f87171",
  border: "rgba(255, 255, 255, 0.08)",
  titleBar: "#1c1c2e",
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
