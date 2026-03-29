import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { ImCard } from "../components/ImCard";
import { ImInput } from "../components/ImInput";
import { ImStatCard } from "../components/ImStatCard";
import { ImButton } from "../components/ImButton";
import { ImToggle } from "../components/ImToggle";
import { MaterialIcon } from "../components/MaterialIcon";
import { AnimatedCursor } from "../components/AnimatedCursor";

const STATUS_PHASES = [
  { at: 0, label: "Downloading clips..." },
  { at: 30, label: "Downloading: IMG_3959.HEIC" },
  { at: 50, label: "Assembling video..." },
  { at: 80, label: "Encoding..." },
];

type Props = { bassIntensity?: number };

export const GeneratingScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Phase 1: Initial state (frames 0-15)
  // Phase 2: Generating (frames 15+, triggered by cursor click)
  const isGenerating = frame >= 15;

  // Progress bar animation (starts at frame 15)
  const genProgress = interpolate(
    frame,
    [15, 50, 90, 140, 200],
    [0, 15, 45, 75, 100],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.quad) },
  );

  const currentPhase =
    [...STATUS_PHASES].reverse().find((p) => genProgress >= p.at) ??
    STATUS_PHASES[0];

  // Progress bar reveal spring
  const progressReveal = spring({
    frame: Math.max(0, frame - 15),
    fps,
    config: { damping: 15, stiffness: 120 },
    delay: 5,
  });

  // Single photo with Ken Burns effect (slow zoom + pan)
  const kenBurnsScale = interpolate(frame, [50, 200], [1.0, 1.15], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const kenBurnsX = interpolate(frame, [50, 200], [0, -15], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const kenBurnsY = interpolate(frame, [50, 200], [0, -8], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Progressive reveal (simulates frame-by-frame rendering)
  const renderOpacity = interpolate(frame, [50, 70], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={4} completedSteps={[1, 2, 3]} />
        <div
          style={{
            flex: 1,
            padding: "20px 28px",
            fontFamily,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {/* Page title */}
          <div
            style={{
              fontSize: 20,
              fontWeight: 600,
              color: COLORS.text,
              marginBottom: 18,
            }}
          >
            Preview & Export
          </div>

          <div
            style={{
              flex: 1,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
              gap: 18,
            }}
          >
            {/* Section: Summary */}
            <div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 10,
                }}
              >
                <MaterialIcon
                  name="summarize"
                  size={20}
                  color={COLORS.primary}
                />
                <span
                  style={{ fontSize: 15, fontWeight: 600, color: COLORS.text }}
                >
                  Summary
                </span>
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                <ImStatCard icon="movie" value="15" label="Clips" />
                <ImStatCard icon="timer" value="2:02" label="Duration" />
                <ImStatCard
                  icon="video_file"
                  value="MP4 (H.264)"
                  label="Format"
                />
              </div>
            </div>

            {/* Section: Output */}
            <div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 10,
                }}
              >
                <MaterialIcon
                  name="folder"
                  size={20}
                  color={COLORS.primary}
                />
                <span
                  style={{ fontSize: 15, fontWeight: 600, color: COLORS.text }}
                >
                  Output
                </span>
              </div>
              <ImCard>
                <ImInput
                  label="Output filename"
                  value="alice_2025_memories.mp4"
                />
                <div
                  style={{
                    fontSize: 12,
                    color: COLORS.textSecondary,
                    fontFamily,
                    marginTop: 8,
                  }}
                >
                  Will be saved to: ~/Videos/Memories/alice_2025_memories.mp4
                </div>
              </ImCard>
            </div>

            {/* Section: Upload to Immich */}
            <div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 10,
                }}
              >
                <MaterialIcon
                  name="cloud_upload"
                  size={20}
                  color={COLORS.primary}
                />
                <span
                  style={{ fontSize: 15, fontWeight: 600, color: COLORS.text }}
                >
                  Upload to Immich
                </span>
              </div>
              <ImCard>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    marginBottom: 8,
                  }}
                >
                  <MaterialIcon
                    name="cloud_upload"
                    size={22}
                    color={COLORS.primary}
                  />
                  <span
                    style={{
                      fontSize: 14,
                      fontWeight: 600,
                      color: COLORS.text,
                      fontFamily,
                    }}
                  >
                    Upload to Immich
                  </span>
                </div>
                <ImToggle label="Upload after generation" checked={false} />
              </ImCard>
            </div>

            {/* Generate button OR progress */}
            <div>
              {!isGenerating ? (
                <ImButton
                  text="GENERATE VIDEO"
                  variant="primary"
                  icon="movie"
                  fullWidth
                  style={{ fontSize: 14, padding: "14px 20px" }}
                />
              ) : (
                <div
                  style={{
                    opacity: interpolate(progressReveal, [0, 1], [0, 1]),
                    transform: `translateY(${interpolate(progressReveal, [0, 1], [8, 0])}px)`,
                  }}
                >
                  {/* Progress bar */}
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
                  <div
                    style={{
                      fontSize: 13,
                      color: COLORS.textSecondary,
                      fontFamily,
                    }}
                  >
                    {currentPhase.label}
                  </div>
                </div>
              )}
            </div>

            {/* Frame preview (appears during generation — single photo with Ken Burns) */}
            {frame >= 50 && (
              <div
                style={{
                  width: "100%",
                  height: 350,
                  borderRadius: 10,
                  overflow: "hidden",
                  opacity: renderOpacity,
                }}
              >
                <Img
                  src={staticFile("stock/thumb-21.jpg")}
                  style={{
                    width: "110%",
                    height: "110%",
                    objectFit: "cover",
                    transform: `scale(${kenBurnsScale}) translate(${kenBurnsX}px, ${kenBurnsY}px)`,
                  }}
                />
              </div>
            )}

            {/* Spacer */}
            <div style={{ flex: 1 }} />

            {/* Bottom buttons */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <ImButton
                text="BACK TO GENERATION OPTIONS"
                variant="secondary"
                icon="arrow_back"
              />
              {!isGenerating && (
                <ImButton
                  text="START NEW PROJECT"
                  variant="ghost"
                  icon="refresh"
                />
              )}
              {isGenerating && (
                <ImButton
                  text="CANCEL"
                  variant="ghost"
                  icon="cancel"
                  style={{ color: COLORS.error }}
                />
              )}
            </div>
          </div>
        </div>

        {/* Cursor: moves to GENERATE VIDEO button, then clicks */}
        <AnimatedCursor
          steps={[
            { frame: 8, x: 700, y: 650 },
            { frame: 15, x: 700, y: 650, click: true },
          ]}
        />
      </WindowFrame>
    </AbsoluteFill>
  );
};
