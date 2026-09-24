import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { PageHeader, CONTENT_X, CONTENT_Y } from "../components/PageHeader";
import { ImCard } from "../components/ImCard";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { ImSelect } from "../components/ImSelect";
import { AnimatedCursor } from "../components/AnimatedCursor";
import { BriefFooter, BriefParams } from "../components/BriefParts";

// The select's rows, in the order the real page lists them: the CLI's
// --memory-type choices, then the custom range the CLI spells as --start/--end.
const MEMORY_TYPES = [
  "Year in Review",
  "Season",
  "Person Spotlight",
  "Multi-Person",
  "Monthly Highlights",
  "On This Day",
  "Album",
  "Trip",
  "Holiday",
  "Surprise me",
  "Custom date range",
];

// The fixture library is one household's June 2024, and every scene after this
// one shows that cut; the brief has to ask for it.
const PICKED = "Monthly Highlights";

const OPEN_DROPDOWN = 45;
const PICK_TYPE = 72;
const CLICK_CUT = 145;

// Measured against a 1920x1080 still render: the select field, the picked
// dropdown row, and the middle of the full-width Cut button. The rows are 30px
// and Monthly Highlights is the fifth.
const ROW_HEIGHT = 30;
const PICKED_INDEX = MEMORY_TYPES.indexOf(PICKED);
const SELECT_XY = { x: CONTENT_X + 144, y: CONTENT_Y + 111 };
const FIRST_ROW_XY = {
  x: CONTENT_X + 144,
  y: CONTENT_Y + 156 + PICKED_INDEX * ROW_HEIGHT,
};
const CUT_XY = { x: CONTENT_X + 520, y: CONTENT_Y + 556 };

const cursorSteps = [
  { frame: 32, ...SELECT_XY },
  { frame: OPEN_DROPDOWN, ...SELECT_XY, click: true },
  { frame: 62, ...FIRST_ROW_XY },
  { frame: PICK_TYPE, ...FIRST_ROW_XY, click: true },
  { frame: 130, ...CUT_XY },
  { frame: CLICK_CUT, ...CUT_XY, click: true },
];

type Props = { bassIntensity?: number };

export const BriefScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const dropdownOpacity = interpolate(
    frame,
    [OPEN_DROPDOWN - 1, OPEN_DROPDOWN + 3, PICK_TYPE - 1, PICK_TYPE + 3],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const rowHighlighted = frame >= 60 && frame < PICK_TYPE + 4;
  const typePicked = frame >= PICK_TYPE;

  // The type's own parameters slide in under the select once it is picked.
  const paramsReveal = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 110 },
    delay: PICK_TYPE + 2,
  });

  const cutPress = interpolate(frame, [CLICK_CUT, CLICK_CUT + 6], [1, 0.985], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar active="Memory" />
        <div
          style={{
            flex: 1,
            padding: "20px 32px",
            overflow: "hidden",
            fontFamily,
            display: "flex",
            flexDirection: "column",
            position: "relative",
          }}
        >
          <PageHeader title="Memory" />

          <ImSectionHeader icon="auto_awesome" title="What is it about" />

          {/* Memory type select + its dropdown */}
          <div style={{ position: "relative", width: 288 }}>
            <ImSelect
              label="Memory type"
              value={typePicked ? PICKED : ""}
            />
            {dropdownOpacity > 0 && (
              <div
                style={{
                  position: "absolute",
                  top: "100%",
                  left: 0,
                  right: 0,
                  marginTop: 4,
                  backgroundColor: COLORS.elevated,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: 10,
                  opacity: dropdownOpacity,
                  transform: `translateY(${(1 - dropdownOpacity) * -6}px)`,
                  zIndex: 100,
                  overflow: "hidden",
                  boxShadow:
                    "0 8px 24px rgba(0,0,0,0.45), 0 2px 8px rgba(0,0,0,0.3)",
                }}
              >
                {MEMORY_TYPES.map((name) => {
                  const highlighted = name === PICKED && rowHighlighted;
                  return (
                    <div
                      key={name}
                      style={{
                        height: 30,
                        display: "flex",
                        alignItems: "center",
                        padding: "0 16px",
                        fontSize: 13,
                        fontFamily,
                        color: COLORS.text,
                        backgroundColor: highlighted
                          ? "rgba(107, 143, 232, 0.15)"
                          : "transparent",
                        borderLeft: highlighted
                          ? `3px solid ${COLORS.primary}`
                          : "3px solid transparent",
                      }}
                    >
                      {name}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* What the picked type asks for */}
          {typePicked && (
            <ImCard
              style={{
                marginTop: 12,
                opacity: paramsReveal,
                transform: `translateY(${(1 - paramsReveal) * 10}px)`,
              }}
            >
              <BriefParams />
            </ImCard>
          )}

          <BriefFooter cutScale={cutPress} />
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
