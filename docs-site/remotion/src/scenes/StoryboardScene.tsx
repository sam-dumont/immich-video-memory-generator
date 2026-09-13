import React from "react";
import {
  AbsoluteFill,
  Easing,
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
import { ImSeparator } from "../components/ImSeparator";
import { MaterialIcon } from "../components/MaterialIcon";
import { AnimatedCursor } from "../components/AnimatedCursor";

/**
 * The result page as the app draws it since the storyboard landed: the thesis
 * once, two tabs (Storyboard, Story), and the cut in the order it plays, one
 * card per picture with its timecode, day, kind, story and the reader's reason.
 */

export type Shot = {
  /** The same six pictures the hermetic fixture serves. */
  picture: string;
  day: string;
  motion: boolean;
  seconds: number;
  story: string;
  reason: string;
  /** Set on the first picture of a month: the real page prints a chapter label. */
  chapter?: string;
};

export const THESIS =
  "A year that opens at a table in the garden, spends its best day of the " +
  "summer walking in the woods, and ends camped on a slope above a lake.";

export const SHOTS: Shot[] = [
  {
    picture: "library/garden-table.jpg",
    day: "2025-04-19",
    motion: true,
    seconds: 5,
    story: "Lunch in the garden",
    reason: "the table and chairs still out on the lawn",
    chapter: "April 2025",
  },
  {
    picture: "library/garden-cake.jpg",
    day: "2025-04-19",
    motion: false,
    seconds: 4,
    story: "Lunch in the garden",
    reason: "the cake with the candles still in it",
  },
  {
    picture: "library/woods-hamper.jpg",
    day: "2025-07-02",
    motion: false,
    seconds: 4,
    story: "The day in the woods",
    reason: "the hamper open on the checked cloth",
    chapter: "July 2025",
  },
  {
    picture: "library/woods-path.jpg",
    day: "2025-07-02",
    motion: true,
    seconds: 6,
    story: "The day in the woods",
    reason: "the long green path back to the car",
  },
  {
    picture: "library/lake-tents.jpg",
    day: "2025-08-14",
    motion: true,
    seconds: 5,
    story: "Two nights by the lake",
    reason: "the tents pitched on the slope above the lake",
    chapter: "August 2025",
  },
  {
    picture: "library/lake-sunset.jpg",
    day: "2025-08-16",
    motion: false,
    seconds: 4,
    story: "Two nights by the lake",
    reason: "the last of the sun going down over the water",
  },
];

const timecode = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

const contentLabel = (shots: Shot[]) => {
  const total = shots.reduce((sum, s) => sum + s.seconds, 0);
  return `${shots.length} pictures, ${timecode(total)} of pictures and video`;
};

const Tabs: React.FC = () => (
  <div
    style={{
      display: "flex",
      justifyContent: "center",
      gap: 24,
      marginTop: 14,
      marginBottom: 18,
      borderBottom: `1px solid ${COLORS.border}`,
    }}
  >
    {[
      { icon: "view_timeline", label: "STORYBOARD", active: true },
      { icon: "auto_stories", label: "STORY", active: false },
    ].map((tab) => (
      <div
        key={tab.label}
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 4,
          padding: "6px 14px 8px",
          borderBottom: tab.active
            ? `2px solid ${COLORS.primary}`
            : "2px solid transparent",
          color: tab.active ? COLORS.text : COLORS.textSecondary,
          fontSize: 13,
          fontWeight: 600,
          letterSpacing: 0.4,
        }}
      >
        <MaterialIcon
          name={tab.icon}
          size={20}
          color={tab.active ? COLORS.text : COLORS.textSecondary}
        />
        {tab.label}
      </div>
    ))}
  </div>
);

const ShotRow: React.FC<{
  shot: Shot;
  at: number;
  reveal: number;
  drift: number;
}> = ({ shot, at, reveal, drift }) => (
  <ImCard
    style={{
      padding: "8px 10px",
      marginBottom: 8,
      opacity: reveal,
      transform: `translateY(${(1 - reveal) * 12}px)`,
    }}
  >
    <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
      <span
        style={{
          fontSize: 12,
          fontFamily: "ui-monospace, Menlo, monospace",
          color: COLORS.textSecondary,
          width: 40,
        }}
      >
        {timecode(at)}
      </span>
      <div
        style={{
          width: 96,
          height: 54,
          borderRadius: 6,
          overflow: "hidden",
          flexShrink: 0,
        }}
      >
        <Img
          src={staticFile(shot.picture)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: `scale(${1.06 + drift * 0.06})`,
          }}
        />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: COLORS.text }}>
            {shot.day}
          </span>
          <ImBadge
            text={shot.motion ? "Video" : "Still"}
            variant={shot.motion ? "analysis" : "info"}
          />
          <span style={{ fontSize: 12, color: COLORS.textSecondary }}>
            {shot.seconds} s
          </span>
        </div>
        <span style={{ fontSize: 12, color: COLORS.primary }}>{shot.story}</span>
        <span style={{ fontSize: 12, color: COLORS.text }}>{shot.reason}</span>
      </div>
    </div>
  </ImCard>
);

