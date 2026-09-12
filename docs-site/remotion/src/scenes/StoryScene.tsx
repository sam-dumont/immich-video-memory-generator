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

type Carrier = {
  motion: boolean;
  seconds: string;
  taken: string;
  reason: string;
  /** The picture the reason is about, from the same CC0 library the tests use. */
  picture: string;
};

type Story = {
  title: string;
  /** The editor's own word; the badge shows WEIGHT_LABEL[weight] instead. */
  weight: "dominant" | "major" | "glimpse";
  pictures: string;
  day: string;
  purpose: string;
  carriers: Carrier[];
};

const THESIS =
  "A year that opens at a table in the garden, spends its best day of the " +
  "summer walking in the woods, and ends camped on a slope above a lake.";

const DURATION_LINE =
  "312 s of pictures and video selected for a 600 s memory, " +
  "540 s of it available for content — near target";

const STORIES: Story[] = [
  {
    title: "Lunch in the garden",
    weight: "dominant",
    pictures: "2 pictures",
    day: "2025-04-19",
    purpose: "Opens the year where most of it actually happened",
    carriers: [
      {
        motion: true,
        seconds: "5 s",
        taken: "Apr 19, 12:15",
        reason: "the table and chairs still out on the lawn",
        picture: "library/garden-table.jpg",
      },
      {
        motion: false,
        seconds: "4 s",
        taken: "Apr 19, 16:30",
        reason: "the cake with the candles still in it",
        picture: "library/garden-cake.jpg",
      },
    ],
  },
  {
    title: "The day in the woods",
    weight: "major",
    pictures: "2 pictures",
    day: "2025-07-02",
    purpose: "Carries the middle of the year, the one day spent well away from the house",
    carriers: [
      {
        motion: false,
        seconds: "4 s",
        taken: "Jul 02, 11:05",
        reason: "the hamper open on the checked cloth",
        picture: "library/woods-hamper.jpg",
      },
      {
        motion: true,
        seconds: "6 s",
        taken: "Jul 02, 15:40",
        reason: "the long green path back to the car",
        picture: "library/woods-path.jpg",
      },
    ],
  },
  {
    title: "Two nights by the lake",
    weight: "glimpse",
    pictures: "2 pictures",
    day: "2025-08-14",
    purpose: "Closes the year on the weekend everyone slept outside",
    carriers: [
      {
        motion: true,
        seconds: "5 s",
        taken: "Aug 14, 18:45",
        reason: "the tents pitched on the slope above the lake",
        picture: "library/lake-tents.jpg",
      },
      {
        motion: false,
        seconds: "4 s",
        taken: "Aug 16, 21:10",
        reason: "the last of the sun going down over the water",
        picture: "library/lake-sunset.jpg",
      },
    ],
  },
];

const WEIGHT_VARIANT = {
  dominant: "success",
  major: "success",
  glimpse: "warning",
} as const;

// Display only — the plan keeps the editor's word, which the Details row shows.
const WEIGHT_LABEL = {
  dominant: "Main story",
  major: "Important",
  glimpse: "Small moment",
} as const;

const SCROLL_START = 60;
const SCROLL_END = 218;
const SCROLL_PX = 400;
const CLICK_EXPORT = 268;
const SCENE_FRAMES = 285;

// Measured against a 1920x1080 still render, with the page scrolled to its end.
const EXPORT_XY = { x: CONTENT_X + 60, y: CONTENT_Y + 769 };

const cursorSteps = [
  { frame: 240, ...EXPORT_XY },
  { frame: CLICK_EXPORT, ...EXPORT_XY, click: true },
];

/** A thumbnail that keeps drifting: the page is alive even while nothing is clicked. */
const CarrierRow: React.FC<{ carrier: Carrier; drift: number }> = ({
  carrier,
  drift,
}) => (
  <div style={{ display: "flex", alignItems: "flex-start", gap: 14, marginTop: 12 }}>
    <div
      style={{
        width: 128,
        height: 72,
        borderRadius: 6,
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      <Img
        src={staticFile(carrier.picture)}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${1.06 + drift * 0.06})`,
        }}
      />
    </div>
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
      </div>
      <span style={{ fontSize: 14, color: COLORS.text }}>{carrier.reason}</span>
    </div>
  </div>
);

/** Collapsed, as the page opens it: the editor's weight and standings are one click away. */
const DetailsRow: React.FC = () => (
  <div
    style={{
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      marginTop: 12,
      fontSize: 12,
      color: COLORS.textSecondary,
    }}
  >
    <span>Details</span>
    <MaterialIcon name="expand_more" size={18} color={COLORS.textSecondary} />
  </div>
);

const StoryCard: React.FC<{ story: Story; reveal: number; drift: number }> = ({
  story,
  reveal,
  drift,
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
      <ImBadge
        text={WEIGHT_LABEL[story.weight]}
        variant={WEIGHT_VARIANT[story.weight]}
      />
      <span style={{ fontSize: 12, color: COLORS.textSecondary }}>
        {story.pictures}
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
      <CarrierRow key={carrier.taken} carrier={carrier} drift={drift} />
    ))}
    <DetailsRow />
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
  // Every thumbnail creeps for the whole scene, so the longest hold in the demo
  // still has something moving in it.
  const drift = interpolate(frame, [0, SCENE_FRAMES], [0, 1], {
    extrapolateRight: "clamp",
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
                drift={drift}
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
