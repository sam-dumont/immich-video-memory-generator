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

export const TitleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleProgress = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 120 },
    delay: 5,
  });
  const subProgress = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 120 },
    delay: 18,
  });

  const titleOpacity = interpolate(titleProgress, [0, 1], [0, 1]);
  const titleY = interpolate(titleProgress, [0, 1], [25, 0]);
  const subOpacity = interpolate(subProgress, [0, 1], [0, 1]);
  const subY = interpolate(subProgress, [0, 1], [15, 0]);

  const bgScale = interpolate(frame, [0, 158], [1.05, 1.12], {
    extrapolateRight: "clamp",
  });
  const bgOpacity = interpolate(frame, [0, 20], [0, 0.35], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Img
        src={staticFile("stock/thumb-1.jpg")}
        style={{
          position: "absolute",
          width: "130%",
          height: "130%",
          top: "-15%",
          left: "-15%",
          objectFit: "cover",
          filter: "blur(50px) brightness(0.3)",
          opacity: bgOpacity,
          transform: `scale(${bgScale})`,
        }}
      />
      <AbsoluteFill
        style={{ justifyContent: "center", alignItems: "center" }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 20,
          }}
        >
          <div
            style={{
              fontSize: 88,
              fontWeight: 700,
              color: "white",
              fontFamily,
              opacity: titleOpacity,
              transform: `translateY(${titleY}px)`,
              letterSpacing: -1.5,
            }}
          >
            Immich Memories
          </div>
          <div
            style={{
              fontSize: 28,
              color: COLORS.text,
              fontFamily,
              fontWeight: 400,
              opacity: subOpacity,
              transform: `translateY(${subY}px)`,
            }}
          >
            Turn your photo library into cinematic recap videos
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
