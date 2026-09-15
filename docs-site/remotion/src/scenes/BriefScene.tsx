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
import { ImButton } from "../components/ImButton";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { ImSeparator } from "../components/ImSeparator";
import { ImToggle } from "../components/ImToggle";
import { ImSelect } from "../components/ImSelect";
import { MaterialIcon } from "../components/MaterialIcon";
import { AnimatedCursor } from "../components/AnimatedCursor";

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

const PICKED = "Monthly Highlights";

const OPEN_DROPDOWN = 45;
const PICK_TYPE = 72;
const CLICK_CUT = 145;

// Measured against a 1920x1080 still render: the select field, the first
// dropdown row, and the middle of the full-width Cut button. Rows are 30 px, so
// the picked row is the first plus its index.
const ROW_HEIGHT = 30;
const SELECT_XY = { x: CONTENT_X + 144, y: CONTENT_Y + 111 };
const FIRST_ROW_XY = { x: CONTENT_X + 144, y: CONTENT_Y + 156 };
const PICKED_ROW_XY = {
  x: FIRST_ROW_XY.x,
  y: FIRST_ROW_XY.y + ROW_HEIGHT * MEMORY_TYPES.indexOf(PICKED),
};
const CUT_XY = { x: CONTENT_X + 520, y: CONTENT_Y + 440 };

const cursorSteps = [
  { frame: 32, ...SELECT_XY },
  { frame: OPEN_DROPDOWN, ...SELECT_XY, click: true },
  { frame: 62, ...PICKED_ROW_XY },
  { frame: PICK_TYPE, ...PICKED_ROW_XY, click: true },
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
              <div style={{ display: "flex", gap: 24, alignItems: "flex-end" }}>
                <ImSelect label="Year" value="2024" style={{ width: 144 }} />
                <ImSelect label="Month" value="June" style={{ width: 192 }} />
                <ImSelect
                  label="Only with (optional)"
                  value=""
                  style={{ width: 256 }}
                />
              </div>
            </ImCard>
          )}

          <div style={{ marginTop: 16 }}>
            <ImSectionHeader icon="timer" title="How long" />
          </div>
          <ImToggle
            label="Auto duration"
            checked
            trailing={
              <>
                <span
                  style={{
                    fontSize: 15,
                    fontWeight: 700,
                    color: COLORS.text,
                    fontFamily,
                    marginLeft: 8,
                  }}
                >
                  Auto &middot; 1m 00s
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: COLORS.textSecondary,
                    fontFamily,
                    marginLeft: 8,
                  }}
                >
                  the type&rsquo;s default length
                </span>
              </>
            }
          />

          {/* Advanced expansion — collapsed, as the brief opens */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              marginTop: 8,
              padding: "10px 0",
            }}
          >
            <MaterialIcon name="tune" size={20} color={COLORS.textSecondary} />
            <span style={{ fontSize: 14, color: COLORS.text, flex: 1 }}>
              Advanced
            </span>
            <MaterialIcon
              name="keyboard_arrow_down"
              size={22}
              color={COLORS.textSecondary}
            />
          </div>

          <ImSeparator />

          <ImButton
            text="Cut"
            variant="primary"
            icon="content_cut"
            fullWidth
            style={{
              padding: "12px 20px",
              transform: `scale(${cutPress})`,
            }}
          />
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
