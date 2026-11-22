import React from "react";
import { Composition } from "remotion";
import { DarkFleet, TOTAL_SECONDS, FPS } from "./DarkFleet";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="DarkFleetDemo"
    component={DarkFleet}
    durationInFrames={Math.round(TOTAL_SECONDS * FPS)}
    fps={FPS}
    width={1920}
    height={1080}
    defaultProps={{ captions: true }}
  />
);
