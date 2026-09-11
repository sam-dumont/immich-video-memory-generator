import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Audio } from "@remotion/media";
import { staticFile, interpolate } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { COLORS, MUSIC_FADE_START, TOTAL_FRAMES } from "./theme";
import { useBassIntensity } from "./hooks/useBassIntensity";
import { TitleScene } from "./scenes/TitleScene";
import { BriefScene } from "./scenes/BriefScene";
import { CuttingScene } from "./scenes/CuttingScene";
import { StoryScene } from "./scenes/StoryScene";
import { ExportScene } from "./scenes/ExportScene";
import { GeneratingScene } from "./scenes/GeneratingScene";
import { CompleteScene } from "./scenes/CompleteScene";
import { OutputPreviewScene } from "./scenes/OutputPreviewScene";
import { CliScene } from "./scenes/CliScene";
import { OutroScene } from "./scenes/OutroScene";

const FADE = 15; // 0.5s
const SLIDE = 12; // 0.4s

// Scene durations (frames at 30fps). TransitionSeries overlaps each pair by the
// transition's length, so the video runs sum(D) - sum(transitions) = 1500 frames.
// Transitions below add up to 129; the D map adds up to 1629.
const D = {
  title: 90, // 3.0s — punchy, not lingering
  brief: 195, // 6.5s — open the type dropdown, pick, click Cut
  cutting: 195, // 6.5s — the five phase rows and the six stages of the edit
  story: 330, // 11.0s — the payoff: what the cut produced
  export: 135, // 4.5s — summary, output path, click Generate
  generating: 165, // 5.5s — progress + live preview
  complete: 60, // 2.0s — success state
  output: 264, // 8.8s — the ACTUAL output video; absorbs the frame remainder
  cli: 105, // 3.5s — 5x CLI playback
  outro: 90, // 3.0s — CTA, not lingering
};

export const DemoVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const bass = useBassIntensity(frame);

  // Music volume: fade out over the last 5 seconds.
  const musicVolume = (f: number) =>
    interpolate(f, [MUSIC_FADE_START, TOTAL_FRAMES], [0.7, 0], {
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

        {/* 2. Brief — pick a memory type, keep the auto duration, cut */}
        <TransitionSeries.Sequence durationInFrames={D.brief}>
          <BriefScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 3. Cutting — phases advance, the editor's own stage strings */}
        <TransitionSeries.Sequence durationInFrames={D.cutting}>
          <CuttingScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 4. Story — the thesis, its stories, the pictures each was granted */}
        <TransitionSeries.Sequence durationInFrames={D.story}>
          <StoryScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 5. Export — summary, output path, click Generate */}
        <TransitionSeries.Sequence durationInFrames={D.export}>
          <ExportScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 6. Generating — progress + preview */}
        <TransitionSeries.Sequence durationInFrames={D.generating}>
          <GeneratingScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 7. Complete — success! */}
        <TransitionSeries.Sequence durationInFrames={D.complete}>
          <CompleteScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 8. Output Preview — THE ACTUAL VIDEO at 5x (placeholder) */}
        <TransitionSeries.Sequence durationInFrames={D.output}>
          <OutputPreviewScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 9. CLI — terminal demo at 5x */}
        <TransitionSeries.Sequence durationInFrames={D.cli}>
          <CliScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 10. Outro — CTA */}
        <TransitionSeries.Sequence durationInFrames={D.outro}>
          <OutroScene />
        </TransitionSeries.Sequence>
      </TransitionSeries>
    </AbsoluteFill>
  );
};
