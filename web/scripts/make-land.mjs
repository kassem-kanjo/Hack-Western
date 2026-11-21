#!/usr/bin/env node
// Regenerates public/basemap/land.geojson from world-atlas@2.0.2 (land-50m TopoJSON,
// derived from Natural Earth, public domain) using topojson-client.
//
// Usage: npm run make-land
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { feature } = require("topojson-client");

const scriptDir = dirname(fileURLToPath(import.meta.url));
const topologyPath = require.resolve("world-atlas/land-50m.json");
const outputPath = resolve(scriptDir, "..", "public", "basemap", "land.geojson");

const topology = JSON.parse(await readFile(topologyPath, "utf8"));
const land = feature(topology, topology.objects.land);
const collection =
  land.type === "FeatureCollection"
    ? land
    : { type: "FeatureCollection", features: [land] };

// ---------------------------------------------------------------------------
// Antimeridian repair.
//
// A handful of Natural Earth rings step from -180 to +180 (or back) between two
// consecutive points: Wrangel Island, the tip of Chukotka, a Fiji islet and
// Antarctica. Rendered as GeoJSON they draw a line all the way around the
// world. Unwrap each ring into a continuous longitude, cut it at +/-180 and
// close every part along the meridian. Rings around a pole are closed through
// the pole instead, so the seam stays at the edge of the map.
// ---------------------------------------------------------------------------

const MERIDIAN = 180;
const AREA_EPSILON = 1e-8;

/** Drop a repeated closing point so helpers can treat a ring cyclically. */
function openRing(ring) {
  const points = ring.slice();
  if (
    points.length > 2 &&
    points[0][0] === points[points.length - 1][0] &&
    points[0][1] === points[points.length - 1][1]
  ) {
    points.pop();
  }
  return points;
}

function closeRing(points) {
  const ring = points.slice();
  if (ring.length > 0) {
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) {
      ring.push([first[0], first[1]]);
    }
  }
  return ring;
}

function dedupe(points) {
  const out = [];
  for (const point of points) {
    const last = out[out.length - 1];
    if (!last || last[0] !== point[0] || last[1] !== point[1]) out.push(point);
  }
  return out;
}

/** Undo jump-over-the-dateline steps to get one continuous longitude path. */
function unwrap(points) {
  const out = [[points[0][0], points[0][1]]];
  let previous = points[0][0];
  for (let i = 1; i < points.length; i += 1) {
    let x = points[i][0];
    while (x - previous > MERIDIAN) x -= 360;
    while (x - previous < -MERIDIAN) x += 360;
    out.push([x, points[i][1]]);
    previous = x;
  }
  return out;
}

/** Shoelace area in square degrees; only used to discard degenerate parts. */
function ringArea(ring) {
  let sum = 0;
  for (let i = 0; i < ring.length - 1; i += 1) {
    sum += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  }
  return Math.abs(sum) / 2;
}

/** Sutherland-Hodgman clip of a closed ring against one half-plane. */
function clipHalfPlane(points, inside, intersection) {
  if (points.length === 0) return [];
  const out = [];
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    const aInside = inside(a);
    const bInside = inside(b);
    if (aInside) {
      out.push(a);
      if (!bInside) out.push(intersection(a, b));
    } else if (bInside) {
      out.push(intersection(a, b));
      out.push(b);
    }
  }
  return dedupe(out);
}

