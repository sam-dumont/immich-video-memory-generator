import "./index.css";
import { Composition } from "remotion";
import { DemoVideo } from "./Composition";
import { BEATS, FPS } from "./theme";
import { TitleScene } from "./scenes/TitleScene";
import { ConfigScene } from "./scenes/ConfigScene";
import { GenerateMemoriesScene } from "./scenes/GenerateMemoriesScene";
import { PipelineScene } from "./scenes/PipelineScene";
import { RefineScene } from "./scenes/RefineScene";
import { OptionsScene } from "./scenes/OptionsScene";
import { GeneratingScene } from "./scenes/GeneratingScene";
import { CompleteScene } from "./scenes/CompleteScene";

export const RemotionRoot: React.FC = () => (
  <>
    {/* Full demo video */}
    <Composition
      id="DemoVideo"
      component={DemoVideo}
      durationInFrames={BEATS.musicEnd}
      fps={FPS}
      width={1920}
      height={1080}
    />
    {/* Individual scenes for preview in Remotion Studio */}
    <Composition
      id="TitleScene"
      component={TitleScene}
      durationInFrames={158}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="ConfigScene"
      component={ConfigScene}
      durationInFrames={150}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="GenerateMemoriesScene"
      component={GenerateMemoriesScene}
      durationInFrames={150}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="PipelineScene"
      component={PipelineScene}
      durationInFrames={150}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="RefineScene"
      component={RefineScene}
      durationInFrames={180}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="OptionsScene"
      component={OptionsScene}
      durationInFrames={120}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="GeneratingScene"
      component={GeneratingScene}
      durationInFrames={180}
      fps={FPS}
      width={1920}
      height={1080}
    />
    <Composition
      id="CompleteScene"
      component={CompleteScene}
      durationInFrames={120}
      fps={FPS}
      width={1920}
      height={1080}
    />
  </>
);
