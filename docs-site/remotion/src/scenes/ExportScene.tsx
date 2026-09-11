import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { PageHeader, CONTENT_X, CONTENT_Y } from "../components/PageHeader";
import { ImCard } from "../components/ImCard";
import { ImInput } from "../components/ImInput";
import { ImStatCard } from "../components/ImStatCard";
import { ImButton } from "../components/ImButton";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { ImSeparator } from "../components/ImSeparator";
import { ImToggle } from "../components/ImToggle";
import { AnimatedCursor } from "../components/AnimatedCursor";

const FILENAME = "year_2025_memories.mp4";
const OUTPUT_DIR = "/home/user/Videos/Memories";

const CLICK_GENERATE = 118;

// Measured against a 1920x1080 still render: the middle of the full-width button.
const GENERATE_XY = { x: CONTENT_X + 520, y: CONTENT_Y + 460 };

const cursorSteps = [
  { frame: 100, ...GENERATE_XY },
  { frame: CLICK_GENERATE, ...GENERATE_XY, click: true },
];

type Props = { bassIntensity?: number };

export const ExportScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();

  const press = interpolate(
    frame,
    [CLICK_GENERATE, CLICK_GENERATE + 6],
    [1, 0.985],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={4} completedSteps={[1, 2]} />
        <div
          style={{
            flex: 1,
            padding: "20px 32px",
            overflow: "hidden",
            fontFamily,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <PageHeader title="Preview & Export" />

          <ImSectionHeader icon="summarize" title="Summary" />
          <div style={{ display: "flex", gap: 12 }}>
            <ImStatCard
              icon="movie"
              value="6"
              label="Clips"
              style={{ flex: "0 0 160px" }}
            />
            <ImStatCard
              icon="photo_library"
              value="3"
              label="Photo Pool"
              style={{ flex: "0 0 160px" }}
            />
            <ImStatCard
              icon="timer"
              value="5:12"
              label="Duration"
              style={{ flex: "0 0 160px" }}
            />
            <ImStatCard
              icon="video_file"
              value="MP4"
              label="Format"
              style={{ flex: "0 0 160px" }}
            />
          </div>

          <div style={{ marginTop: 16 }}>
            <ImSectionHeader icon="folder" title="Output" />
          </div>
          <ImCard>
            <ImInput
              label="Output filename"
              value={FILENAME}
              style={{ maxWidth: 520 }}
            />
            <div
              style={{
                fontSize: 14,
                color: COLORS.textSecondary,
                marginTop: 8,
              }}
            >
              Will be saved to: {OUTPUT_DIR}/{FILENAME}
            </div>
            <ImSeparator />
            <ImToggle label="Upload after generation" checked={false} />
          </ImCard>

          <div style={{ marginTop: 16 }}>
            <ImButton
              text="Generate Video"
              variant="primary"
              icon="movie"
              fullWidth
              style={{
                padding: "12px 20px",
                transform: `scale(${press})`,
              }}
            />
          </div>
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