/** Point-in-ring test (ray casting) for assigning holes to split polygons. */
function pointInRing(point, ring) {
  const [x, y] = point;
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function ringHasJump(ring) {
  for (let i = 1; i < ring.length; i += 1) {
    if (Math.abs(ring[i][0] - ring[i - 1][0]) > MERIDIAN) return true;
  }
  return false;
}

/**
 * Split one ring at the antimeridian. Rings with no jump are returned untouched
 * so the regenerated coastline stays bit-for-bit identical to Natural Earth.
 */
function splitRing(ring) {
  if (!ringHasJump(ring)) return [ring];

  const points = openRing(ring);
  if (points.length < 3) return [];

  // The bottom edge of the Natural Earth land layer is a degenerate ring at
  // -89.999; it carries no area and would only add a seam artefact.
  const latitudes = points.map((point) => point[1]);
  const north = Math.max(...latitudes);
  const south = Math.min(...latitudes);
  if (north - south < 0.01 && Math.abs(points[0][1]) > 89.9) return [];

  const path = unwrap([...points, points[0]]);
  const drift = path[path.length - 1][0] - path[0][0];

  if (Math.abs(drift) > MERIDIAN) {
    // A ring around a pole: close it through the pole rather than across the
    // map. Antarctica's coastline becomes a valid polygon and the artificial
    // edge sits on the pole and on the +/-180 map edges, never across the world.
    const meanLat = latitudes.reduce((sum, lat) => sum + lat, 0) / latitudes.length;
    const poleLat = meanLat < 0 ? -90 : 90;
    const start = path[0][0];
    const direction = drift > 0 ? 1 : -1;
    const end = start + direction * 360;

    const closed = path.slice();
    // Walk back along the pole in <= 90 degree segments so no edge jumps more
    // than a quarter of the world.
    for (let i = 1; i <= 4; i += 1) {
      closed.push([end - direction * 90 * i, poleLat]);
    }

    const longitudes = closed.map((point) => point[0]);
    const shift =
      -360 * Math.round((Math.min(...longitudes) + Math.max(...longitudes)) / 720);
    const shifted = dedupe(closed.map(([x, y]) => [x + shift, y]));
    const closedRing = closeRing(shifted);
    return ringArea(closedRing) > AREA_EPSILON ? [closedRing] : [];
  }

  // Normal ring: shift by whole turns so most of it sits in [-180, 180], then
  // collect the part inside and any slivers pushed past either edge.
  const longitudes = path.map((point) => point[0]);
  const west = Math.min(...longitudes);
  const east = Math.max(...longitudes);
  const shift = -360 * Math.round((west + east) / 720);
  const loop = dedupe(path.map(([x, y]) => [x + shift, y]));

  const parts = [];
  const addPart = (pointsIn, offset) => {
    const moved = dedupe(pointsIn.map(([x, y]) => [x + offset, y]));
    if (moved.length < 3) return;
    const closed = closeRing(moved);
    if (ringArea(closed) > AREA_EPSILON) parts.push(closed);
  };

  const atOrAboveWest = clipHalfPlane(loop, (point) => point[0] >= -MERIDIAN, (a, b) => {
    const t = (-MERIDIAN - a[0]) / (b[0] - a[0]);
    return [-MERIDIAN, a[1] + t * (b[1] - a[1])];
  });
  const inside = clipHalfPlane(atOrAboveWest, (point) => point[0] <= MERIDIAN, (a, b) => {
    const t = (MERIDIAN - a[0]) / (b[0] - a[0]);
    return [MERIDIAN, a[1] + t * (b[1] - a[1])];
  });
  addPart(inside, 0);

  const belowWest = clipHalfPlane(loop, (point) => point[0] <= -MERIDIAN, (a, b) => {
    const t = (-MERIDIAN - a[0]) / (b[0] - a[0]);
    return [-MERIDIAN, a[1] + t * (b[1] - a[1])];
  });
  addPart(belowWest, 360);

  const aboveEast = clipHalfPlane(loop, (point) => point[0] >= MERIDIAN, (a, b) => {
    const t = (MERIDIAN - a[0]) / (b[0] - a[0]);
    return [MERIDIAN, a[1] + t * (b[1] - a[1])];
  });
  addPart(aboveEast, -360);

  return parts;
}

/** Split a polygon's outer ring, then give each hole to the part containing it. */
function splitPolygon(rings) {
  const outerParts = splitRing(rings[0]);
  if (outerParts.length === 0) return [];

  const polygons = outerParts.map((ring) => [ring]);
  for (const hole of rings.slice(1)) {
    for (const holePart of splitRing(hole)) {
      const owner = polygons.find(([ring]) => pointInRing(holePart[0], ring));
      (owner ?? polygons[0]).push(holePart);
    }
  }
  return polygons;
}

function splitGeometry(geometry) {
  if (geometry.type !== "MultiPolygon" && geometry.type !== "Polygon") return;
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  geometry.type = "MultiPolygon";
  geometry.coordinates = polygons.flatMap((rings) => splitPolygon(rings));
}

for (const featureItem of collection.features) splitGeometry(featureItem.geometry);

// Round coordinates to ~10 m so the committed file stays small without visibly
// changing the 1:50m coastline.
const round = (value) => Math.round(value * 1e5) / 1e5;
const roundGeometry = (geometry) => {
  if (!geometry.coordinates) return;
  const walk = (node) =>
    typeof node[0] === "number"
      ? [round(node[0]), round(node[1])]
      : node.map(walk);
  geometry.coordinates = geometry.coordinates.map(walk);
};
for (const featureItem of collection.features) roundGeometry(featureItem.geometry);

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(collection)}\n`);

console.log(
  `wrote ${outputPath} (${collection.features.length} feature(s), ${(
    (await readFile(outputPath)).length / 1e6
  ).toFixed(2)} MB)`,
);
