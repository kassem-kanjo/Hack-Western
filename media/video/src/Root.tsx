import React from "react";
import { Composition } from "remotion";
import { DarkFleet, TOTAL_SECONDS, FPS } from "./DarkFleet";
import { DarkFleetV5, V5_TOTAL_FRAMES, V5_FPS } from "./DarkFleetV5";

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="DarkFleetDemo"
      component={DarkFleet}
      durationInFrames={Math.round(TOTAL_SECONDS * FPS)}
      fps={FPS}
      width={1920}
      height={1080}
      defaultProps={{ captions: true, guide: false }}
    />
    <Composition
      id="DarkFleetV5"
      component={DarkFleetV5}
      durationInFrames={V5_TOTAL_FRAMES}
      fps={V5_FPS}
      width={1920}
      height={1080}
      defaultProps={{ captions: true, guide: false }}
    />
  </>
);
