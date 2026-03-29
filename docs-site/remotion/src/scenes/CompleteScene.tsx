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
import { MaterialIcon } from "../components/MaterialIcon";

type Props = { bassIntensity?: number };

export const CompleteScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Success banner celebration effect (scale pop)
  const bannerEntry = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 150 },
    delay: 5,
  });
  const bannerScale = interpolate(bannerEntry, [0, 1], [0.92, 1]);
  const bannerOpacity = interpolate(bannerEntry, [0, 1], [0, 1]);

  // Video preview fades in slightly delayed (simulating video player loading)
  const videoEntry = spring({
    frame,
    fps,
    config: { damping: 200 },
    delay: 20,
  });
  const videoOpacity = interpolate(videoEntry, [0, 1], [0, 1]);

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

          {/* Success banner */}
          <div
            style={{
              backgroundColor: "rgba(74, 222, 128, 0.1)",
              border: "1px solid rgba(74, 222, 128, 0.3)",
              borderRadius: 10,
              padding: "18px 20px",
              display: "flex",
              alignItems: "center",
              gap: 16,
              opacity: bannerOpacity,
              transform: `scale(${bannerScale})`,
              transformOrigin: "center center",
              marginBottom: 20,
            }}
          >
            <MaterialIcon
              name="check_circle"
              size={40}
              color={COLORS.success}
            />
            <div>
              <div
                style={{
                  fontSize: 16,
                  fontWeight: 600,
                  color: COLORS.success,
                  fontFamily,
                }}
              >
                Your memory video is ready!
              </div>
              <div
                style={{
                  fontSize: 13,
                  color: COLORS.textSecondary,
                  fontFamily,
                  marginTop: 4,
                }}
              >
                Saved to: ~/Videos/Memories/alice_2025_memories.mp4 (42 MB)
              </div>
            </div>
          </div>

          {/* Video player preview */}
          <div
            style={{
              flex: 1,
              borderRadius: 10,
              overflow: "hidden",
              border: `1px solid ${COLORS.border}`,
              position: "relative",
              opacity: videoOpacity,
              minHeight: 0,
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
                width: 64,
                height: 64,
                marginLeft: -32,
                marginTop: -32,
                backgroundColor: "rgba(0,0,0,0.6)",
                borderRadius: "50%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <MaterialIcon
                name="play_arrow"
                size={36}
                color="white"
                style={{ marginLeft: 2 }}
              />
            </div>
          </div>

          {/* Bottom buttons */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              paddingTop: 16,
            }}
          >
            <ImButton
              text="BACK TO GENERATION OPTIONS"
              variant="secondary"
              icon="arrow_back"
            />
            <ImButton
              text="START NEW PROJECT"
              variant="ghost"
              icon="refresh"
            />
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
