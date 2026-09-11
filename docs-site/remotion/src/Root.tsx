import "./index.css";
import { Composition } from "remotion";
import { DemoVideo } from "./Composition";
import { FPS, TOTAL_FRAMES } from "./theme";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="DemoVideo"
    component={DemoVideo}
    durationInFrames={TOTAL_FRAMES}
    fps={FPS}
    width={1920}
    height={1080}
  />
);
