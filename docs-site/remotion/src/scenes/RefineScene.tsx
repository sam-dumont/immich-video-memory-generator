import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { MaterialIcon } from "../components/MaterialIcon";
import { ImButton } from "../components/ImButton";
import { AnimatedCursor } from "../components/AnimatedCursor";

type Props = { bassIntensity?: number };

/* ------------------------------------------------------------------ */
/*  Clip row data                                                      */
/* ------------------------------------------------------------------ */
const CLIP_ROWS = [
  { star: true, hlg: true, date: "February 18, 2025", using: "0:08", of: "1:02" },
  { star: true, hlg: true, date: "March 01, 2025", using: "0:05", of: "0:28" },
  { star: true, hlg: true, date: "March 15, 2025", using: "0:05", of: "0:19" },
  { star: false, hlg: false, date: "March 18, 2025", using: "0:03", of: "0:12" },
  { star: false, hlg: true, date: "March 18, 2025", using: "0:16", of: "0:31" },
  { star: true, hlg: false, date: "March 21, 2025", using: "0:05", of: "0:12" },
  { star: false, hlg: false, date: "March 22, 2025", using: "0:05", of: "0:10" },
  { star: false, hlg: false, date: "March 22, 2025", using: "0:06", of: "0:13" },
  { star: true, hlg: false, date: "March 22, 2025", using: "0:16", of: "0:16" },
  { star: false, hlg: true, date: "March 23, 2025", using: "0:16", of: "0:16" },
  { star: true, hlg: true, date: "March 23, 2025", using: "0:14", of: "0:16" },
  { star: false, hlg: false, date: "March 25, 2025", using: "0:05", of: "0:12" },
  { star: false, hlg: false, date: "March 26, 2025", using: "0:05", of: "0:10" },
  { star: true, hlg: true, date: "March 28, 2025", using: "0:08", of: "0:22" },
  { star: false, hlg: false, date: "March 29, 2025", using: "0:05", of: "0:09" },
];

/* ------------------------------------------------------------------ */
/*  Stat box for the top row                                           */
/* ------------------------------------------------------------------ */
const StatBox: React.FC<{
  label: string;
  value: string;
  highlight?: boolean;
}> = ({ label, value, highlight }) => (
  <div
    style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: 2,
    }}
  >
    <span
      style={{
        fontSize: 11,
        color: COLORS.textSecondary,
        fontFamily,
      }}
    >
      {label}
    </span>
    <span
      style={{
        fontSize: 18,
        fontWeight: 700,
        color: highlight ? COLORS.warning : COLORS.text,
        fontFamily,
      }}
    >
      {value}
    </span>
  </div>
);

