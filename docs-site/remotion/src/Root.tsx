import "./index.css";
import { Composition } from "remotion";
import { DemoVideo } from "./Composition";
import { FPS } from "./theme";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="DemoVideo"
    component={DemoVideo}
    durationInFrames={1500}
    fps={FPS}
    width={1920}
    height={1080}
  />
);
