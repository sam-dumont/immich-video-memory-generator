import React from "react";
import {
  AbsoluteFill,
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
import { ImBadge } from "../components/ImBadge";

type Props = { bassIntensity?: number };

const LLM_TEXT =
  "A family playing on the beach at sunset, children building sandcastles";

export const AnalysisScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // LLM text appears all at once after analysis completes (matches real behavior)
  const textReveal = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 40,
  });

  // Badges spring in shortly after text
  const emotionBadge = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 200 },
    delay: 55,
  });
  const scoreBadge = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 200 },
    delay: 65,
  });

  // Face detection box
  const faceBoxOpacity = interpolate(frame, [50, 60], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Phase label
  const phaseOpacity = interpolate(frame, [5, 15], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame
        bassIntensity={bassIntensity}
        zoom={{
          targetX: 0.55,
          targetY: 0.4,
          scale: 1.3,
          startFrame: 20,
          durationFrames: 50,
        }}
      >
        <Sidebar activeStep={2} completedSteps={[1]} />
        <div
          style={{
            flex: 1,
            padding: 24,
            fontFamily,
            overflow: "hidden",
          }}
        >
          {/* Analysis phase indicator */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 16,
              opacity: phaseOpacity,
            }}
          >
            <span style={{ fontSize: 14, color: COLORS.primary }}>⟳</span>
            <span
              style={{ fontSize: 13, color: COLORS.textSecondary }}
            >
              Analyzing: content analysis (LLM)
            </span>
          </div>

          {/* Expanded clip card with analysis */}
          <ImCard style={{ padding: 16 }}>
            <div style={{ display: "flex", gap: 16 }}>
              {/* Thumbnail with face detection */}
              <div
                style={{
                  position: "relative",
                  width: 280,
                  height: 200,
                  borderRadius: 8,
                  overflow: "hidden",
                  flexShrink: 0,
                }}
              >
                <Img
                  src={staticFile("stock/thumb-3.jpg")}
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                  }}
                />
                {/* Face detection boxes */}
                <div
                  style={{
                    position: "absolute",
                    top: "20%",
                    left: "30%",
                    width: "25%",
                    height: "40%",
                    border: `2px dashed ${COLORS.success}`,
                    borderRadius: 4,
                    opacity: faceBoxOpacity,
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    top: "55%",
                    left: "55%",
                    width: "20%",
                    height: "35%",
                    border: `2px dashed ${COLORS.success}`,
                    borderRadius: 4,
                    opacity: faceBoxOpacity,
                  }}
                />
              </div>

              {/* Analysis results */}
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 600,
                    color: COLORS.text,
                    marginBottom: 8,
                  }}
                >
                  Content Analysis
                </div>

                {/* LLM description typing */}
                <div
                  style={{
                    fontSize: 13,
                    color: COLORS.text,
                    lineHeight: 1.6,
                    minHeight: 40,
                    padding: 10,
                    backgroundColor: "rgba(107, 143, 232, 0.05)",
                    borderRadius: 6,
                    borderLeft: `3px solid ${COLORS.primary}`,
                    opacity: interpolate(textReveal, [0, 1], [0, 1]),
                    transform: `translateY(${interpolate(textReveal, [0, 1], [6, 0])}px)`,
                  }}
                >
                  {LLM_TEXT}
                </div>

                {/* Badges */}
                <div
                  style={{
                    display: "flex",
                    gap: 8,
                    marginTop: 12,
                  }}
                >
                  <div
                    style={{
                      transform: `scale(${interpolate(emotionBadge, [0, 1], [0.5, 1])})`,
                      opacity: interpolate(
                        emotionBadge,
                        [0, 1],
                        [0, 1],
                      ),
                    }}
                  >
                    <ImBadge text="emotion=happy" variant="success" />
                  </div>
                  <div
                    style={{
                      transform: `scale(${interpolate(scoreBadge, [0, 1], [0.5, 1])})`,
                      opacity: interpolate(
                        scoreBadge,
                        [0, 1],
                        [0, 1],
                      ),
                    }}
                  >
                    <ImBadge text="score=0.68" variant="info" />
                  </div>
                </div>

                {/* Scene boundaries */}
                <div style={{ marginTop: 16 }}>
                  <span
                    style={{
                      fontSize: 11,
                      color: COLORS.textSecondary,
                    }}
                  >
                    Scene boundaries
                  </span>
                  <div
                    style={{
                      height: 4,
                      backgroundColor: "rgba(255,255,255,0.06)",
                      borderRadius: 2,
                      marginTop: 6,
                      position: "relative",
                    }}
                  >
                    {[0.15, 0.45, 0.72].map((pos, idx) => (
                      <div
                        key={idx}
                        style={{
                          position: "absolute",
                          left: `${pos * 100}%`,
                          top: -4,
                          width: 2,
                          height: 12,
                          backgroundColor: COLORS.error,
                          opacity: interpolate(
                            frame,
                            [60 + idx * 8, 68 + idx * 8],
                            [0, 1],
                            {
                              extrapolateLeft: "clamp",
                              extrapolateRight: "clamp",
                            },
                          ),
                        }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </ImCard>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
