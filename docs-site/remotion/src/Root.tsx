import "./index.css";
import { Composition } from "remotion";
import { DemoVideo } from "./Composition";
import { BEATS, FPS } from "./theme";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="DemoVideo"
    component={DemoVideo}
    durationInFrames={BEATS.musicEnd}
    fps={FPS}
    width={1920}
    height={1080}
  />
);
