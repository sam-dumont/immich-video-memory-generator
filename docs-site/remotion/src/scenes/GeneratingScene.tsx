import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  OffthreadVideo,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { PageHeader } from "../components/PageHeader";
import { ImCard } from "../components/ImCard";
import { ImInput } from "../components/ImInput";
import { ImStatCard } from "../components/ImStatCard";
import { ImButton } from "../components/ImButton";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { ImSeparator } from "../components/ImSeparator";
import { ImToggle } from "../components/ImToggle";

import { CUT_COUNT, CUT_SECONDS, POOL_TOTAL, POOL_VIDEOS } from "../fixture";

const PHOTOS = POOL_TOTAL - POOL_VIDEOS;
const DURATION = `${Math.floor(CUT_SECONDS / 60)}:${String(CUT_SECONDS % 60).padStart(2, "0")}`;
const FILENAME = "everyone_june_2024_memories.mp4";
const OUTPUT_DIR = "/home/user/Videos/Memories";

// Percent-of-progress the status line switches at.
const STATUS_PHASES = [
  { at: 0, label: "Downloading clips..." },
  { at: 30, label: "Rendering photos..." },
  { at: 50, label: "Assembling video..." },
  { at: 80, label: "Encoding..." },
];

const PREVIEW_FROM = 35;

type Props = { bassIntensity?: number };

/** The Export page mid-run: the Generate button has been replaced by progress. */
export const GeneratingScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const genProgress = interpolate(frame, [0, 35, 75, 125, 160], [0, 15, 45, 75, 100], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.quad),
  });

  const currentPhase =
    [...STATUS_PHASES].reverse().find((p) => genProgress >= p.at) ??
    STATUS_PHASES[0];

  const progressReveal = spring({
    frame,
    fps,
    config: { damping: 15, stiffness: 120 },
  });

  const previewOpacity = interpolate(
    frame,
    [PREVIEW_FROM, PREVIEW_FROM + 18],
    [0, 1],
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
            fontFamily,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <PageHeader title="Preview & Export" />

          <ImSectionHeader icon="summarize" title="Summary" />
          <div style={{ display: "flex", gap: 12 }}>
            <ImStatCard
              icon="movie"
              value={String(CUT_COUNT)}
              label="Clips"
              style={{ flex: "0 0 160px" }}
            />
            <ImStatCard
              icon="photo_library"
              value={String(PHOTOS)}
              label="Photo Pool"
              style={{ flex: "0 0 160px" }}
            />
            <ImStatCard
              icon="timer"
              value={DURATION}
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

          {/* The Generate button's place, now taken by the run */}
          <div
            style={{
              marginTop: 16,
              opacity: interpolate(progressReveal, [0, 1], [0, 1]),
              transform: `translateY(${interpolate(progressReveal, [0, 1], [8, 0])}px)`,
            }}
          >
            <div
              style={{
                width: "100%",
                height: 6,
                backgroundColor: "rgba(255,255,255,0.06)",
                borderRadius: 3,
                overflow: "hidden",
                marginBottom: 8,
              }}
            >
              <div
                style={{
                  width: `${Math.min(genProgress, 100)}%`,
                  height: "100%",
                  backgroundColor: COLORS.primary,
                  borderRadius: 3,
                }}
              />
            </div>
            <div style={{ fontSize: 14, color: COLORS.textSecondary }}>
              {currentPhase.label}
            </div>
          </div>

          {/* The output advancing while it is written */}
          {frame >= PREVIEW_FROM && (
            <div
              style={{
                marginTop: 14,
                width: "100%",
                maxHeight: 290,
                aspectRatio: "16 / 9",
                borderRadius: 10,
                overflow: "hidden",
                opacity: previewOpacity,
                backgroundColor: "#000",
                alignSelf: "center",
              }}
            >
              <OffthreadVideo
                src={staticFile("output-preview.mp4")}
                style={{ width: "100%", height: "100%", objectFit: "contain" }}
                playbackRate={3}
                volume={0}
                startFrom={0}
              />
            </div>
          )}

          <div style={{ flex: 1 }} />

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <ImButton
              text="Back to Generation Options"
              variant="secondary"
              icon="arrow_back"
            />
            <ImButton
              text="Cancel"
              variant="ghost"
              icon="cancel"
              style={{ color: COLORS.error }}
            />
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
