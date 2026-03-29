import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { ImCard } from "../components/ImCard";
import { ImInput } from "../components/ImInput";
import { ImButton } from "../components/ImButton";
import { ImBadge } from "../components/ImBadge";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { ImToggle } from "../components/ImToggle";
import { ImSelect } from "../components/ImSelect";
import { MaterialIcon } from "../components/MaterialIcon";
import { AnimatedCursor } from "../components/AnimatedCursor";

const PRESETS = [
  {
    icon: "calendar_today",
    name: "Year in Review",
    desc: "Your year, one video",
  },
  {
    icon: "wb_sunny",
    name: "Season",
    desc: "Best moments of the season",
  },
  {
    icon: "person",
    name: "Person Spotlight",
    desc: "A year through their eyes",
  },
  {
    icon: "group",
    name: "Multi-Person",
    desc: "Together moments",
  },
  {
    icon: "event_note",
    name: "Monthly Highlights",
    desc: "One month, distilled",
  },
  {
    icon: "history",
    name: "On This Day",
    desc: "This day through the years",
  },
  {
    icon: "flight_takeoff",
    name: "Trip",
    desc: "Adventure recap, GPS-tagged",
  },
  {
    icon: "tune",
    name: "Custom",
    desc: "Full manual configuration",
  },
];

const PERSON_NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve"];

// Unhurried timing across full 120 frames:
// frame  0: page visible, cursor fades in
// frame 25: cursor arrives at Person Spotlight card
// frame 30: click → card selected with blue glow
// frame 40: cursor moves to Person select field, dropdown opens
// frame 55: cursor clicks "Alice" → dropdown closes, field shows "Alice"
// frame 80: cursor arrives at Next button
// frame 90: click Next button
const cursorSteps = [
  { frame: 25, x: 1230, y: 426, click: false },
  { frame: 30, x: 1230, y: 426, click: true },
  { frame: 40, x: 600, y: 530, click: true },
  { frame: 55, x: 600, y: 560, click: true },
  { frame: 80, x: 1060, y: 900, click: false },
  { frame: 90, x: 1060, y: 900, click: true },
];

type Props = { bassIntensity?: number };

