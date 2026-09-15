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
import { ImSelect } from "../components/ImSelect";

/**
 * The Runs page: every run the server has made, manual or automatic, kept with
 * its cut and its timings. It is where `runs why` reads from, so it follows the
 * terminal scene.
 */

const RUNS = [
  {
    id: "20260915_023320_d1f0",
    meta: "2026-09-15 00:33 · completed · auto",
    type: "monthly highlights",
    dates: "2024-06-01 to 2024-06-30",
  },
  {
    id: "20260914_191204_7ab3",
    meta: "2026-09-14 19:12 · completed · manual",
    type: "trip",
    dates: "2024-06-21 to 2024-06-27",
  },
  {
    id: "20260914_084417_2c9e",
    meta: "2026-09-14 08:44 · failed · scheduled",
    type: "person spotlight",
    dates: "2023-01-01 to 2023-12-31",
  },
];

const Row: React.FC<{ run: (typeof RUNS)[number]; reveal: number }> = ({
  run,
  reveal,
}) => (
  <div
    style={{
      backgroundColor: COLORS.elevated,
      border: `1px solid ${COLORS.borderLight}`,
      borderRadius: 8,
      padding: "14px 18px",
      marginBottom: 12,
      opacity: reveal,
      transform: `translateY(${(1 - reveal) * 12}px)`,
    }}
  >
    <div
      style={{
        fontSize: 15,
        color: COLORS.primary,
        textDecoration: "underline",
      }}
    >
      {run.id}
    </div>
    <div style={{ fontSize: 14, color: COLORS.text, marginTop: 8 }}>{run.meta}</div>
    <div style={{ fontSize: 14, color: COLORS.text, marginTop: 8 }}>{run.type}</div>
    <div style={{ fontSize: 14, color: COLORS.text, marginTop: 8 }}>{run.dates}</div>
  </div>
);

type Props = { bassIntensity?: number };

export const RunsScene: React.FC<Props> = ({ bassIntensity }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const reveal = (delay: number) =>
    spring({ frame, fps, config: { damping: 20, stiffness: 140 }, delay });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <WindowFrame bassIntensity={bassIntensity}>
        <Sidebar active="Runs" />
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
          <PageHeader title="Runs" />

          <div style={{ fontSize: 15, color: COLORS.text, opacity: reveal(2) }}>
            Manual and automatic runs, including failures. Open a run to read its cut
            and timings.
          </div>

          <div style={{ marginTop: 14, opacity: reveal(5) }}>
            <ImSelect label="Status" value="all" style={{ width: 120 }} small />
          </div>

          <div style={{ marginTop: 20 }}>
            {RUNS.map((run, i) => (
              <Row key={run.id} run={run} reveal={reveal(10 + i * 7)} />
            ))}
          </div>

          <div style={{ display: "flex", gap: 12, marginTop: 6, opacity: reveal(30) }}>
            <ImButton text="Previous runs" variant="primary" disabled />
            <ImButton text="Next runs" variant="primary" disabled />
            <ImButton text="Refresh runs" variant="primary" />
          </div>
        </div>
      </WindowFrame>
    </AbsoluteFill>
  );
};
