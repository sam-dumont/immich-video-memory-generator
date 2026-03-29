import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import { staticFile, interpolate } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { COLORS, FPS } from "./theme";
import { useBassIntensity } from "./hooks/useBassIntensity";
import { TitleScene } from "./scenes/TitleScene";
import { StepTitleCard } from "./scenes/StepTitleCard";
import { ConfigScene } from "./scenes/ConfigScene";
import { GenerateMemoriesScene } from "./scenes/GenerateMemoriesScene";
import { PipelineScene } from "./scenes/PipelineScene";
import { RefineScene } from "./scenes/RefineScene";
import { OptionsScene } from "./scenes/OptionsScene";
import { GeneratingScene } from "./scenes/GeneratingScene";
import { CompleteScene } from "./scenes/CompleteScene";
import { CliScene } from "./scenes/CliScene";
import { OutroScene } from "./scenes/OutroScene";

const FADE = Math.round(FPS * 0.5); // 15 frames
const SLIDE = Math.round(FPS * 0.4); // 12 frames
const TITLE_CARD = 30; // 1 second

export const DemoVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const bass = useBassIntensity(frame);

  // Music volume: fade out in last 8 seconds
  const musicVolume = (f: number) =>
    interpolate(f, [1260, 1500], [0.7, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Audio src={staticFile("demo-music.wav")} volume={musicVolume} />

      <TransitionSeries>
        {/* 1. Title (5s) */}
        <TransitionSeries.Sequence durationInFrames={150}>
          <TitleScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* Step 1 title card */}
        <TransitionSeries.Sequence durationInFrames={TITLE_CARD}>
          <StepTitleCard step={1} title="Configuration" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 2. ConfigScene — select Person Spotlight, click Next */}
        <TransitionSeries.Sequence durationInFrames={180}>
          <ConfigScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* Step 2 title card */}
        <TransitionSeries.Sequence durationInFrames={TITLE_CARD}>
          <StepTitleCard step={2} title="Clip Review" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 3. GenerateMemories — click Generate */}
        <TransitionSeries.Sequence durationInFrames={110}>
          <GenerateMemoriesScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 4. Pipeline running → complete */}
        <TransitionSeries.Sequence durationInFrames={180}>
          <PipelineScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 5. Refine — open all clips, scroll, click Continue */}
        <TransitionSeries.Sequence durationInFrames={220}>
          <RefineScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* Step 3 title card */}
        <TransitionSeries.Sequence durationInFrames={TITLE_CARD}>
          <StepTitleCard step={3} title="Generation Options" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 6. Options — expand advanced, click Next */}
        <TransitionSeries.Sequence durationInFrames={100}>
          <OptionsScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* Step 4 title card */}
        <TransitionSeries.Sequence durationInFrames={TITLE_CARD}>
          <StepTitleCard step={4} title="Preview & Export" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 7. Generating — click Generate Video, progress + frame preview */}
        <TransitionSeries.Sequence durationInFrames={200}>
          <GeneratingScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 8. Complete — success state */}
        <TransitionSeries.Sequence durationInFrames={75}>
          <CompleteScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 9. CLI demo — 5x playback */}
        <TransitionSeries.Sequence durationInFrames={160}>
          <CliScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 10. Outro */}
        <TransitionSeries.Sequence durationInFrames={180}>
          <OutroScene />
        </TransitionSeries.Sequence>
      </TransitionSeries>
    </AbsoluteFill>
  );
};
