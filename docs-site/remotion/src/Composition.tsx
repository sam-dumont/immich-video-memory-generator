import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import { staticFile, interpolate } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { COLORS, BEATS, FPS } from "./theme";
import { useBassIntensity } from "./hooks/useBassIntensity";
import { SceneLabel } from "./components/SceneLabel";
import { TitleScene } from "./scenes/TitleScene";
import { ConfigScene } from "./scenes/ConfigScene";
import { GenerateMemoriesScene } from "./scenes/GenerateMemoriesScene";
import { PipelineScene } from "./scenes/PipelineScene";
import { RefineScene } from "./scenes/RefineScene";
import { OptionsScene } from "./scenes/OptionsScene";
import { GeneratingScene } from "./scenes/GeneratingScene";
import { CompleteScene } from "./scenes/CompleteScene";
import { CliScene } from "./scenes/CliScene";
import { OutroScene } from "./scenes/OutroScene";

const FADE = Math.round(FPS * 0.5);
const SLIDE = Math.round(FPS * 0.4);

export const DemoVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const bass = useBassIntensity(frame);

  // Music volume: full until outro, then fade out
  const musicVolume = (f: number) =>
    interpolate(f, [BEATS.bar15, BEATS.end], [0.7, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Audio src={staticFile("demo-music.wav")} volume={musicVolume} />

      <TransitionSeries>
        {/* 1. Title (0 → 5.3s) */}
        <TransitionSeries.Sequence durationInFrames={BEATS.bar3}>
          <TitleScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 2. Step 1: Config (5.3 → 10.3s) */}
        <TransitionSeries.Sequence
          durationInFrames={BEATS.bar5 - BEATS.bar3}
        >
          <ConfigScene bassIntensity={bass} />
          <SceneLabel text="Step 1 · Configuration" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 3. Step 2: Generate Memories (10.3 → 14.5s) */}
        <TransitionSeries.Sequence
          durationInFrames={Math.round(4.2 * FPS)}
        >
          <GenerateMemoriesScene bassIntensity={bass} />
          <SceneLabel text="Step 2 · Generate Memories" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 4. Step 2: Pipeline running + complete (14.5 → 19.5s) */}
        <TransitionSeries.Sequence
          durationInFrames={Math.round(5.0 * FPS)}
        >
          <PipelineScene bassIntensity={bass} />
          <SceneLabel text="Generating Memories" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 5. Step 2: Refine (19.5 → 23.0s) */}
        <TransitionSeries.Sequence
          durationInFrames={BEATS.bar10 - Math.round(19.5 * FPS)}
        >
          <RefineScene bassIntensity={bass} />
          <SceneLabel text="Review & Refine" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 6. Step 3: Options (23.0 → 25.5s) */}
        <TransitionSeries.Sequence
          durationInFrames={BEATS.bar11 - BEATS.bar10}
        >
          <OptionsScene bassIntensity={bass} />
          <SceneLabel text="Step 3 · Generation Options" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 7. Step 4: Generating (25.5 → 30.5s) */}
        <TransitionSeries.Sequence
          durationInFrames={BEATS.bar13 - BEATS.bar11}
        >
          <GeneratingScene bassIntensity={bass} />
          <SceneLabel text="Step 4 · Preview & Export" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 8. Step 4: Complete (30.5 → 33.5s) */}
        <TransitionSeries.Sequence
          durationInFrames={BEATS.bar14 - BEATS.bar13}
        >
          <CompleteScene bassIntensity={bass} />
          <SceneLabel text="Your memory is ready!" />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 9. CLI demo (33.5 → 38.5s) */}
        <TransitionSeries.Sequence
          durationInFrames={BEATS.bar15 - BEATS.bar14}
        >
          <CliScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 10. Outro (38.5 → 50s) */}
        <TransitionSeries.Sequence
          durationInFrames={BEATS.musicEnd - BEATS.bar15}
        >
          <OutroScene />
        </TransitionSeries.Sequence>
      </TransitionSeries>
    </AbsoluteFill>
  );
};
