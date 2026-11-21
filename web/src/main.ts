import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";
// MapLibre's worker is a sibling file in the package: point it at the copy Vite
// emits so it is served locally (offline, no CDN).
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { offlineStyle } from "./basemap";

maplibregl.setWorkerUrl(workerUrl);

const map = new maplibregl.Map({
  container: "map",
  style: offlineStyle(),
  center: [19, 58], // [lon, lat]: central Baltic Sea
  zoom: 4.5,
});
map.addControl(
  new maplibregl.AttributionControl({
    compact: false,
    customAttribution: "Natural Earth",
  }),
  "bottom-right",
);
