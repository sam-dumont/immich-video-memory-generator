import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { MaterialIcon } from "../components/MaterialIcon";
import { ImButton } from "../components/ImButton";
import { ImSectionHeader } from "../components/ImSectionHeader";

type Props = { bassIntensity?: number };

/* ------------------------------------------------------------------ */
/*  Checkbox – Quasar-style toggle                                     */
/* ------------------------------------------------------------------ */
const Checkbox: React.FC<{
  checked: boolean;
  label: string;
  style?: React.CSSProperties;
}> = ({ checked, label, style }) => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      gap: 8,
      fontFamily,
      fontSize: 13,
      color: COLORS.text,
      ...style,
    }}
  >
    <div
      style={{
        width: 18,
        height: 18,
        borderRadius: 3,
        border: checked
          ? `2px solid ${COLORS.primary}`
          : `2px solid ${COLORS.textSecondary}`,
        backgroundColor: checked ? COLORS.primary : "transparent",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {checked && (
        <MaterialIcon name="check" size={14} color="white" />
      )}
    </div>
    {label}
  </div>
);

/* ------------------------------------------------------------------ */
/*  Slider – Quasar-style range                                        */
/* ------------------------------------------------------------------ */
const Slider: React.FC<{
  label: string;
  value: string;
  percent: number;
}> = ({ label, value, percent }) => (
  <div style={{ fontFamily, fontSize: 13, color: COLORS.text }}>
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        marginBottom: 6,
      }}
    >
      <span>{label}</span>
      <span style={{ color: COLORS.textSecondary }}>{value}</span>
    </div>
    <div
      style={{
        height: 4,
        backgroundColor: "rgba(255,255,255,0.08)",
        borderRadius: 2,
        position: "relative",
      }}
    >
      <div
        style={{
          width: `${percent}%`,
          height: "100%",
          backgroundColor: COLORS.primary,
          borderRadius: 2,
        }}
      />
      {/* Thumb */}
      <div
        style={{
          position: "absolute",
          top: -5,
          left: `${percent}%`,
          width: 14,
          height: 14,
          borderRadius: "50%",
          backgroundColor: COLORS.primary,
          marginLeft: -7,
          boxShadow: "0 1px 4px rgba(0,0,0,0.3)",
        }}
      />
    </div>
  </div>
);

/* ------------------------------------------------------------------ */
/*  Stat box – small inline stat used in the top row                   */
/* ------------------------------------------------------------------ */
const StatBox: React.FC<{
  label: string;
  value: string;
  opacity: number;
  translateY: number;
}> = ({ label, value, opacity, translateY }) => (
  <div
    style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      opacity,
      transform: `translateY(${translateY}px)`,
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
        fontSize: 20,
        fontWeight: 700,
        color: COLORS.text,
        fontFamily,
      }}
    >
      {value}
    </span>
  </div>
);

/* ================================================================== */
/*  Scene                                                              */
/* ================================================================== */

