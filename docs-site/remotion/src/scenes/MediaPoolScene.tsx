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
import { SHOTS } from "./StoryboardScene";

/**
 * The media pool after a cut, as the app draws it: one counters line, one
 * sentence on what a tick means, Cut again as the only primary action, and the
 * pool itself. The cursor unticks one picture and presses Cut again.
 */

// The pool card for each of the six fixture pictures, in capture order.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const takenLabel = (day: string, motion: boolean) =>
  `${MONTHS[Number(day.slice(5, 7)) - 1]} ${day.slice(8)} ${motion ? "12:15" : "16:30"}`;

const CARDS = SHOTS.map((shot) => ({
  picture: shot.picture,
  motion: shot.motion,
  taken: takenLabel(shot.day, shot.motion),
  file: `IMG_${2418 + SHOTS.indexOf(shot) * 137}.${shot.motion ? "mp4" : "jpg"}`,
  seconds: shot.seconds,
  favourite: SHOTS.indexOf(shot) === 0 || SHOTS.indexOf(shot) === 2,
}));

/** Which card the cursor unticks. The storyboard's second visit leaves it out. */
export const UNTICKED = 1;

const UNTICK_AT = 52;
const CLICK_CUT_AGAIN = 92;

const CARD_W = 108;
const CARD_GAP = 18;
// Measured against a 1920x1080 still render: the Include box of the second
// card, and the middle of the Cut again button.
const CHECKBOX_XY = { x: CONTENT_X + 146, y: CONTENT_Y + 370 };
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
    <div style={{ width: "100%", height: 52, borderRadius: 6, overflow: "hidden" }}>
      <Img
        src={staticFile(card.picture)}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
    </div>
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8, height: 18 }}>
      {!card.motion && <ImBadge text="Photo" variant="analysis" />}
      {card.favourite && <MaterialIcon name="star" size={14} color={COLORS.warning} />}
    </div>
    <div style={{ fontSize: 13, fontWeight: 600, color: COLORS.text, marginTop: 6 }}>
      {card.taken}
    </div>
    {card.motion && (
      <div style={{ fontSize: 11, color: COLORS.textSecondary, marginTop: 4 }}>
        ⏱ 0:0{card.seconds}
      </div>
    )}
    <div style={{ fontSize: 11, color: COLORS.textSecondary, marginTop: 4 }}>{card.file}</div>
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
  const ticked = unticked ? CARDS.length - 1 : CARDS.length;
  // The cut itself does not change until Cut again runs: only the tick count moves.
  const cutSeconds = CARDS.reduce((sum, c) => sum + c.seconds, 0);
  const cutLabel = `0:${String(cutSeconds).padStart(2, "0")}`;
  const videos = CARDS.filter((c) => c.motion).length;

  const pressed = interpolate(frame, [CLICK_CUT_AGAIN, CLICK_CUT_AGAIN + 4], [1, 0.97], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={2} completedSteps={[1]} />
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
            {CARDS.length} in the pool ({videos} videos, {CARDS.length - videos} photos) · {ticked}{" "}
            ticked · {CARDS.length} in the cut, {cutLabel}
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
            <ImSectionHeader icon="video_library" title="The pool: Jan 01, 2025 - Dec 31, 2025" />
          </div>

          <div style={{ display: "flex", gap: CARD_GAP, marginTop: 12, flexWrap: "wrap" }}>
            {CARDS.map((card, i) => (
              <PoolCard
                key={card.picture}
                card={card}
                checked={!(unticked && i === UNTICKED)}
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
