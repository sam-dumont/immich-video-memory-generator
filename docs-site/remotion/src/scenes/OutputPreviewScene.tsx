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
import { fontFamily } from "../fonts";

/**
 * Placeholder for the real rendered memory video.
 * Simulates what the output looks like: title screen → photo montage
 * with Ken Burns transitions. Will be replaced by actual OffthreadVideo
 * of a real render at 5x speed.
 *
 * For now: stock photo slideshow with crossfades + title overlay.
 */

const PHOTOS = [
  "stock/thumb-21.jpg",
  "stock/thumb-23.jpg",
  "stock/thumb-25.jpg",
  "stock/thumb-27.jpg",
  "stock/thumb-29.jpg",
  "stock/thumb-31.jpg",
  "stock/thumb-33.jpg",
  "stock/thumb-35.jpg",
];

const SLIDE_DURATION = 45; // frames per photo (~1.5s)

export const OutputPreviewScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Title overlay: "Alice — 2025" fades in then out
  const titleIn = spring({
    frame,
    fps,
    config: { damping: 20, stiffness: 100 },
    delay: 10,
  });
  const titleOut = interpolate(frame, [70, 90], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleOpacity = Math.min(
    interpolate(titleIn, [0, 1], [0, 1]),
    titleOut,
  );

  // Current photo index
  const photoIndex = Math.floor(frame / SLIDE_DURATION);
  const photoProgress = (frame % SLIDE_DURATION) / SLIDE_DURATION;

  // Current and next photo for crossfade
  const currentPhoto = PHOTOS[photoIndex % PHOTOS.length];
  const nextPhoto = PHOTOS[(photoIndex + 1) % PHOTOS.length];

  // Crossfade in last 20% of each slide
  const crossfadeStart = 0.75;
  const crossfadeOpacity =
    photoProgress > crossfadeStart
      ? (photoProgress - crossfadeStart) / (1 - crossfadeStart)
      : 0;

  // Ken Burns: slow zoom + pan per slide
  const kenBurnsScale = 1 + photoProgress * 0.12;
  const kenBurnsX = photoProgress * (photoIndex % 2 === 0 ? -12 : 8);
  const kenBurnsY = photoProgress * (photoIndex % 2 === 0 ? -6 : 4);

  // Subtle vignette
  const vignetteOpacity = 0.5;

  // "5x speed" badge
  const badgeOpacity = interpolate(frame, [5, 15, 280, 300], [0, 0.8, 0.8, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {/* Current photo with Ken Burns */}
      <Img
        src={staticFile(currentPhoto)}
        style={{
          position: "absolute",
          width: "110%",
          height: "110%",
          top: "-5%",
          left: "-5%",
          objectFit: "cover",
          transform: `scale(${kenBurnsScale}) translate(${kenBurnsX}px, ${kenBurnsY}px)`,
        }}
      />

      {/* Next photo crossfading in */}
      {crossfadeOpacity > 0 && (
        <Img
          src={staticFile(nextPhoto)}
          style={{
            position: "absolute",
            width: "110%",
            height: "110%",
            top: "-5%",
            left: "-5%",
            objectFit: "cover",
            opacity: crossfadeOpacity,
          }}
        />
      )}

      {/* Vignette overlay */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.7) 100%)",
          opacity: vignetteOpacity,
        }}
      />

      {/* Title overlay: "Alice — 2025" */}
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          opacity: titleOpacity,
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 8,
          }}
        >
          <div
            style={{
              fontSize: 56,
              fontWeight: 700,
              color: "white",
              fontFamily,
              textShadow: "0 4px 30px rgba(0,0,0,0.8), 0 1px 3px rgba(0,0,0,0.5)",
              letterSpacing: -1,
            }}
          >
            Alice — 2025
          </div>
          <div
            style={{
              fontSize: 20,
              color: "rgba(255,255,255,0.7)",
              fontFamily,
              fontWeight: 400,
              textShadow: "0 2px 12px rgba(0,0,0,0.6)",
            }}
          >
            Person Spotlight
          </div>
        </div>
      </AbsoluteFill>

      {/* 5x speed badge */}
      <div
        style={{
          position: "absolute",
          top: 24,
          right: 24,
          backgroundColor: "rgba(0,0,0,0.6)",
          backdropFilter: "blur(8px)",
          borderRadius: 8,
          padding: "6px 14px",
          display: "flex",
          alignItems: "center",
          gap: 6,
          opacity: badgeOpacity,
        }}
      >
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "rgba(255,255,255,0.9)",
            fontFamily,
            letterSpacing: 0.5,
          }}
        >
          5× speed
        </span>
      </div>

      {/* Thin progress line at bottom */}
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          height: 3,
          backgroundColor: "rgba(107, 143, 232, 0.8)",
          width: `${(frame / 360) * 100}%`,
          borderRadius: 2,
        }}
      />
    </AbsoluteFill>
  );
};
