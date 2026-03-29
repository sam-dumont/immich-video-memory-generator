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
import { ImSelect } from "../components/ImSelect";
import { ImStatCard } from "../components/ImStatCard";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { ImSeparator } from "../components/ImSeparator";

type Props = { bassIntensity?: number };

export const OptionsScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const reveal = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 5,
  });
  const statsReveal = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 20,
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={3} completedSteps={[1, 2]} />
        <div
          style={{
            flex: 1,
            padding: 24,
            fontFamily,
            overflow: "hidden",
            opacity: interpolate(reveal, [0, 1], [0, 1]),
          }}
        >
          <ImSectionHeader icon="🎛️" title="Output Settings" />
          <ImCard>
            <div style={{ display: "flex", gap: 16 }}>
              <ImSelect
                label="Resolution"
                value="4K (2160p)"
                style={{ flex: 1 }}
              />
              <ImSelect
                label="Format"
                value="MP4 (H.264)"
                style={{ flex: 1 }}
              />
            </div>
            <div style={{ display: "flex", gap: 16, marginTop: 12 }}>
              <ImSelect
                label="Orientation"
                value="Landscape (16:9)"
                style={{ flex: 1 }}
              />
              <ImSelect
                label="Transition"
                value="Smart (fades & cuts)"
                style={{ flex: 1 }}
              />
            </div>
          </ImCard>

          <ImSeparator />

          <ImSectionHeader icon="🎵" title="Music" />
          <ImCard>
            <ImSelect
              label="Source"
              value="AI Generated (ACE-Step)"
            />
          </ImCard>

          <ImSeparator />

          {/* Summary stats */}
          <div
            style={{
              display: "flex",
              gap: 12,
              marginTop: 8,
              opacity: interpolate(statsReveal, [0, 1], [0, 1]),
              transform: `translateY(${interpolate(statsReveal, [0, 1], [10, 0])}px)`,
            }}
          >
            <ImStatCard icon="🎬" value="12" label="Clips" />
            <ImStatCard icon="⏱" value="1:04" label="Duration" />
            <ImStatCard icon="📐" value="4K" label="Resolution" />
            <ImStatCard icon="🎵" value="AI" label="Music" />
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
