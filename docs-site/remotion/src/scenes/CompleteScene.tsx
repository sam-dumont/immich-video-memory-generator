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
import { ImButton } from "../components/ImButton";

type Props = { bassIntensity?: number };

export const CompleteScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const bannerEntry = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 150 },
    delay: 5,
  });
  const bannerScale = interpolate(bannerEntry, [0, 1], [0.9, 1]);
  const bannerOpacity = interpolate(bannerEntry, [0, 1], [0, 1]);

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={4} completedSteps={[1, 2, 3]} />
        <div
          style={{
            flex: 1,
            padding: 24,
            fontFamily,
            overflow: "hidden",
          }}
        >
          {/* Success banner */}
          <div
            style={{
              backgroundColor: "rgba(74, 222, 128, 0.1)",
              border: "1px solid rgba(74, 222, 128, 0.3)",
              borderRadius: 10,
              padding: 20,
              display: "flex",
              alignItems: "center",
              gap: 16,
              opacity: bannerOpacity,
              transform: `scale(${bannerScale})`,
            }}
          >
            <span style={{ fontSize: 32 }}>✅</span>
            <div>
              <div
                style={{
                  fontSize: 16,
                  fontWeight: 600,
                  color: COLORS.success,
                }}
              >
                Your memory video is ready!
              </div>
              <div
                style={{
                  fontSize: 13,
                  color: COLORS.textSecondary,
                  marginTop: 4,
                }}
              >
                Saved to: ~/Videos/Memories/alice_2025_memories.mp4 (42
                MB)
              </div>
            </div>
          </div>

          {/* Video preview */}
          <div
            style={{
              marginTop: 20,
              borderRadius: 10,
              overflow: "hidden",
              border: `1px solid ${COLORS.border}`,
              height: 340,
              position: "relative",
              opacity: interpolate(frame, [20, 35], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            <Img
              src={staticFile("stock/thumb-4.jpg")}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
              }}
            />
            {/* Play button overlay */}
            <div
              style={{
                position: "absolute",
                top: "50%",
                left: "50%",
                width: 60,
                height: 60,
                marginLeft: -30,
                marginTop: -30,
                backgroundColor: "rgba(0,0,0,0.6)",
                borderRadius: "50%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <span style={{ fontSize: 24, marginLeft: 4 }}>▶</span>
            </div>
          </div>

          <div style={{ marginTop: 16 }}>
            <ImButton
              text="Generate Video"
              variant="primary"
              fullWidth
              disabled
            />
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