type Props = {
  bassIntensity?: number;
  /** Pictures to leave out, by index, for the second cut after a tick was removed. */
  without?: number[];
  /** Total scene length in frames, for the scroll and the drift. */
  frames: number;
  /** Where the cursor ends up: the pool button on the first visit, Export on the last. */
  clickTarget: "pool" | "export";
  /** Frame of the click. */
  clickAt: number;
  /** How far the page scrolls before the click. */
  scrollPx: number;
};

// Measured against a 1920x1080 still render with the page scrolled to its end.
const EXPORT_XY = { x: CONTENT_X + 60, y: CONTENT_Y + 546 };
const POOL_XY = { x: CONTENT_X + 235, y: CONTENT_Y + 598 };

export const StoryboardScene: React.FC<Props> = ({
  bassIntensity,
  without = [],
  frames,
  clickTarget,
  clickAt,
  scrollPx,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const shots = SHOTS.filter((_, i) => !without.includes(i));

  const reveal = (delay: number) =>
    spring({ frame, fps, config: { damping: 20, stiffness: 130 }, delay });

  const scrollY = interpolate(
    frame,
    [Math.round(frames * 0.25), Math.round(frames * 0.7)],
    [0, scrollPx],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.inOut(Easing.cubic),
    },
  );
  const drift = interpolate(frame, [0, frames], [0, 1], { extrapolateRight: "clamp" });

  const target = clickTarget === "pool" ? POOL_XY : EXPORT_XY;
  const cursorSteps = [
    { frame: clickAt - 28, ...target },
    { frame: clickAt, ...target, click: true },
  ];

  let at = 0;
  let previousChapter: string | undefined;

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar activeStep={1} completedSteps={[2]} />
        <div
          style={{
            flex: 1,
            padding: "20px 32px",
            overflow: "hidden",
            fontFamily,
            position: "relative",
          }}
        >
          <div style={{ transform: `translateY(${-scrollY}px)` }}>
            <PageHeader title="Memory" />

            <div
              style={{
                fontSize: 19,
                lineHeight: 1.5,
                color: COLORS.text,
                maxWidth: 1240,
                opacity: reveal(4),
              }}
            >
              {THESIS}
            </div>

            <Tabs />

            <div style={{ opacity: reveal(12) }}>
              <ImSectionHeader icon="view_timeline" title="The storyboard" />
              <ImSectionHeader icon="movie" title={contentLabel(shots)} />
              <div
                style={{ fontSize: 12, color: COLORS.textSecondary, marginTop: -6, marginBottom: 10 }}
              >
                Titles and transitions make up the rest of the film.
              </div>
            </div>

            {shots.map((shot, i) => {
              const rowAt = at;
              at += shot.seconds;
              const chapter =
                shot.chapter && shot.chapter !== previousChapter ? shot.chapter : undefined;
              if (shot.chapter) previousChapter = shot.chapter;
              return (
                <React.Fragment key={shot.picture}>
                  {chapter && (
                    <div
                      style={{
                        fontSize: 14,
                        fontWeight: 600,
                        color: COLORS.textSecondary,
                        margin: "10px 0 8px",
                        opacity: reveal(16 + i * 5),
                      }}
                    >
                      {chapter}
                    </div>
                  )}
                  <ShotRow shot={shot} at={rowAt} reveal={reveal(18 + i * 5)} drift={drift} />
                </React.Fragment>
              );
            })}

            <ImSeparator />

            <div style={{ display: "flex", gap: 16 }}>
              <ImButton text="Export" variant="primary" icon="movie" />
              <ImButton text="Review the pool" variant="secondary" icon="video_library" />
              <ImButton text="Cut again" variant="secondary" icon="refresh" />
              <ImButton text="Change the brief" variant="ghost" icon="edit" />
            </div>
          </div>
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
