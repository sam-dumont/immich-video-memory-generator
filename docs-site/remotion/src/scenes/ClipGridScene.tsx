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
import { ImBadge } from "../components/ImBadge";

const CLIPS = Array.from({ length: 12 }, (_, i) => ({
  thumb: `stock/thumb-${(i % 12) + 1}.jpg`,
  duration: [
    "0:06",
    "0:12",
    "0:04",
    "0:15",
    "0:08",
    "0:03",
    "0:11",
    "0:07",
    "0:09",
    "0:05",
    "0:14",
    "0:10",
  ][i],
  score: [0.82, 0.71, 0.65, 0.93, 0.58, 0.77, 0.84, 0.69, 0.91, 0.73, 0.88, 0.62][i],
  favorite: [true, false, false, true, false, true, false, false, true, false, true, false][i],
}));

type Props = { bassIntensity?: number };

export const ClipGridScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Scroll: content moves up to reveal more rows
  const scrollY = interpolate(frame, [40, 160], [0, 180], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.cubic),
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame
        bassIntensity={bassIntensity}
        zoom={{
          targetX: 0.55,
          targetY: 0.5,
          scale: 1.35,
          startFrame: 60,
          durationFrames: 70,
        }}
      >
        <Sidebar activeStep={2} completedSteps={[1]} />
        <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          <div
            style={{
              transform: `translateY(-${scrollY}px)`,
              padding: 20,
            }}
          >
            {/* Stats bar */}
            <div
              style={{
                display: "flex",
                gap: 16,
                alignItems: "center",
                marginBottom: 16,
                fontFamily,
                fontSize: 13,
                color: COLORS.textSecondary,
              }}
            >
              <span>Aug 2024</span>
              <span style={{ fontWeight: 600, color: COLORS.text }}>
                31 clips
              </span>
              <span>12:45 total</span>
              <ImBadge text="4 HDR" variant="info" />
              <ImBadge text="7 ★" variant="warning" />
            </div>

            {/* Clip grid */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(4, 1fr)",
                gap: 10,
              }}
            >
              {CLIPS.map((clip, i) => {
                // Stagger card entrance
                const cardEntry = spring({
                  frame,
                  fps,
                  config: { damping: 20, stiffness: 200 },
                  delay: 5 + i * 3,
                });
                const cardOpacity = interpolate(
                  cardEntry,
                  [0, 1],
                  [0, 1],
                );
                const cardY = interpolate(cardEntry, [0, 1], [15, 0]);

                return (
                  <div
                    key={i}
                    style={{
                      backgroundColor: COLORS.surface,
                      border: `1px solid ${COLORS.border}`,
                      borderRadius: 8,
                      overflow: "hidden",
                      opacity: cardOpacity,
                      transform: `translateY(${cardY}px)`,
                    }}
                  >
                    {/* Thumbnail */}
                    <div
                      style={{
                        position: "relative",
                        height: 120,
                      }}
                    >
                      <Img
                        src={staticFile(clip.thumb)}
                        style={{
                          width: "100%",
                          height: "100%",
                          objectFit: "cover",
                        }}
                      />
                      {/* Duration badge */}
                      <span
                        style={{
                          position: "absolute",
                          bottom: 4,
                          right: 4,
                          backgroundColor: "rgba(0,0,0,0.75)",
                          color: "white",
                          fontSize: 11,
                          padding: "2px 6px",
                          borderRadius: 4,
                          fontFamily,
                        }}
                      >
                        {clip.duration}
                      </span>
                      {clip.favorite && (
                        <span
                          style={{
                            position: "absolute",
                            top: 4,
                            right: 4,
                            fontSize: 14,
                          }}
                        >
                          ⭐
                        </span>
                      )}
                    </div>
                    {/* Score */}
                    <div
                      style={{
                        padding: "6px 8px",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <span
                        style={{
                          fontSize: 11,
                          color: COLORS.textSecondary,
                          fontFamily,
                        }}
                      >
                        Score
                      </span>
                      <span
                        style={{
                          fontSize: 12,
                          fontWeight: 600,
                          color:
                            clip.score > 0.8
                              ? COLORS.success
                              : COLORS.text,
                          fontFamily,
                        }}
                      >
                        {clip.score.toFixed(2)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