/* ------------------------------------------------------------------ */
/*  Collapsed clip row                                                 */
/* ------------------------------------------------------------------ */
const ClipRow: React.FC<{
  clip: (typeof CLIP_ROWS)[0];
  expanded: boolean;
  opacity: number;
  translateY: number;
  thumbnailSrc?: string;
}> = ({ clip, expanded, opacity, translateY, thumbnailSrc }) => {
  const checkboxColor = COLORS.primary;
  return (
    <div
      style={{
        opacity,
        transform: `translateY(${translateY}px)`,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 0",
          borderBottom: `1px solid ${COLORS.border}`,
        }}
      >
        {/* Checkbox */}
        <div
          style={{
            width: 16,
            height: 16,
            borderRadius: 3,
            border: `2px solid ${checkboxColor}`,
            backgroundColor: checkboxColor,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="check" size={12} color="white" />
        </div>

        {/* Star */}
        {clip.star && (
          <span style={{ fontSize: 13, color: COLORS.warning }}>★</span>
        )}

        {/* HLG badge */}
        {clip.hlg && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              fontFamily,
              color: COLORS.primary,
              backgroundColor: "rgba(107, 143, 232, 0.15)",
              padding: "2px 6px",
              borderRadius: 4,
            }}
          >
            HLG
          </span>
        )}

        {/* Date + duration info */}
        <span
          style={{
            fontSize: 12,
            color: COLORS.text,
            fontFamily,
            flex: 1,
          }}
        >
          {clip.date} • Using {clip.using} of {clip.of}
        </span>

        {/* Chevron */}
        <MaterialIcon
          name={expanded ? "expand_more" : "chevron_right"}
          size={18}
          color={COLORS.textSecondary}
        />
      </div>

      {/* Expanded content for the first row */}
      {expanded && (
        <div
          style={{
            padding: "12px 0 12px 26px",
            display: "flex",
            gap: 16,
            borderBottom: `1px solid ${COLORS.border}`,
          }}
        >
          {/* Thumbnail */}
          <div
            style={{
              width: 160,
              height: 220,
              borderRadius: 6,
              overflow: "hidden",
              flexShrink: 0,
            }}
          >
            <Img
              src={staticFile(thumbnailSrc ?? "stock/thumb-14.jpg")}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
              }}
            />
          </div>

          {/* Controls */}
          <div style={{ flex: 1, fontFamily }}>
            <div
              style={{
                fontSize: 12,
                color: COLORS.textSecondary,
                marginBottom: 8,
              }}
            >
              Select range
            </div>

            {/* Range slider mock */}
            <div
              style={{
                height: 4,
                backgroundColor: "rgba(255,255,255,0.08)",
                borderRadius: 2,
                position: "relative",
                marginBottom: 16,
              }}
            >
              <div
                style={{
                  position: "absolute",
                  left: "0%",
                  width: "13%",
                  height: "100%",
                  backgroundColor: COLORS.primary,
                  borderRadius: 2,
                }}
              />
              {/* Left thumb */}
              <div
                style={{
                  position: "absolute",
                  top: -5,
                  left: "0%",
                  width: 14,
                  height: 14,
                  borderRadius: "50%",
                  backgroundColor: COLORS.primary,
                }}
              />
              {/* Right thumb */}
              <div
                style={{
                  position: "absolute",
                  top: -5,
                  left: "13%",
                  width: 14,
                  height: 14,
                  borderRadius: "50%",
                  backgroundColor: COLORS.primary,
                  marginLeft: -7,
                }}
              />
            </div>

            {/* Buttons row */}
            <div
              style={{
                display: "flex",
                gap: 8,
                flexWrap: "wrap",
                marginBottom: 12,
              }}
            >
              <ImButton
                text="PREVIEW"
                variant="secondary"
                icon="play_arrow"
                style={{ fontSize: 10, padding: "5px 10px" }}
              />
              <ImButton
                text="FIRST 5S"
                variant="secondary"
                style={{ fontSize: 10, padding: "5px 10px" }}
              />
              <ImButton
                text="LAST 5S"
                variant="secondary"
                style={{ fontSize: 10, padding: "5px 10px" }}
              />
              <ImButton
                text="MIDDLE 5S"
                variant="secondary"
                style={{ fontSize: 10, padding: "5px 10px" }}
              />
              <ImButton
                text="FULL CLIP"
                variant="secondary"
                style={{ fontSize: 10, padding: "5px 10px" }}
              />
            </div>

            {/* Rotation dropdown */}
            <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <span
                style={{
                  fontSize: 12,
                  color: COLORS.textSecondary,
                }}
              >
                Rotation:
              </span>
              <div
                style={{
                  backgroundColor: COLORS.bg,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: 6,
                  padding: "4px 10px",
                  fontSize: 12,
                  color: COLORS.text,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                Auto
                <MaterialIcon
                  name="arrow_drop_down"
                  size={16}
                  color={COLORS.textSecondary}
                />
              </div>
              <span
                style={{
                  fontSize: 11,
                  color: COLORS.textSecondary,
                }}
              >
                4K (3840 x 2160)
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

/* ================================================================== */
/*  Scene                                                              */
/* ================================================================== */

export const RefineScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();

  // All clips visible from frame 0, collapsed
  const clipReveal = (_index: number) => ({
    opacity: 1,
    translateY: 0,
  });

  // Clips expand one by one: clip 0 at frame 5, clip 1 at frame 9, ..., clip 14 at frame 61
  const isExpanded = (index: number) => {
    const expandFrame = 5 + index * 4;
    return frame >= expandFrame;
  };

  // Each expanded row uses a different stock photo (thumb-1 through thumb-15)
  const expandedThumbnails: Record<number, string> = {};
  for (let i = 0; i < 15; i++) {
    expandedThumbnails[i] = `stock/thumb-${i + 1}.jpg`;
  }

  // Scroll: ease in-out (slow start, fast middle, slow landing on button)
  const scrollY = interpolate(frame, [30, 145], [0, 4000], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.cubic),
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={2} completedSteps={[1]} />
        <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          <div
            style={{
              transform: `translateY(-${scrollY}px)`,
              padding: "24px 32px",
              fontFamily,
            }}
          >
            {/* Page title */}
            <h1
              style={{
                fontSize: 22,
                fontWeight: 700,
                color: COLORS.text,
                margin: 0,
                fontFamily,
              }}
            >
              Clip Review
            </h1>
            <div
              style={{
                fontSize: 13,
                color: COLORS.textSecondary,
                marginTop: 4,
                fontFamily,
              }}
            >
              Person Spotlight — 2025
            </div>

            {/* Section title */}
            <h2
              style={{
                fontSize: 16,
                fontWeight: 600,
                color: COLORS.text,
                marginTop: 16,
                marginBottom: 4,
                fontFamily,
              }}
            >
              Review & Refine Selected Clips
            </h2>
            <div
              style={{
                fontSize: 13,
                color: COLORS.textSecondary,
                marginBottom: 16,
                fontFamily,
              }}
            >
              Adjust time segments, preview clips, and remove any unwanted
              selections.
            </div>

            {/* Stats row: 4 boxes */}
            <div
              style={{
                display: "flex",
                gap: 32,
                marginBottom: 16,
              }}
            >
              <StatBox label="Selected Clips" value="15" />
              <StatBox label="Total Duration" value="2:02" />
              <StatBox label="Target" value="2:00" />
              <StatBox label="Difference" value="+0:02" highlight />
            </div>

            {/* Batch controls */}
            <div
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                marginBottom: 16,
              }}
            >
              <ImButton
                text="SET ALL TO FIRST 5S"
                variant="secondary"
                style={{ fontSize: 10, padding: "6px 12px" }}
              />
              <ImButton
                text="SET ALL TO MIDDLE 5S"
                variant="secondary"
                style={{ fontSize: 10, padding: "6px 12px" }}
              />
              {/* Custom seconds input */}
              <span
                style={{
                  fontSize: 12,
                  color: COLORS.textSecondary,
                  fontFamily,
                  marginLeft: 8,
                }}
              >
                Custom seconds:
              </span>
              <div
                style={{
                  backgroundColor: COLORS.bg,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: 6,
                  padding: "4px 10px",
                  fontSize: 13,
                  color: COLORS.text,
                  fontFamily,
                  width: 40,
                  textAlign: "center",
                }}
              >
                5
              </div>
              <ImButton
                text="APPLY"
                variant="secondary"
                style={{ fontSize: 10, padding: "6px 12px" }}
              />
            </div>

            {/* Clip list */}
            <div>
              {CLIP_ROWS.map((clip, i) => {
                const cr = clipReveal(i);
                return (
                  <ClipRow
                    key={i}
                    clip={clip}
                    expanded={isExpanded(i)}
                    opacity={cr.opacity}
                    translateY={cr.translateY}
                    thumbnailSrc={expandedThumbnails[i]}
                  />
                );
              })}
            </div>

            {/* Include in compilation checkbox */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginTop: 8,
                marginBottom: 20,
              }}
            >
              <div
                style={{
                  width: 16,
                  height: 16,
                  borderRadius: 3,
                  border: `2px solid ${COLORS.primary}`,
                  backgroundColor: COLORS.primary,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <MaterialIcon name="check" size={12} color="white" />
              </div>
              <span
                style={{
                  fontSize: 12,
                  color: COLORS.text,
                  fontFamily,
                }}
              >
                Include in compilation
              </span>
            </div>

            {/* Final Duration */}
            <div
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: COLORS.text,
                marginBottom: 16,
                fontFamily,
              }}
            >
              Final Duration: 2:02
            </div>

            {/* Bottom buttons */}
            <div
              style={{
                display: "flex",
                gap: 12,
                alignItems: "center",
              }}
            >
              <ImButton
                text="BACK TO SELECTION"
                variant="secondary"
                icon="arrow_back"
              />
              <ImButton
                text="RE-RUN ANALYSIS"
                variant="secondary"
                icon="refresh"
              />
              <ImButton
                text="CONTINUE TO GENERATION"
                variant="primary"
                icon="arrow_forward"
              />
            </div>
          </div>

          {/* Cursor: appears at CONTINUE button, then clicks */}
          <AnimatedCursor
            steps={[
              { frame: 148, x: 850, y: 750 },
              { frame: 155, x: 850, y: 750, click: true },
            ]}
          />
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
