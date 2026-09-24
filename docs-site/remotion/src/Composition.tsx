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
import { StoryboardScene } from "./scenes/StoryboardScene";
import { MediaPoolScene, UNTICKED } from "./scenes/MediaPoolScene";
import { ExportScene } from "./scenes/ExportScene";
import { GeneratingScene } from "./scenes/GeneratingScene";
import { CompleteScene } from "./scenes/CompleteScene";
import { OutputPreviewScene } from "./scenes/OutputPreviewScene";
import { CliScene } from "./scenes/CliScene";
import { RunsScene } from "./scenes/RunsScene";
import { SuggestionsScene } from "./scenes/SuggestionsScene";
import { RefusedScene } from "./scenes/RefusedScene";

const FADE = 15; // 0.5s
const SLIDE = 12; // 0.4s

// Scene durations (frames at 30fps). TransitionSeries overlaps each pair by the
// transition's length, so the video runs sum(D) - sum(transitions):
// 1679 - 183 = 1496 frames, which is TOTAL_FRAMES in theme.ts.
//
// The ceiling is demo-music.wav: 49.97 s, or 1499 frames. A demo that outruns
// its own track ends on an audible cut, which is why the terminal and the film
// tail are cut to the frame rather than rounded up.
//
// Every length is cut to the frame its own scene stops moving on.
const D = {
  title: 75, // 2.5s
  brief: 168, // 5.6s — open the type dropdown, pick, click Cut
  cutting: 176, // 5.9s — the phase rows, the bar with its count, the strip
  storyboard: 190, // 6.3s — the cut in the order it plays; then Review the pool
  pool: 110, // 3.7s — untick one picture, Cut again
  storyboardAgain: 75, // 2.5s — the second cut, one picture fewer; click Export
  export: 100, // 3.3s — summary lands, cursor arrives, click Generate
  generating: 140, // 4.7s — progress + live preview
  complete: 60, // 2.0s — the finished file and its measured length
  runs: 90, // 3.0s — the completed run in the browser's history
  suggestions: 90, // 3.0s — what automation would propose next
  refused: 75, // 2.5s — a host without its models: the brief says why, and what to run
  // The recording runs 58.0s and CliScene starts it at 18.6s, so 118 frames at
  // 10x is the whole rest of it: one more and the terminal freezes on its last
  // line, one fewer and the `open` that ends it never arrives.
  cli: 118, // 3.9s — the real terminal at 10x: the bar, the cut, runs story, runs why
  output: 212, // 7.1s — the film it made, ending on its last picture, full bleed
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
        {/* 1. Title */}
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

        {/* 3. Cutting — phases advance, the bar counts, the strip fills */}
        <TransitionSeries.Sequence durationInFrames={D.cutting}>
          <CuttingScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 4. Storyboard — the cut in the order it plays, then Review the pool */}
        <TransitionSeries.Sequence durationInFrames={D.storyboard}>
          <StoryboardScene
            bassIntensity={bass}
            frames={D.storyboard}
            clickTarget="pool"
            clickAt={174}
            scrollPx={1520}
          />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 5. Media pool — untick one picture, Cut again */}
        <TransitionSeries.Sequence durationInFrames={D.pool}>
          <MediaPoolScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 6. Storyboard again — one picture fewer, then Export */}
        <TransitionSeries.Sequence durationInFrames={D.storyboardAgain}>
          <StoryboardScene
            bassIntensity={bass}
            without={[UNTICKED]}
            frames={D.storyboardAgain}
            clickTarget="export"
            clickAt={60}
            scrollPx={1430}
          />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 7. Export — summary, output path, click Generate */}
        <TransitionSeries.Sequence durationInFrames={D.export}>
          <ExportScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 8. Generating — progress + preview */}
        <TransitionSeries.Sequence durationInFrames={D.generating}>
          <GeneratingScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 9. Complete — finish the UI workflow before showing other features */}
        <TransitionSeries.Sequence durationInFrames={D.complete}>
          <CompleteScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 10. Runs — every run kept, with its cut and its timings */}
        <TransitionSeries.Sequence durationInFrames={D.runs}>
          <RunsScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={slide({ direction: "from-right" })}
          timing={linearTiming({ durationInFrames: SLIDE })}
        />

        {/* 11. Suggestions — what it would make next, on its own */}
        <TransitionSeries.Sequence durationInFrames={D.suggestions}>
          <SuggestionsScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 12. Refused — a cut on a host without models says why on the brief */}
        <TransitionSeries.Sequence durationInFrames={D.refused}>
          <RefusedScene bassIntensity={bass} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 13. CLI — its final `open` leads straight into the rendered film */}
        <TransitionSeries.Sequence durationInFrames={D.cli}>
          <CliScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: FADE })}
        />

        {/* 14. The film it made, last: full bleed, real time */}
        <TransitionSeries.Sequence durationInFrames={D.output}>
          <OutputPreviewScene frames={D.output} />
        </TransitionSeries.Sequence>
      </TransitionSeries>
    </AbsoluteFill>
  );
};
