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
import { PageHeader, CONTENT_X, CONTENT_Y } from "../components/PageHeader";
import { ImCard } from "../components/ImCard";
import { ImBadge } from "../components/ImBadge";
import { ImButton } from "../components/ImButton";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { MaterialIcon } from "../components/MaterialIcon";
import { AnimatedCursor } from "../components/AnimatedCursor";
import { CUT_COUNT, CUT_SECONDS, POOL, POOL_TOTAL, POOL_VIDEOS } from "../fixture";

/**
 * The media pool after a cut, as the app draws it: one counters line, one
 * sentence on what a tick means, Cut again as the only primary action, and the
 * pool itself. The cursor unticks one picture and presses Cut again.
 */

// The first page of the pool, twenty pictures in capture order, from the fixture.
const CARDS = POOL;

/** Which card the cursor unticks: the first picture of the month, which the cut kept.
 * The storyboard's second visit leaves the same picture out. */
export const UNTICKED = 0;

const UNTICK_AT = 52;
const CLICK_CUT_AGAIN = 92;

// Five cards across, the way the real page lays its first page out.
const CARD_W = 214;
const CARD_GAP = 14;
// Measured against a 1920x1080 still render: the Include box of the first
// card, and the middle of the Cut again button.
const CHECKBOX_XY = { x: CONTENT_X + 22, y: CONTENT_Y + 486 };
const CUT_AGAIN_XY = { x: CONTENT_X + 71, y: CONTENT_Y + 134 };

const cursorSteps = [
  { frame: 22, ...CHECKBOX_XY },
  { frame: UNTICK_AT, ...CHECKBOX_XY, click: true },
  { frame: 74, ...CUT_AGAIN_XY },
  { frame: CLICK_CUT_AGAIN, ...CUT_AGAIN_XY, click: true },
];

const Checkbox: React.FC<{ checked: boolean }> = ({ checked }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
    <div
      style={{
        width: 18,
        height: 18,
        borderRadius: 4,
        border: `2px solid ${checked ? COLORS.primary : COLORS.textMuted}`,
        backgroundColor: checked ? COLORS.primary : "transparent",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {checked && <MaterialIcon name="check" size={14} color={COLORS.bg} />}
    </div>
    <span style={{ fontSize: 13, color: COLORS.text }}>Include</span>
  </div>
);

const PoolCard: React.FC<{
  card: (typeof CARDS)[number];
  checked: boolean;
  reveal: number;
}> = ({ card, checked, reveal }) => (
  <ImCard
    style={{
      width: CARD_W,
      padding: 10,
      opacity: reveal,
      transform: `translateY(${(1 - reveal) * 10}px)`,
    }}
  >
    <div style={{ width: "100%", height: 112, borderRadius: 6, overflow: "hidden" }}>
      <Img
        src={staticFile(card.picture)}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    </div>
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10, height: 20 }}>
      <ImBadge text={card.motion ? "Video" : "Photo"} variant="analysis" />
      {card.favourite && <MaterialIcon name="star" size={14} color={COLORS.warning} />}
    </div>
    <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.text, marginTop: 8 }}>
      {card.taken}
    </div>
    {card.motion && (
      <div style={{ fontSize: 11, color: COLORS.textSecondary, marginTop: 4 }}>
        &#9201; 0:0{card.seconds}
      </div>
    )}
    <div style={{ fontSize: 11, color: COLORS.textSecondary, marginTop: 6 }}>{card.file}</div>
    {/* What the saved cut did with this picture, from the same evidence as `runs why` */}
    <div
      style={{
        fontSize: 11,
        lineHeight: 1.35,
        color: COLORS.textSecondary,
        marginTop: 8,
        height: 46,
        overflow: "hidden",
      }}
    >
      {card.outcome}
    </div>
    <Checkbox checked={checked} />
  </ImCard>
);

type Props = { bassIntensity?: number };

export const MediaPoolScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const reveal = (delay: number) =>
    spring({ frame, fps, config: { damping: 20, stiffness: 150 }, delay });

  const unticked = frame >= UNTICK_AT + 6;
  const ticked = unticked ? CUT_COUNT - 1 : CUT_COUNT;
  // The cut itself does not change until Cut again runs: only the tick count moves.
  const cutLabel = `${Math.floor(CUT_SECONDS / 60)}:${String(CUT_SECONDS % 60).padStart(2, "0")}`;
  const videos = POOL_VIDEOS;

  const pressed = interpolate(frame, [CLICK_CUT_AGAIN, CLICK_CUT_AGAIN + 4], [1, 0.97], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar active="Media pool" />
        <div
          style={{
            flex: 1,
            padding: "20px 32px",
            overflow: "hidden",
            fontFamily,
            position: "relative",
          }}
        >
          <PageHeader title="Media pool" />

          <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.text, opacity: reveal(2) }}>
            {POOL_TOTAL} in the pool ({videos} videos, {POOL_TOTAL - videos} photos) · {ticked}{" "}
            ticked · {CUT_COUNT} in the cut, {cutLabel}
          </div>
          <div
            style={{ fontSize: 13, color: COLORS.textSecondary, marginTop: 10, opacity: reveal(4) }}
          >
            Ticked pictures are in the next cut, unticked ones are out. Cut again applies it.
          </div>

          <div style={{ display: "flex", gap: 16, marginTop: 18, opacity: reveal(8) }}>
            <div style={{ transform: `scale(${pressed})` }}>
              <ImButton text="Cut again" variant="primary" icon="refresh" />
            </div>
            <ImButton text="Trim the video clips" variant="secondary" icon="edit" />
            <ImButton text="Start over" variant="ghost" icon="restart_alt" />
          </div>

          <div style={{ marginTop: 26, opacity: reveal(12) }}>
            <ImSectionHeader icon="video_library" title="The pool: Jun 01, 2024 - Jun 30, 2024" />
          </div>

          {/* Compact grid / detailed list, the list selected */}
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 4,
              opacity: reveal(12),
            }}
          >
            {[
              { icon: "grid_view", selected: false },
              { icon: "view_list", selected: true },
            ].map((view) => (
              <div
                key={view.icon}
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: 6,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: view.selected ? COLORS.primary : "transparent",
                }}
              >
                <MaterialIcon
                  name={view.icon}
                  size={18}
                  color={view.selected ? "white" : COLORS.primary}
                />
              </div>
            ))}
          </div>

          <div style={{ display: "flex", gap: CARD_GAP, marginTop: 12, flexWrap: "wrap" }}>
            {CARDS.map((card, i) => (
              <PoolCard
                key={card.picture}
                card={card}
                checked={card.ticked && !(unticked && i === UNTICKED)}
                reveal={reveal(14 + i * 3)}
              />
            ))}
          </div>

          <div
            style={{
              position: "absolute",
              left: 32,
              bottom: 20,
              display: "flex",
              gap: 16,
              opacity: reveal(24),
            }}
          >
            <ImButton text="Back to the brief" variant="secondary" icon="arrow_back" />
            <ImButton text="Back to the cut" variant="ghost" icon="view_timeline" />
          </div>
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
