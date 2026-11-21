import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

const map = new maplibregl.Map({
  container: "map",
  style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  center: [19, 58], // [lon, lat]: central Baltic Sea
  zoom: 4.5,
});
map.addControl(new maplibregl.AttributionControl({ compact: false }), "bottom-right");
