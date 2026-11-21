/**
 * Shared rules for matching radar passes to lens frames. Pure functions, no DOM,
 * so the map and the timeline agree on exactly when a radar dot is "live".
 */

import type { Candidate, LensFrame } from "../api";

/** A radar dot is highlighted when the scrubber is within this many frames of its scene time. */
export const HIGHLIGHT_WINDOW_FRAMES = 1;

/** Index of the lens frame whose time is closest to the given ISO instant. */
export function nearestFrameIndex(frames: LensFrame[], iso: string): number {
  const target = Date.parse(iso);
  let best = 0;
  let bestDiff = Number.POSITIVE_INFINITY;
  frames.forEach((frame, i) => {
    const diff = Math.abs(Date.parse(frame.t) - target);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  });
  return best;
}

/** True when the scrubber at frameIndex is at this candidate's scene time (+/- window). */
export function isCandidateLive(
  frames: LensFrame[],
  frameIndex: number,
  candidate: Candidate,
  window: number = HIGHLIGHT_WINDOW_FRAMES,
): boolean {
  if (candidate.scene_time === null) return false;
  const sceneFrame = nearestFrameIndex(frames, candidate.scene_time);
  return Math.abs(sceneFrame - frameIndex) <= window;
}
