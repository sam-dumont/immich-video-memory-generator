import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import { staticFile, interpolate } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { COLORS, FPS } from "./theme";
import { useBassIntensity } from "./hooks/useBassIntensity";
import { TitleScene } from "./scenes/TitleScene";
import { ConfigScene } from "./scenes/ConfigScene";
import { GenerateMemoriesScene } from "./scenes/GenerateMemoriesScene";
import { PipelineScene } from "./scenes/PipelineScene";
import { RefineScene } from "./scenes/RefineScene";
import { OptionsScene } from "./scenes/OptionsScene";
import { GeneratingScene } from "./scenes/GeneratingScene";
import { CompleteScene } from "./scenes/CompleteScene";
import { OutputPreviewScene } from "./scenes/OutputPreviewScene";
import { CliScene } from "./scenes/CliScene";
import { OutroScene } from "./scenes/OutroScene";

const FADE = 15; // 0.5s
const SLIDE = 12; // 0.4s

// Scene durations (frames at 30fps)
// Total sequences: 1680, transitions: 10×15 = 150, effective: ~1530 frames ≈ 51s
// TransitionSeries handles the math — we target 1500 frames total
const D = {
  title: 90, // 3s — punchy, not lingering
  config: 180, // 6s — dropdown interaction
  genMem: 90, // 3s — show page, click button
  pipeline: 180, // 6s — running + complete
  refine: 200, // 6.7s — expand clips, scroll, click
  options: 110, // 3.7s — expand advanced, click next
  generating: 180, // 6s — click, progress, Ken Burns preview
  complete: 70, // 2.3s — success state
  output: 360, // 12s — the ACTUAL output video (placeholder)
  cli: 120, // 4s — 5x CLI playback
  outro: 100, // 3.3s — CTA, not lingering
};

export const DemoVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const bass = useBassIntensity(frame);

  // Music volume: fade out in last 5 seconds
  const musicVolume = (f: number) =>
    interpolate(f, [1350, 1500], [0.7, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Audio src={staticFile("demo-music.wav")} volume={musicVolume} />

      <TransitionSeries>
        {/* 1. Title — 3s, punchy */}
        <TransitionSeries.Sequence durationInFrames={D.title}>
          <TitleScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 2. Config — select person, click next */}
        <TransitionSeries.Sequence durationInFrames={D.config}>
          <ConfigScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 3. Generate Memories — click the magic button */}
        <TransitionSeries.Sequence durationInFrames={D.genMem}>
          <GenerateMemoriesScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 4. Pipeline — analysis running → complete */}
        <TransitionSeries.Sequence durationInFrames={D.pipeline}>
          <PipelineScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 5. Refine — expand all clips, scroll, click continue */}
        <TransitionSeries.Sequence durationInFrames={D.refine}>
          <RefineScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 6. Options — expand advanced, click next */}
        <TransitionSeries.Sequence durationInFrames={D.options}>
          <OptionsScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 7. Generating — click generate, progress + preview */}
        <TransitionSeries.Sequence durationInFrames={D.generating}>
          <GeneratingScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 8. Complete — success! */}
        <TransitionSeries.Sequence durationInFrames={D.complete}>
          <CompleteScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 9. Output Preview — THE ACTUAL VIDEO at 5x (placeholder) */}
        <TransitionSeries.Sequence durationInFrames={D.output}>
          <OutputPreviewScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 10. CLI — terminal demo at 5x */}
        <TransitionSeries.Sequence durationInFrames={D.cli}>
          <CliScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 11. Outro — CTA */}
        <TransitionSeries.Sequence durationInFrames={D.outro}>
          <OutroScene />
        </TransitionSeries.Sequence>
      </TransitionSeries>
    </AbsoluteFill>
  );
};
