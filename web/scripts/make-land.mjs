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
