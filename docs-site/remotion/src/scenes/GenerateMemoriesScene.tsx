import React from "react";
import {
  AbsoluteFill,
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
import { AnimatedCursor } from "../components/AnimatedCursor";

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
}> = ({ label, value }) => (
  <div
    style={{
      display: "flex",
      flexDirection: "column",
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
  const { fps: _fps } = useVideoConfig();

  // Button pressed state: scale down briefly on click at frame 40
  const buttonPressed =
    frame >= 40 && frame < 46
      ? 0.97
      : 1;

  const cursorSteps = [
    { frame: 30, x: 1060, y: 650, click: false },
    { frame: 40, x: 1060, y: 650, click: true },
  ];

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={2} completedSteps={[1]} />
        <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          <div
            style={{
              padding: "18px 32px",
              fontFamily,
            }}
          >
            {/* Page title */}
            <div>
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
                marginBottom: 12,
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
                marginBottom: 12,
              }}
            >
              <StatBox label="Selected Clips" value="60" />
              <StatBox label="Total Duration" value="4:44" />
            </div>

            {/* Cache notice — blue info card */}
            <div
              style={{
                backgroundColor: "rgba(107, 143, 232, 0.08)",
                border: `1px solid rgba(107, 143, 232, 0.25)`,
                borderRadius: 8,
                padding: "10px 16px",
                marginBottom: 14,
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
            <div>
              <ImSectionHeader icon="auto_awesome" title="Generate Memories" />
            </div>

            {/* Target / avg / clips needed row */}
            <div
              style={{
                display: "flex",
                gap: 16,
                alignItems: "flex-end",
                marginBottom: 8,
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
                marginBottom: 8,
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
                marginBottom: 8,
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
                marginBottom: 10,
              }}
            >
              <Slider label="Max non-favorites:" value="25%" percent={25} />
            </div>

            {/* Explanation text */}
            <div
              style={{
                fontSize: 13,
                color: COLORS.textSecondary,
                marginBottom: 10,
                fontFamily,
              }}
            >
              To fit 2 minutes with ~5s per clip, you need approximately 24
              clips.
            </div>

            {/* Generate button */}
            <div
              style={{
                marginBottom: 12,
                transform: `scale(${buttonPressed})`,
                transformOrigin: "center center",
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
                marginBottom: 14,
              }}
            >
              <ImButton text="SELECT ALL" variant="secondary" />
              <ImButton text="DESELECT ALL" variant="secondary" />
              <ImButton text="INVERT SELECTION" variant="secondary" />
            </div>

            {/* Videos Found header */}
            <div>
              <ImSectionHeader icon="video_library" title="117 Videos Found" />
            </div>
          </div>
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
