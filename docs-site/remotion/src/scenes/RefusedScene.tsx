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

// What the brief shows after a Cut on a host where nobody ran `models fetch`:
// memory_run.install_refusal refuses before the pool loads, and the brief keeps
// the reason in a red card until the next Cut. The text is the real one, from
// preflight_run.run_blockers on a fresh install, with the home directory
// written the way the docs screenshots redact it.
const MODELS = "/home/user/.immich-memories/models";
const REFUSAL =
  "The last cut failed: Pinned DINOv2 export missing: public heads need the " +
  `pinned DINOv2 ONNX export at ${MODELS}/triage/dinov2-small.onnx. Run ` +
  "`immich-memories models fetch` to download it, or point " +
  "advanced.triage.encoder at your copy of the export.; Pinned " +
  "Marqo/nsfw-image-detection-384@0c26ec22/onnx-384 export missing: nsfw_marqo " +
  "has no model: Marqo/nsfw-image-detection-384@0c26ec22/onnx-384 is not at " +
  `${MODELS}/detectors/nsfw-marqo-384.onnx. Run \`immich-memories models ` +
  "fetch` to download it, or point advanced.editorial.preparation.marqo_onnx " +
  "at your copy of the export.";

// The dark theme's --im-error and --im-error-bg in ui/theme.py.
const ERROR_TEXT = "#f87171";
const ERROR_BG = "rgba(248,113,113,0.06)";

const CARD_IN = 6;
// The pointer settles on the command the card asks for.
const cursorSteps = [
  { frame: 30, x: CONTENT_X + 900, y: CONTENT_Y + 300 },
  { frame: 46, x: CONTENT_X + 1200, y: CONTENT_Y + 68 },
];

export const RefusedScene: React.FC<{ bassIntensity?: number }> = ({
  bassIntensity,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const reveal = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 120 },
    delay: CARD_IN,
  });
  const glow = interpolate(frame, [CARD_IN, CARD_IN + 10, 40], [0, 1, 0.35], {
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

          <div
            style={{
              width: "100%",
              borderRadius: 8,
              padding: "8px 12px",
              marginBottom: 12,
              backgroundColor: ERROR_BG,
              boxShadow: `0 0 0 1px rgba(248,113,113,${0.45 * glow})`,
              opacity: reveal,
              transform: `translateY(${(1 - reveal) * -8}px)`,
            }}
          >
            <span
              style={{
                fontSize: 14,
                lineHeight: 1.45,
                color: ERROR_TEXT,
                fontFamily,
              }}
            >
              {REFUSAL}
            </span>
          </div>

          <ImSectionHeader icon="auto_awesome" title="What is it about" />
          <ImSelect
            label="Memory type"
            value="Monthly Highlights"
            style={{ width: 288 }}
          />
          <ImCard style={{ marginTop: 12 }}>
            <BriefParams />
          </ImCard>
          <BriefFooter />
        </div>
      </WindowFrame>
      <AnimatedCursor steps={cursorSteps} />
    </AbsoluteFill>
  );
};
