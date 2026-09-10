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
import { PageHeader, CONTENT_X, CONTENT_Y } from "../components/PageHeader";
import { ImCard } from "../components/ImCard";
import { ImBadge } from "../components/ImBadge";
import { ImButton } from "../components/ImButton";
import { ImSectionHeader } from "../components/ImSectionHeader";
import { ImSeparator } from "../components/ImSeparator";
import { AnimatedCursor } from "../components/AnimatedCursor";

type Carrier = {
  motion: boolean;
  seconds: string;
  taken: string;
  standing: "remarkable" | "maybe";
  reason: string;
  /** Soft colour block standing in for the thumbnail — never a real picture. */
  tint: string;
};

type Story = {
  title: string;
  weight: "dominant" | "major" | "glimpse";
  granted: string;
  day: string;
  purpose: string;
  carriers: Carrier[];
};

const THESIS =
  "A year that starts in the garden, empties into a move halfway through, " +
  "and comes back together on the coast in August with everyone in the same frame again.";

const DURATION_LINE =
  "312 s of pictures and video selected for a 600 s memory, " +
  "540 s of it available for content — near target";

const STORIES: Story[] = [
  {
    title: "The garden through spring",
    weight: "dominant",
    granted: "2 granted",
    day: "2025-04-19",
    purpose: "Opens the year where most of it actually happened",
    carriers: [
      {
        motion: true,
        seconds: "5 s",
        taken: "Apr 19, 10:15",
        standing: "remarkable",
        reason: "the only capture of the morning",
        tint: "linear-gradient(135deg, #4f6f52 0%, #86a789 55%, #d2e3c8 100%)",
      },
      {
        motion: false,
        seconds: "4 s",
        taken: "Apr 19, 17:40",
        standing: "maybe",
        reason: "the whole table in one frame",
        tint: "linear-gradient(135deg, #6b5b4b 0%, #b08968 60%, #ddb892 100%)",
      },
    ],
  },
  {
    title: "Moving week",
    weight: "major",
    granted: "2 granted",
    day: "2025-07-02",
    purpose: "Carries the middle of the year and explains the change of rooms",
    carriers: [
      {
        motion: false,
        seconds: "4 s",
        taken: "Jul 02, 08:05",
        standing: "remarkable",
        reason: "boxes to the ceiling and the room already empty behind them",
        tint: "linear-gradient(135deg, #3d405b 0%, #5c6378 55%, #9aa0b5 100%)",
      },
      {
        motion: true,
        seconds: "6 s",
        taken: "Jul 02, 15:22",
        standing: "maybe",
        reason: "the last look back down the hallway",
        tint: "linear-gradient(135deg, #2f3e46 0%, #52796f 60%, #84a98c 100%)",
      },
    ],
  },
  {
    title: "A week on the coast",
    weight: "glimpse",
    granted: "2 granted",
    day: "2025-08-14",
    purpose: "Closes the year on the one week everyone was in the same place",
    carriers: [
      {
        motion: true,
        seconds: "5 s",
        taken: "Aug 14, 19:05",
        standing: "remarkable",
        reason: "low sun, the whole group walking into it",
        tint: "linear-gradient(135deg, #e07a5f 0%, #f2cc8f 60%, #fdf0d5 100%)",
      },
      {
        motion: false,
        seconds: "4 s",
        taken: "Aug 16, 12:30",
        standing: "maybe",
        reason: "the only picture with all of them looking up",
        tint: "linear-gradient(135deg, #1d3557 0%, #457b9d 55%, #a8dadc 100%)",
      },
    ],
  },
];

const WEIGHT_VARIANT = {
  dominant: "success",
  major: "success",
  glimpse: "warning",
} as const;

const SCROLL_START = 72;
const SCROLL_END = 250;
const SCROLL_PX = 305;
const CLICK_EXPORT = 300;

