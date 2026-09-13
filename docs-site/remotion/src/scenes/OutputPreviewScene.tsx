import React from "react";
import {
  AbsoluteFill,
  interpolate,
  OffthreadVideo,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { fontFamily } from "../fonts";
import { COLORS } from "../theme";

/**
 * The real rendered memory, last and full bleed, in real time. It is cut by the
 * product itself on the hermetic launch, over the six CC0 stock photographs in
 * tests/e2e/fixtures -- see `make demo-output`. Nothing in it is anyone's library.
 */

// The rendered film is 32 s: pictures until about 25 s, then its blurred ending
// card. The demo plays the last nine seconds of pictures and ends where the
// card begins, so the closing image is a picture rather than a blur.
const FILM_START_SECONDS = 16;
const FPS = 30;

type Props = { frames: number };

export const OutputPreviewScene: React.FC<Props> = ({ frames }) => {
  const frame = useCurrentFrame();
  const startFrom = FILM_START_SECONDS * FPS;

  // The closing line fades in over the last two seconds.
  const closing = interpolate(frame, [frames - 60, frames - 40], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const progress = (frame / frames) * 100;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <OffthreadVideo
        src={staticFile("output-preview.mp4")}
        style={{ width: "100%", height: "100%", objectFit: "contain" }}
        startFrom={startFrom}
        playbackRate={1}
        volume={0}
      />

      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 56,
          textAlign: "center",
          opacity: closing,
          fontFamily,
        }}
      >
        <div style={{ fontSize: 30, fontWeight: 700, color: "#ffffff", letterSpacing: 0.5 }}>
          Immich Memories
        </div>
        <div style={{ fontSize: 16, color: "rgba(255,255,255,0.85)", marginTop: 6 }}>
          Your library, cut into the videos it deserves. Self-hosted, open source.
        </div>
      </div>

      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          height: 3,
          backgroundColor: COLORS.primary,
          width: `${progress}%`,
          borderRadius: 2,
        }}
      />
    </AbsoluteFill>
  );
};