export const ConfigScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Person Spotlight gets selected when cursor clicks at frame 30
  const selectedPreset = frame >= 30 ? 2 : -1;
  const selectionGlow = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 120 },
    delay: 30,
  });

  // Person select field appears after preset selection (slide down)
  const personFieldVisible = frame >= 30;
  const personFieldReveal = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 100 },
    delay: 32,
  });

  // Dropdown: visible between frame 40 and frame 55
  const dropdownOpacity = interpolate(
    frame,
    [39, 42, 53, 56],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // After clicking "Alice" at frame 55, show her name in the field
  const personSelected = frame >= 55;

  // Highlight "Alice" row when cursor is near (frames 48-55)
  const aliceHighlight = frame >= 48 && frame < 56;

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={1} />
        <div
          style={{
            flex: 1,
            padding: "16px 28px",
            overflow: "hidden",
            fontFamily,
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Page title */}
          <h1
            style={{
              fontSize: 22,
              fontWeight: 700,
              color: COLORS.text,
              fontFamily,
              margin: "0 0 14px 0",
            }}
          >
            Configuration
          </h1>

          {/* Scrollable content area */}
          <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
            {/* Section 1: Immich Connection */}
            <div>
              <ImSectionHeader icon="cloud" title="Immich Connection" />
              <ImCard>
                {/* Connected badge */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    marginBottom: 14,
                  }}
                >
                  <ImBadge
                    text="Connected as: user@example.com"
                    variant="success"
                    icon="check_circle"
                  />
                </div>

                {/* URL + API key inputs */}
                <div style={{ display: "flex", gap: 12 }}>
                  <ImInput
                    label="Immich Server URL"
                    value="https://photos.example.com"
                    style={{ flex: 1 }}
                  />
                  <ImInput
                    label="API Key"
                    value="••••••••••••••••"
                    style={{ flex: 1 }}
                  />
                </div>

                {/* Action buttons */}
                <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
                  <ImButton
                    text="Test Connection"
                    variant="secondary"
                    icon="wifi"
                  />
                  <ImButton
                    text="Save Config"
                    variant="secondary"
                    icon="save"
                  />
                </div>
              </ImCard>
            </div>

            {/* Section 2: Memory Type */}
            <div
              style={{
                marginTop: 16,
              }}
            >
              <ImSectionHeader icon="auto_awesome" title="Memory Type" />
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: 10,
                }}
              >
                {PRESETS.map((p, i) => {
                  const isSelected = i === selectedPreset;
                  const glowAmount = i === 2 ? selectionGlow : 0;

                  return (
                    <ImCard
                      key={p.name}
                      style={{
                        textAlign: "center",
                        padding: "14px 8px",
                        border: isSelected
                          ? `2px solid ${COLORS.primary}`
                          : `1px solid ${COLORS.border}`,
                        boxShadow: isSelected
                          ? `0 0 ${14 * glowAmount}px rgba(107, 143, 232, ${0.35 * glowAmount})`
                          : undefined,
                        backgroundColor: isSelected
                          ? "rgba(107, 143, 232, 0.06)"
                          : COLORS.surface,
                      }}
                    >
                      <MaterialIcon
                        name={p.icon}
                        size={28}
                        color={
                          isSelected
                            ? COLORS.primary
                            : COLORS.textSecondary
                        }
                        style={{ marginBottom: 6 }}
                      />
                      <div
                        style={{
                          fontSize: 12,
                          fontWeight: 600,
                          color: isSelected
                            ? COLORS.primary
                            : COLORS.text,
                          fontFamily,
                        }}
                      >
                        {p.name}
                      </div>
                      <div
                        style={{
                          fontSize: 10,
                          color: COLORS.textSecondary,
                          fontFamily,
                          marginTop: 3,
                        }}
                      >
                        {p.desc}
                      </div>
                    </ImCard>
                  );
                })}
              </div>

              {/* Person select field — appears after Person Spotlight is selected */}
              {personFieldVisible && (
                <div
                  style={{
                    marginTop: 12,
                    opacity: personFieldReveal,
                    transform: `translateY(${(1 - personFieldReveal) * 8}px)`,
                    position: "relative",
                  }}
                >
                  <ImSelect
                    label="Person"
                    value={personSelected ? "Alice" : "Select a person…"}
                  />

                  {/* Dropdown overlay — Quasar q-menu style */}
                  {dropdownOpacity > 0 && (
                    <div
                      style={{
                        position: "absolute",
                        top: "100%",
                        left: 0,
                        right: 0,
                        maxWidth: 280,
                        marginTop: 4,
                        backgroundColor: COLORS.elevated,
                        border: `1px solid ${COLORS.border}`,
                        borderRadius: 10,
                        opacity: dropdownOpacity,
                        transform: `translateY(${(1 - dropdownOpacity) * -6}px)`,
                        zIndex: 100,
                        overflow: "hidden",
                        boxShadow:
                          "0 8px 24px rgba(0,0,0,0.4), 0 2px 8px rgba(0,0,0,0.3)",
                      }}
                    >
                      {PERSON_NAMES.map((name) => {
                        const isAlice = name === "Alice";
                        const highlighted = isAlice && aliceHighlight;
                        return (
                          <div
                            key={name}
                            style={{
                              padding: "8px 16px",
                              fontSize: 14,
                              fontFamily,
                              color: highlighted
                                ? COLORS.text
                                : COLORS.text,
                              backgroundColor: highlighted
                                ? "rgba(107, 143, 232, 0.15)"
                                : "transparent",
                              cursor: "pointer",
                              borderLeft: highlighted
                                ? `3px solid ${COLORS.primary}`
                                : "3px solid transparent",
                            }}
                          >
                            {name}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Section 3: Options */}
            <div
              style={{
                marginTop: 16,
              }}
            >
              <ImSectionHeader icon="settings" title="Options" />
              <ImCard>
                <ImToggle
                  label="Include Live Photos"
                  description="Short clips from Live Photos, burst-merged when consecutive"
                  checked={true}
                />
                <div
                  style={{
                    height: 1,
                    backgroundColor: COLORS.border,
                    margin: "4px 0",
                  }}
                />
                <ImToggle
                  label="Include Photos"
                  description="Include photos as animated clips"
                  checked={true}
                />
                <div
                  style={{
                    height: 1,
                    backgroundColor: COLORS.border,
                    margin: "4px 0",
                  }}
                />
                <ImSelect
                  label="Analysis Depth"
                  value='Fast (LLM top clips only)'
                  style={{ marginTop: 4 }}
                />
                <div
                  style={{
                    height: 1,
                    backgroundColor: COLORS.border,
                    margin: "8px 0 4px",
                  }}
                />
                <ImToggle
                  label="Prioritize Favorites"
                  description="Rank favorited clips higher in selection"
                  checked={true}
                />
              </ImCard>
            </div>

            {/* Bottom: Next button */}
            <div
              style={{
                marginTop: 16,
              }}
            >
              <ImButton
                text="Next: Review Clips"
                variant="primary"
                icon="arrow_forward"
                fullWidth
              />
            </div>
          </div>
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