// Measured against a 1920x1080 still render, with the page scrolled to its end.
const EXPORT_XY = { x: CONTENT_X + 60, y: CONTENT_Y + 769 };

const cursorSteps = [
  { frame: 282, ...EXPORT_XY },
  { frame: CLICK_EXPORT, ...EXPORT_XY, click: true },
];

const CarrierRow: React.FC<{ carrier: Carrier }> = ({ carrier }) => (
  <div style={{ display: "flex", alignItems: "flex-start", gap: 14, marginTop: 12 }}>
    <div
      style={{
        width: 128,
        height: 72,
        borderRadius: 6,
        background: carrier.tint,
        flexShrink: 0,
      }}
    />
    <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <ImBadge
          text={carrier.motion ? "Motion" : "Still"}
          variant={carrier.motion ? "analysis" : "info"}
        />
        <span style={{ fontSize: 12, color: COLORS.textSecondary }}>
          {carrier.seconds}
        </span>
        <span style={{ fontSize: 12, color: COLORS.textSecondary }}>
          {carrier.taken}
        </span>
        <ImBadge
          text={carrier.standing}
          variant={carrier.standing === "remarkable" ? "success" : "warning"}
        />
      </div>
      <span style={{ fontSize: 14, color: COLORS.text }}>{carrier.reason}</span>
    </div>
  </div>
);

const StoryCard: React.FC<{ story: Story; reveal: number }> = ({
  story,
  reveal,
}) => (
  <ImCard
    style={{
      padding: 16,
      marginBottom: 16,
      opacity: reveal,
      transform: `translateY(${(1 - reveal) * 16}px)`,
    }}
  >
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 16, fontWeight: 600, color: COLORS.text }}>
        {story.title}
      </span>
      <ImBadge text={story.weight} variant={WEIGHT_VARIANT[story.weight]} />
      <span style={{ fontSize: 12, color: COLORS.textSecondary }}>
        {story.granted}
      </span>
      <span style={{ fontSize: 12, color: COLORS.textSecondary }}>
        {story.day}
      </span>
    </div>
    <div
      style={{
        fontSize: 14,
        fontStyle: "italic",
        color: COLORS.textSecondary,
        marginTop: 6,
      }}
    >
      {story.purpose}
    </div>
    {story.carriers.map((carrier) => (
      <CarrierRow key={carrier.taken} carrier={carrier} />
    ))}
  </ImCard>
);

type Props = { bassIntensity?: number };

export const StoryScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const reveal = (delay: number) =>
    spring({ frame, fps, config: { damping: 20, stiffness: 130 }, delay });

  const thesisReveal = reveal(4);
  const headReveal = reveal(14);

  const scrollY = interpolate(frame, [SCROLL_START, SCROLL_END], [0, SCROLL_PX], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.cubic),
  });

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
                opacity: thesisReveal,
                transform: `translateY(${(1 - thesisReveal) * 12}px)`,
              }}
            >
              <ImSectionHeader icon="auto_stories" title="The story" />
              <div
                style={{
                  fontSize: 19,
                  lineHeight: 1.5,
                  color: COLORS.text,
                  maxWidth: 1240,
                }}
              >
                {THESIS}
              </div>
              <div
                style={{
                  fontSize: 14,
                  color: COLORS.textSecondary,
                  marginTop: 8,
                }}
              >
                {DURATION_LINE}
              </div>
            </div>

            <div
              style={{
                marginTop: 20,
                opacity: headReveal,
              }}
            >
              <ImSectionHeader icon="movie" title="3 stories, 6 pictures" />
            </div>

            {STORIES.map((story, i) => (
              <StoryCard
                key={story.title}
                story={story}
                reveal={reveal(20 + i * 8)}
              />
            ))}

            <ImSeparator />

            <div style={{ display: "flex", gap: 16 }}>
              <ImButton text="Export" variant="primary" icon="movie" />
              <ImButton
                text="Review the pool"
                variant="secondary"
                icon="video_library"
              />
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
