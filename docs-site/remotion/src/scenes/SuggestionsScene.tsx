import React from "react";
import {
  AbsoluteFill,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS } from "../theme";
import { fontFamily } from "../fonts";
import { WindowFrame } from "../components/WindowFrame";
import { Sidebar } from "../components/Sidebar";
import { PageHeader } from "../components/PageHeader";
import { ImButton } from "../components/ImButton";
import { MaterialIcon } from "../components/MaterialIcon";
import { POOL_TOTAL } from "../fixture";

/**
 * The Suggestions page as the app draws it: what automation would make next,
 * by the same rules the scheduled run uses. The first candidate is the month
 * the demo just cut by hand, which is the point of the scene. The cast (Robin,
 * Charlie, Kit) is the fixture library's, not anybody's.
 */

const CANDIDATES = [
  {
    reason: `${POOL_TOTAL} assets, most recent month`,
    people: "",
    meta: `2024-06-01 to 2024-06-30 · ${POOL_TOTAL} pictures · monthly review`,
    key: "monthly_highlights:2024-06-01:2024-06-30:",
  },
  {
    reason: "1st most featured person, 18 assets",
    people: "Kit",
    meta: "2023-01-01 to 2023-12-31 · 18 pictures · person spotlight",
    key: "",
  },
  {
    reason: "2nd most featured person, 14 assets",
    people: "Robin",
    meta: "2023-01-01 to 2023-12-31 · 14 pictures · person spotlight",
    key: "",
  },
];

const Card: React.FC<{
  candidate: (typeof CANDIDATES)[number];
  reveal: number;
}> = ({ candidate, reveal }) => (
  <div
    style={{
      backgroundColor: COLORS.elevated,
      border: `1px solid ${COLORS.borderLight}`,
      borderRadius: 8,
      padding: "16px 18px",
      marginBottom: 14,
      opacity: reveal,
      transform: `translateY(${(1 - reveal) * 12}px)`,
    }}
  >
    <div style={{ fontSize: 18, fontWeight: 600, color: COLORS.text }}>
      {candidate.reason}
    </div>
    {candidate.people && (
      <div style={{ fontSize: 14, fontWeight: 500, color: COLORS.text, marginTop: 6 }}>
        {candidate.people}
      </div>
    )}
    <div style={{ fontSize: 14, color: COLORS.text, marginTop: 6 }}>
      {candidate.meta}
    </div>

    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        marginTop: 12,
        paddingRight: 4,
      }}
    >
      <span style={{ fontSize: 14, color: COLORS.text, flex: 1 }}>
        Candidate key
      </span>
      <MaterialIcon
        name={candidate.key ? "keyboard_arrow_up" : "keyboard_arrow_down"}
        size={22}
        color={COLORS.textSecondary}
      />
    </div>
    {candidate.key && (
      <div
        style={{
          fontSize: 12,
          fontFamily: "ui-monospace, Menlo, monospace",
          color: COLORS.textSecondary,
          marginTop: 8,
          marginBottom: 4,
        }}
      >
        {candidate.key}
      </div>
    )}

    <div style={{ display: "flex", gap: 12, marginTop: 14 }}>
      <ImButton text="Check eligibility" variant="primary" />
      <ImButton text="Run this suggestion" variant="primary" />
    </div>
  </div>
);

type Props = { bassIntensity?: number };

export const SuggestionsScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const reveal = (delay: number) =>
    spring({ frame, fps, config: { damping: 20, stiffness: 140 }, delay });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar active="Suggestions" />
        <div
          style={{
            flex: 1,
            padding: "20px 32px",
            overflow: "hidden",
            fontFamily,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <PageHeader title="Suggestions" />

          <div style={{ fontSize: 15, color: COLORS.text, opacity: reveal(2) }}>
            Memories automation would make next, using the same rules as auto suggest.
          </div>
          <div
            style={{
              fontSize: 14,
              color: COLORS.text,
              marginTop: 10,
              opacity: reveal(4),
            }}
          >
            Running a suggestion creates a video on the server. Automatic upload to
            Immich is off.
          </div>

          <div style={{ marginTop: 16, opacity: reveal(8) }}>
            <ImButton text="Refresh suggestions" variant="primary" />
          </div>

          <div style={{ marginTop: 22 }}>
            {CANDIDATES.map((candidate, i) => (
              <Card
                key={candidate.reason}
                candidate={candidate}
                reveal={reveal(14 + i * 8)}
              />
            ))}
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