export const GenerateMemoriesScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Staggered reveals via spring
  const reveal = (delay: number) => {
    const s = spring({ frame, fps, config: { damping: 18, stiffness: 160 }, delay });
    return {
      opacity: interpolate(s, [0, 1], [0, 1]),
      translateY: interpolate(s, [0, 1], [14, 0]),
    };
  };

  const r0 = reveal(0);
  const r1 = reveal(5);
  const r2 = reveal(10);
  const r3 = reveal(15);
  const r4 = reveal(20);
  const r5 = reveal(25);
  const r6 = reveal(30);
  const r7 = reveal(35);
  const r8 = reveal(40);
  const r9 = reveal(45);
  const r10 = reveal(50);

  // Scroll down to show more content
  const scrollY = interpolate(frame, [50, 140], [0, 120], {
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
            <div
              style={{
                opacity: r0.opacity,
                transform: `translateY(${r0.translateY}px)`,
              }}
            >
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
            </div>

            {/* Subtitle: date range */}
            <div
              style={{
                fontSize: 13,
                color: COLORS.textSecondary,
                marginTop: 4,
                marginBottom: 16,
                opacity: r0.opacity,
                transform: `translateY(${r0.translateY}px)`,
                fontFamily,
              }}
            >
              Person Spotlight — 2025
            </div>

            {/* Stats row */}
            <div
              style={{
                display: "flex",
                gap: 32,
                marginBottom: 16,
                opacity: r1.opacity,
                transform: `translateY(${r1.translateY}px)`,
              }}
            >
              <StatBox label="Selected Clips" value="60" opacity={1} translateY={0} />
              <StatBox label="Total Duration" value="4:44" opacity={1} translateY={0} />
            </div>

            {/* Cache notice — blue info card */}
            <div
              style={{
                backgroundColor: "rgba(107, 143, 232, 0.08)",
                border: `1px solid rgba(107, 143, 232, 0.25)`,
                borderRadius: 8,
                padding: "12px 16px",
                marginBottom: 20,
                opacity: r2.opacity,
                transform: `translateY(${r2.translateY}px)`,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                }}
              >
                <MaterialIcon name="info" size={18} color={COLORS.primary} />
                <div>
                  <div
                    style={{
                      fontSize: 13,
                      color: COLORS.text,
                      lineHeight: 1.5,
                      fontFamily,
                    }}
                  >
                    <strong>Previously Analyzed:</strong> Found 18 clips already
                    analyzed from cache. This will save approximately 9m 0s.
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <ImButton
                      text="USE CACHED ANALYSIS (SKIP RE-ANALYSIS)"
                      variant="secondary"
                      style={{ fontSize: 11, padding: "6px 14px" }}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Section: Generate Memories */}
            <div
              style={{
                opacity: r3.opacity,
                transform: `translateY(${r3.translateY}px)`,
              }}
            >
              <ImSectionHeader icon="auto_awesome" title="Generate Memories" />
            </div>

            {/* Target / avg / clips needed row */}
            <div
              style={{
                display: "flex",
                gap: 16,
                alignItems: "flex-end",
                marginBottom: 12,
                opacity: r4.opacity,
                transform: `translateY(${r4.translateY}px)`,
              }}
            >
              {/* Target duration */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 12, color: COLORS.textSecondary, fontFamily }}>
                  Target duration (min):
                </span>
                <div
                  style={{
                    backgroundColor: COLORS.bg,
                    border: `1px solid ${COLORS.border}`,
                    borderRadius: 6,
                    padding: "6px 12px",
                    fontSize: 14,
                    color: COLORS.text,
                    fontFamily,
                    width: 60,
                  }}
                >
                  2
                </div>
              </div>

              {/* Avg seconds per clip */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span style={{ fontSize: 12, color: COLORS.textSecondary, fontFamily }}>
                  Avg seconds per clip:
                </span>
                <div
                  style={{
                    backgroundColor: COLORS.bg,
                    border: `1px solid ${COLORS.border}`,
                    borderRadius: 6,
                    padding: "6px 12px",
                    fontSize: 14,
                    color: COLORS.text,
                    fontFamily,
                    width: 60,
                  }}
                >
                  5
                </div>
              </div>

              {/* Clips needed */}
              <span
                style={{
                  fontSize: 13,
                  color: COLORS.textSecondary,
                  fontFamily,
                  paddingBottom: 8,
                }}
              >
                Clips needed: <strong style={{ color: COLORS.text }}>24</strong>
              </span>
            </div>

            {/* Clip summary */}
            <div
              style={{
                fontSize: 13,
                color: COLORS.textSecondary,
                marginBottom: 12,
                opacity: r5.opacity,
                transform: `translateY(${r5.translateY}px)`,
                fontFamily,
              }}
            >
              117 clips (53 HDR, 26 favorites)
            </div>

            {/* Checkboxes */}
            <div
              style={{
                display: "flex",
                gap: 24,
                marginBottom: 12,
                opacity: r5.opacity,
                transform: `translateY(${r5.translateY}px)`,
              }}
            >
              <Checkbox checked={false} label="HDR clips only" />
              <Checkbox checked={true} label="Prioritize favorites" />
              <Checkbox checked={false} label="Analyze all videos" />
            </div>

            {/* Slider: Max non-favorites */}
            <div
              style={{
                maxWidth: 400,
                marginBottom: 16,
                opacity: r6.opacity,
                transform: `translateY(${r6.translateY}px)`,
              }}
            >
              <Slider label="Max non-favorites:" value="25%" percent={25} />
            </div>

            {/* Explanation text */}
            <div
              style={{
                fontSize: 13,
                color: COLORS.textSecondary,
                marginBottom: 16,
                opacity: r7.opacity,
                transform: `translateY(${r7.translateY}px)`,
                fontFamily,
              }}
            >
              To fit 2 minutes with ~5s per clip, you need approximately 24
              clips.
            </div>

            {/* Generate button */}
            <div
              style={{
                marginBottom: 16,
                opacity: r8.opacity,
                transform: `translateY(${r8.translateY}px)`,
              }}
            >
              <ImButton
                text="GENERATE MEMORIES"
                variant="primary"
                icon="auto_awesome"
                fullWidth
              />
            </div>

            {/* Action buttons row */}
            <div
              style={{
                display: "flex",
                gap: 10,
                marginBottom: 20,
                opacity: r9.opacity,
                transform: `translateY(${r9.translateY}px)`,
              }}
            >
              <ImButton text="SELECT ALL" variant="secondary" />
              <ImButton text="DESELECT ALL" variant="secondary" />
              <ImButton text="INVERT SELECTION" variant="secondary" />
            </div>

            {/* Videos Found header */}
            <div
              style={{
                opacity: r10.opacity,
                transform: `translateY(${r10.translateY}px)`,
              }}
            >
              <ImSectionHeader icon="video_library" title="117 Videos Found" />
            </div>
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
