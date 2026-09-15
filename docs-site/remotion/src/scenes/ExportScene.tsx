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
import { PageHeader, CONTENT_X, CONTENT_Y } from "../components/PageHeader";
import { ImCard } from "../components/ImCard";
import { ImInput } from "../components/ImInput";
import { ImStatCard } from "../components/ImStatCard";
import { ImButton } from "../components/ImButton";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { ImSeparator } from "../components/ImSeparator";
import { ImToggle } from "../components/ImToggle";
import { MaterialIcon } from "../components/MaterialIcon";
import { AnimatedCursor } from "../components/AnimatedCursor";

import { RECUT_SHOTS, RECUT_FILM_SECONDS, POOL_TOTAL, POOL_VIDEOS } from "../fixture";

const PHOTOS = POOL_TOTAL - POOL_VIDEOS;
const DURATION = `≈${Math.floor(RECUT_FILM_SECONDS / 60)}:${String(Math.floor(RECUT_FILM_SECONDS % 60)).padStart(2, "0")}`;

const STATS = [
  { icon: "movie", value: String(RECUT_SHOTS.length), label: "Clips" },
  { icon: "photo_library", value: String(PHOTOS), label: "Photo Pool" },
  { icon: "timer", value: DURATION, label: "Film length" },
  { icon: "video_file", value: "MP4", label: "Format" },
];

// The name the filename builder gives the fixture's June, and the directory the
// screenshots show: a plain home path, not the temp root a hermetic run uses.
const FILENAME = "everyone_june_2024_memories.mp4";
const OUTPUT_DIR = "/home/user/Videos/Memories";

const CLICK_GENERATE = 78;

// Measured against a 1920x1080 still render: the middle of the full-width button.
const GENERATE_XY = { x: CONTENT_X + 520, y: CONTENT_Y + 510 };

// The cursor is hidden until 15 frames before its first step, so a late first
// step is a still page. This one arrives while the cards are still landing.
const cursorSteps = [
  { frame: 58, ...GENERATE_XY },
  { frame: CLICK_GENERATE, ...GENERATE_XY, click: true },
];

type Props = { bassIntensity?: number };

export const ExportScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // The page assembles rather than appearing finished: without this the scene
  // was a still frame until the cursor showed up.
  const reveal = (delay: number) =>
    spring({ frame, fps, config: { damping: 20, stiffness: 140 }, delay });

  const press = interpolate(
    frame,
    [CLICK_GENERATE, CLICK_GENERATE + 6],
    [1, 0.985],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar active="none" />
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
            {STATS.map((stat, i) => {
              const entry = reveal(4 + i * 6);
              return (
                <ImStatCard
                  key={stat.label}
                  icon={stat.icon}
                  value={stat.value}
                  label={stat.label}
                  style={{
                    flex: "0 0 160px",
                    opacity: entry,
                    transform: `translateY(${(1 - entry) * 14}px)`,
                  }}
                />
              );
            })}
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              marginTop: 14,
              padding: "10px 0",
              opacity: reveal(26),
            }}
          >
            <MaterialIcon name="photo_library" size={20} color={COLORS.textSecondary} />
            <span style={{ fontSize: 14, color: COLORS.text, flex: 1 }}>
              {PHOTOS} Photos Available (auto-selected at generation)
            </span>
            <MaterialIcon
              name="keyboard_arrow_down"
              size={22}
              color={COLORS.textSecondary}
            />
          </div>

          <div style={{ marginTop: 10, opacity: reveal(30) }}>
            <ImSectionHeader icon="folder" title="Output" />
          </div>
          <ImCard
            style={{
              opacity: reveal(34),
              transform: `translateY(${(1 - reveal(34)) * 12}px)`,
            }}
          >
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

          <div style={{ marginTop: 16, opacity: reveal(44) }}>
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
