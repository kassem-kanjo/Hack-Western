/**
 * Canvas-drawn radar symbols.
 *
 * The basemap style is offline: no sprite sheet and no glyph server. Icons are
 * therefore generated locally as ImageData and registered with addImage, which
 * keeps the map working with the network switched off.
 */

import * as maplibregl from "maplibre-gl";

import { themeColor } from "./theme";

/** Icons are authored at 24 x 24 CSS px and rasterised at 2x for sharp edges. */
const CSS_SIZE = 24;
const SCALE = 2;
const CENTRE = CSS_SIZE / 2;
const HALF_DIAGONAL = 8;

type Ctx = CanvasRenderingContext2D;

/** A diamond is a square rotated 45 degrees, so it is a four-point path. */
function diamondPath(ctx: Ctx, half: number): void {
  const cx = CENTRE;
  const cy = CENTRE;
  ctx.beginPath();
  ctx.moveTo(cx, cy - half);
  ctx.lineTo(cx + half, cy);
  ctx.lineTo(cx, cy + half);
  ctx.lineTo(cx - half, cy);
  ctx.closePath();
}

/** Draw at CSS-pixel coordinates on a 2x backing store. */
function renderImage(draw: (ctx: Ctx) => void): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = CSS_SIZE * SCALE;
  canvas.height = CSS_SIZE * SCALE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas context unavailable");
  ctx.scale(SCALE, SCALE);
  draw(ctx);
  // getImageData works in device pixels, so it is unaffected by the scale.
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}

function register(map: maplibregl.Map, id: string, image: ImageData): void {
  // Re-registering an existing image throws, so be idempotent in case a
  // component remounts onto the same map instance.
  if (map.hasImage(id)) return;
  map.addImage(id, image, { pixelRatio: SCALE });
}

export function addDiamondImages(map: maplibregl.Map): void {
  const sar = themeColor("--c-sar", "#5BB8FF");
  const candidate = themeColor("--c-candidate", "#F4FAFF");
  const muted = themeColor("--c-text-3", "#8A9BAD");

  // Plain radar detection: blue ring, no fill.
  const radar = renderImage((ctx) => {
    ctx.lineWidth = 2;
    ctx.strokeStyle = sar;
    diamondPath(ctx, HALF_DIAGONAL);
    ctx.stroke();
  });

  // Detection inside the lens at this instant: white fill, blue ring, glow.
  const inside = renderImage((ctx) => {
    ctx.strokeStyle = sar;
    ctx.globalAlpha = 0.35;
    ctx.lineWidth = 3.5;
    diamondPath(ctx, HALF_DIAGONAL + 1.5);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = candidate;
    ctx.lineWidth = 2;
    diamondPath(ctx, HALF_DIAGONAL);
    ctx.fill();
    ctx.stroke();
  });

  // Outside the lens at this instant: ruled out, so dim the grey diamond.
  const outside = renderImage((ctx) => {
    ctx.globalAlpha = 0.7;
    ctx.fillStyle = muted;
    ctx.strokeStyle = muted;
    ctx.lineWidth = 1.5;
    diamondPath(ctx, HALF_DIAGONAL);
    ctx.fill();
    ctx.stroke();
  });

  register(map, "diamond-radar", radar);
  register(map, "diamond-inside", inside);
  register(map, "diamond-outside", outside);
}
