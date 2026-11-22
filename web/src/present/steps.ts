/**
 * Named hash routes the app's own links use. The caption tour that lived here is gone
 * (#274): the Present button and the P key open the self-paced story at `#/story`.
 */

export const PRESENT_ROUTES = {
  hormuz: "#/hormuz",
  watch: "#/watch",
  patrol: "#/patrol",
  story: "#/story",
} as const;

export type PresentRouteName = keyof typeof PRESENT_ROUTES;
