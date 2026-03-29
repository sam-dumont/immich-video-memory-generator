import React from "react";
import {
  AbsoluteFill,
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

const cursorSteps = [
  { frame: 25, x: 850, y: 440, click: false },
  { frame: 35, x: 850, y: 440, click: true },
  { frame: 55, x: 700, y: 720, click: false },
  { frame: 65, x: 700, y: 720, click: true },
];

type Props = { bassIntensity?: number };

export const ConfigScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Person Spotlight gets selected when cursor clicks at frame 35
  const selectedPreset = frame >= 35 ? 2 : -1;
  const selectionGlow = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 120 },
    delay: 35,
  });

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
          <div style={{ flex: 1, overflow: "hidden" }}>
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
