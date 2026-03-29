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
import { ImInput } from "../components/ImInput";
import { ImStatCard } from "../components/ImStatCard";
import { ImButton } from "../components/ImButton";
import { ImProgressBar } from "../components/ImProgressBar";
import { MaterialIcon } from "../components/MaterialIcon";

type Props = { bassIntensity?: number };

export const OptionsScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Staggered spring reveals for each section
  const s1 = spring({ frame, fps, config: { damping: 15, stiffness: 120 }, delay: 5 });
  const s2 = spring({ frame, fps, config: { damping: 15, stiffness: 120 }, delay: 25 });
  const s3 = spring({ frame, fps, config: { damping: 15, stiffness: 120 }, delay: 50 });
  const s4 = spring({ frame, fps, config: { damping: 15, stiffness: 120 }, delay: 70 });
  const s5 = spring({ frame, fps, config: { damping: 15, stiffness: 120 }, delay: 90 });

  const makeStyle = (s: number): React.CSSProperties => ({
    opacity: interpolate(s, [0, 1], [0, 1]),
    transform: `translateY(${interpolate(s, [0, 1], [12, 0])}px)`,
  });

  // Music generation progress (animates over time)
  const musicProgress = interpolate(frame, [30, 90], [0, 45], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={3} completedSteps={[1, 2]} />
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
              ...makeStyle(s1),
            }}
          >
            Generation Options
          </div>

          {/* Scrollable content area */}
          <div
            style={{
              flex: 1,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
              gap: 18,
            }}
          >
            {/* Section 1: Output Settings */}
            <div style={makeStyle(s1)}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 10,
                }}
              >
                <MaterialIcon name="tune" size={20} color={COLORS.primary} />
                <span
                  style={{
                    fontSize: 15,
                    fontWeight: 600,
                    color: COLORS.text,
                  }}
                >
                  Output Settings
                </span>
              </div>
              <ImCard>
                <div style={{ display: "flex", gap: 16 }}>
                  <ImSelect
                    label="Resolution"
                    value="Auto (match clips)"
                    style={{ flex: 1 }}
                  />
                  <ImSelect
                    label="Output Format"
                    value="MP4 (H.264)"
                    style={{ flex: 1 }}
                  />
                </div>
                {/* Advanced options collapsible */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    marginTop: 16,
                    padding: "8px 0",
                    cursor: "default",
                  }}
                >
                  <MaterialIcon
                    name="settings"
                    size={20}
                    color={COLORS.textSecondary}
                  />
                  <span
                    style={{
                      fontSize: 14,
                      color: COLORS.text,
                      fontFamily,
                      flex: 1,
                    }}
                  >
                    Advanced options
                  </span>
                  <MaterialIcon
                    name="keyboard_arrow_down"
                    size={22}
                    color={COLORS.textSecondary}
                  />
                </div>
              </ImCard>
            </div>

            {/* Section 2: Title */}
            <div style={makeStyle(s2)}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 10,
                }}
              >
                <MaterialIcon name="title" size={20} color={COLORS.primary} />
                <span
                  style={{
                    fontSize: 15,
                    fontWeight: 600,
                    color: COLORS.text,
                  }}
                >
                  Title
                </span>
              </div>
              <ImCard>
                <ImInput
                  label="Title"
                  value="Alice — 2025"
                  style={{ marginBottom: 12 }}
                />
                <ImInput
                  label="Subtitle"
                  value=""
                  placeholder="e.g. June – August 2025"
                  style={{ marginBottom: 12 }}
                />
                <div style={{ display: "flex", alignItems: "flex-end", gap: 12 }}>
                  <ImSelect label="Language" value="en" small style={{ width: 80 }} />
                  <ImButton
                    text="REGENERATE"
                    variant="ghost"
                    style={{ color: COLORS.primary, fontSize: 13, fontWeight: 600 }}
                  />
                </div>
              </ImCard>
            </div>

            {/* Section 3: Music */}
            <div style={makeStyle(s3)}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 10,
                }}
              >
                <MaterialIcon
                  name="music_note"
                  size={20}
                  color={COLORS.primary}
                />
                <span
                  style={{
                    fontSize: 15,
                    fontWeight: 600,
                    color: COLORS.text,
                  }}
                >
                  Music
                </span>
              </div>

              {/* Info banner */}
              <div
                style={{
                  backgroundColor: "rgba(107, 143, 232, 0.12)",
                  borderRadius: 6,
                  padding: "10px 14px",
                  marginBottom: 12,
                  fontSize: 13,
                  color: COLORS.primary,
                  fontFamily,
                }}
              >
                AI will generate music based on the mood of your video clips
              </div>

              {/* Music volume slider */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  marginBottom: 12,
                }}
              >
                <span
                  style={{
                    fontSize: 13,
                    color: COLORS.text,
                    fontFamily,
                    whiteSpace: "nowrap",
                  }}
                >
                  Music volume:
                </span>
                {/* Slider track */}
                <div
                  style={{
                    flex: 1,
                    height: 4,
                    backgroundColor: "rgba(255,255,255,0.1)",
                    borderRadius: 2,
                    position: "relative",
                  }}
                >
                  <div
                    style={{
                      width: "50%",
                      height: "100%",
                      backgroundColor: COLORS.primary,
                      borderRadius: 2,
                    }}
                  />
                  {/* Thumb */}
                  <div
                    style={{
                      position: "absolute",
                      left: "50%",
                      top: "50%",
                      width: 14,
                      height: 14,
                      borderRadius: "50%",
                      backgroundColor: COLORS.primary,
                      transform: "translate(-50%, -50%)",
                    }}
                  />
                </div>
                <span
                  style={{
                    fontSize: 13,
                    color: COLORS.textSecondary,
                    fontFamily,
                  }}
                >
                  50%
                </span>
              </div>

              {/* Progress bar for music generation */}
              {musicProgress > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <ImProgressBar
                    progress={musicProgress}
                    label={`generating (${Math.round(musicProgress)}%)`}
                  />
                </div>
              )}

              {/* Generate music button */}
              <ImButton
                text="GENERATE MUSIC"
                variant="primary"
                icon="music_note"
                style={{
                  backgroundColor: "#4ade80",
                  color: "#000",
                  marginBottom: 8,
                }}
              />
              <div
                style={{
                  fontSize: 12,
                  color: COLORS.textSecondary,
                  fontFamily,
                  marginBottom: 12,
                }}
              >
                Generate music now to preview before rendering your video
              </div>
              <ImSelect
                label="Background music"
                value="AI Generated"
                small
                style={{ maxWidth: 220 }}
              />
            </div>

            {/* Section 4: Summary */}
            <div style={makeStyle(s4)}>
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
                  style={{
                    fontSize: 15,
                    fontWeight: 600,
                    color: COLORS.text,
                  }}
                >
                  Summary
                </span>
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                <ImStatCard icon="movie" value="15" label="Clips" />
                <ImStatCard icon="timer" value="2:02" label="Duration" />
                <ImStatCard
                  icon="hd"
                  value="Auto (match clips)"
                  label="Resolution"
                />
                <ImStatCard icon="music_note" value="AI" label="Music" />
              </div>
            </div>

            {/* Bottom buttons */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                paddingTop: 4,
                ...makeStyle(s5),
              }}
            >
              <ImButton
                text="BACK TO CLIP REVIEW"
                variant="secondary"
                icon="arrow_back"
              />
              <ImButton
                text="NEXT: PREVIEW & EXPORT"
                variant="primary"
                icon="arrow_forward"
              />
            </div>
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
