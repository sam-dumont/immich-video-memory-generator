import { staticFile, useVideoConfig } from "remotion";
import { useWindowedAudioData, visualizeAudio } from "@remotion/media-utils";

export const useBassIntensity = (frame: number): number => {
  const { fps } = useVideoConfig();

  const { audioData, dataOffsetInSeconds } = useWindowedAudioData({
    src: staticFile("demo-music.wav"),
    frame,
    fps,
    windowInSeconds: 30,
  });

  if (!audioData) return 0;

  const frequencies = visualizeAudio({
    fps,
    frame,
    audioData,
    numberOfSamples: 128,
    optimizeFor: "speed",
    dataOffsetInSeconds,
  });

  // Average of lowest 16 frequency bins = bass intensity
  const bassSlice = frequencies.slice(0, 16);
  return bassSlice.reduce((sum, v) => sum + v, 0) / bassSlice.length;
};
