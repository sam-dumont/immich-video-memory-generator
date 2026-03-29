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
import { ImSeparator } from "../components/ImSeparator";

const PRESETS = [
  { icon: "📅", name: "Year in Review", desc: "Your year, one video" },
  { icon: "🌤️", name: "Season", desc: "Best moments of the season" },
  { icon: "👤", name: "Person Spotlight", desc: "A year through their eyes" },
  { icon: "👥", name: "Multi-Person", desc: "Together moments" },
];

type Props = { bassIntensity?: number };

export const ConfigScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Stagger reveals
  const connectionReveal = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 10,
  });
  const presetsReveal = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 25,
  });
  const selectionReveal = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 50,
  });
  // Person Spotlight gets selected at ~frame 70
  const selectedPreset = frame > 70 ? 2 : -1;
  const personReveal = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 85,
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={1} />
        <div style={{ flex: 1, padding: 24, overflow: "hidden", fontFamily }}>
          {/* Connection section */}
          <div
            style={{
              opacity: interpolate(connectionReveal, [0, 1], [0, 1]),
            }}
          >
            <ImSectionHeader icon="🔌" title="Immich Connection" />
            <ImCard>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 12,
                }}
              >
                <ImBadge text="✓ Connected" variant="success" />
                <span
                  style={{ fontSize: 12, color: COLORS.textSecondary }}
                >
                  as user@example.com
                </span>
              </div>
              <div style={{ display: "flex", gap: 12 }}>
                <ImInput
                  label="Server URL"
                  value="https://photos.example.com"
                  style={{ flex: 1 }}
                />
                <ImInput
                  label="API Key"
                  value="••••••••••••••••"
                  style={{ flex: 1 }}
                />
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                <ImButton text="Test Connection" variant="secondary" />
                <ImButton text="Save Config" variant="secondary" />
              </div>
            </ImCard>
          </div>

          <ImSeparator />

          {/* Presets */}
          <div
            style={{
              opacity: interpolate(presetsReveal, [0, 1], [0, 1]),
            }}
          >
            <ImSectionHeader icon="✨" title="Memory Type" />
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(4, 1fr)",
                gap: 10,
              }}
            >
              {PRESETS.map((p, i) => {
                const isSelected = i === selectedPreset;
                const selOpacity =
                  i === 2
                    ? interpolate(selectionReveal, [0, 1], [0, 1])
                    : 0;
                return (
                  <ImCard
                    key={p.name}
                    style={{
                      textAlign: "center",
                      padding: 14,
                      border: isSelected
                        ? `2px solid ${COLORS.primary}`
                        : `1px solid ${COLORS.border}`,
                      boxShadow: isSelected
                        ? `0 0 ${12 * selOpacity}px rgba(107, 143, 232, ${0.3 * selOpacity})`
                        : undefined,
                    }}
                  >
                    <div style={{ fontSize: 28, marginBottom: 6 }}>
                      {p.icon}
                    </div>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 600,
                        color: COLORS.text,
                      }}
                    >
                      {p.name}
                    </div>
                    <div
                      style={{
                        fontSize: 11,
                        color: COLORS.textSecondary,
                        marginTop: 2,
                      }}
                    >
                      {p.desc}
                    </div>
                  </ImCard>
                );
              })}
            </div>
          </div>

          {/* Person select */}
          <div
            style={{
              marginTop: 16,
              opacity: interpolate(personReveal, [0, 1], [0, 1]),
              transform: `translateY(${interpolate(personReveal, [0, 1], [10, 0])}px)`,
            }}
          >
            <div
              style={{
                display: "flex",
                gap: 12,
                alignItems: "flex-end",
              }}
            >
              <div style={{ flex: 1 }}>
                <span
                  style={{ fontSize: 12, color: COLORS.textSecondary }}
                >
                  Person
                </span>
                <div
                  style={{
                    backgroundColor: COLORS.bg,
                    border: `1px solid ${COLORS.primary}`,
                    borderRadius: 6,
                    padding: "8px 12px",
                    fontSize: 14,
                    color: COLORS.text,
                    marginTop: 4,
                  }}
                >
                  Alice
                </div>
              </div>
              <ImInput
                label="Target Duration"
                value="60s"
                style={{ width: 100 }}
              />
            </div>
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
