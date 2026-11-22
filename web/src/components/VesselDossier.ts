/**
 * Vessel dossier (#182): one hull, read the way an analyst reads a file.
 *
 * The panel is deliberately not part of the watch screen: the map and the tables
 * call `openDossier(mmsi)` and this module owns the panel, the fetch and the join
 * with whatever is on screen. It lives in `document.body` as a fixed right rail,
 * so the watch grid never changes shape when a hull is opened.
 *
 * The only thing it needs back from the screen is a place to draw the 12 h track:
 * `setDossierTrackRenderer` hands it the map handle's `showDossierTrack`. Every
 * open, refresh and close passes the new track (or null) through that one hook,
 * which keeps the map module free of dossier state.
 */

import "../styles/dossier.css";

import type { VesselDossier, VesselTrackPoint } from "../api";
import { fetchVesselDossier } from "../api";
import { fmtUtc } from "../format";
import { timeAgo } from "../model/liveFacts";
import { h } from "./dom";

const SVG_NS = "http://www.w3.org/2000/svg";

/** A derived 12 h speed sample: one per pair of consecutive track fixes. */
export interface SpeedSample {
  /** Milliseconds at the end of the segment. */
  t: number;
  kn: number;
}

/** Short names for the alert kinds the ids start with (watch contract). */
const KIND_LABEL: Record<string, string> = {
  dark: "Switched off",
  sanctioned_stop: "Stopped at sea",
  spoof: "Faked position",
  meeting: "Meeting",
  jump: "Jump",
  radar_unmatched: "Seen by satellite radar",
};

/** Watchlist categories in the words the panel header uses. */
const CATEGORY_LABEL: Record<string, string> = {
  sanctioned: "Sanctioned",
  shadow_fleet: "Shadow fleet",
  detained: "Detained",
};

const SPEED_MAX_KN = 80;
/** Above this a pair of fixes is an identity jump, not a speed; never plot it. */

let panel: HTMLElement | null = null;
let panelBody: HTMLElement | null = null;
let panelId: HTMLElement | null = null;
let closeButton: HTMLButtonElement | null = null;
let currentMmsi: number | null = null;
let currentTrack: VesselTrackPoint[] | null = null;
let controller: AbortController | null = null;
let trackRenderer: ((track: VesselTrackPoint[] | null) => void) | null = null;

/* ---- pure formatting ------------------------------------------------------ */

/** "2026-11-21T19:10:00Z" -> "21 Nov 19:10Z"; a broken time is an honest dash. */
function fmtZ(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? `${fmtUtc(iso)}Z` : "\u2014";
}

function msZ(ms: number): string {
  return fmtZ(new Date(ms).toISOString());
}

function dash(value: string | null | undefined, empty = "Not recorded"): string {
  const trimmed = value?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : empty;
}

function alertLabel(id: string): string {
  const kind = id.split("-")[0] ?? "";
  return KIND_LABEL[kind] ?? "Alert";
}

/** Great-circle distance in km; the track's own fixes are all the sparkline needs. */
function distanceKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const rad = Math.PI / 180;
  const dLat = (bLat - aLat) * rad;
  const dLon = (bLon - aLon) * rad;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(aLat * rad) * Math.cos(bLat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(s)));
}

/**
 * Speed over each leg of a decimated track, in knots.
 *
 * The API's track is positions only, so speed is measured from the fixes themselves:
 * distance between consecutive points over the time between them. A segment that
 * claims more than `SPEED_MAX_KN` is an identity jump or a re-decimation artefact
 * and is left out rather than drawn as a spike.
 */
export function speedsFromTrack(points: readonly VesselTrackPoint[]): SpeedSample[] {
  const samples: SpeedSample[] = [];
  for (let index = 1; index < points.length; index += 1) {
    const from = points[index - 1];
    const to = points[index];
    if (from === undefined || to === undefined) continue;
    const fromMs = Date.parse(from[2]);
    const toMs = Date.parse(to[2]);
    if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || toMs <= fromMs) continue;
    const km = distanceKm(from[1], from[0], to[1], to[0]);
    const kn = km / (((toMs - fromMs) / 3_600_000) * 1.852);
    if (!Number.isFinite(kn) || kn < 0 || kn > SPEED_MAX_KN) continue;
    samples.push({ t: toMs, kn });
  }
  return samples;
}

/* ---- DOM helpers ---------------------------------------------------------- */

function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
  return el;
}

/** A panel header: small caps, hairline, the count in mono on the right. */
function section(title: string, count: string | number | null, ...children: Node[]): HTMLElement {
  return h(
    "section",
    { class: "vd-section" },
    h(
      "div",
      { class: "vd-section-head" },
      h("h3", { class: "vd-section-title" }, title),
      count === null ? null : h("span", { class: "vd-section-count" }, String(count)),
    ),
    ...children,
  );
}

function field(label: string, value: Node | string, mono = false): [HTMLElement, HTMLElement] {
  return [
    h("dt", null, label),
    h("dd", { class: mono ? "vd-mono" : null }, value),
  ];
}

/** Last seen: the UTC stamp plus its quiet relative follow-up. */
function seenValue(iso: string): HTMLElement {
  return h(
    "span",
    { class: "vd-seen" },
    h("time", { class: "vd-mono", datetime: iso }, fmtZ(iso)),
    h("span", { class: "vd-rel" }, timeAgo(iso, Date.now())),
  );
}

/** The 12 h speed line: flat hairline, no fill, no glow. */
function sparkline(samples: readonly SpeedSample[]): HTMLElement {
  if (samples.length === 0) {
    return h("div", { class: "vd-state vd-spark-empty" }, "No fixes to compare");
  }
  const width = 300;
  const height = 40;
  const peak = Math.max(...samples.map((sample) => sample.kn));
  const ceiling = Math.max(1, Math.ceil(peak * 1.15));
  const x = (index: number): number =>
    samples.length === 1 ? width / 2 : (index / (samples.length - 1)) * width;
  const y = (kn: number): number => height - 2 - (kn / ceiling) * (height - 4);
  const lastSample = samples[samples.length - 1] as SpeedSample;

  const line = svgEl("polyline", {
    points: samples
      .map((sample, index) => `${x(index).toFixed(1)},${y(sample.kn).toFixed(1)}`)
      .join(" "),
    class: "vd-spark-line",
  });
  const dot = svgEl("circle", {
    cx: x(samples.length - 1),
    cy: y(lastSample.kn),
    r: 2,
    class: "vd-spark-dot",
  });
  const graph = svgEl("svg", {
    class: "vd-spark",
    viewBox: `0 0 ${width} ${height}`,
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": `Speed over the last 12 h, ${samples.length} legs, peak ${peak.toFixed(1)} knots`,
  });
  graph.append(
    svgEl("line", { x1: 0, y1: height - 1, x2: width, y2: height - 1, class: "vd-spark-base" }),
    svgEl("line", { x1: 0, y1: 1, x2: width, y2: 1, class: "vd-spark-top" }),
    line,
    dot,
  );

  return h(
    "div",
    { class: "vd-spark-wrap" },
    graph,
    h(
      "div",
      { class: "vd-spark-foot" },
      h("span", { class: "vd-mono" }, `${msZ((samples[0] as SpeedSample).t)} \u2192 ${msZ(lastSample.t)}`),
      h("span", { class: "vd-mono" }, `max ${peak.toFixed(1)} kn`),
    ),
  );
}

function watchlistBlock(data: VesselDossier): HTMLElement | null {
  const entry = data.watchlist;
  if (entry === null) return null;
  const category = CATEGORY_LABEL[entry.category] ?? entry.category.replace(/_/g, " ");

  // The list's name is the label a reader knows (issue #249); raw dataset ids such as
  // ca_dfatd_sema_sanctions stay out of the text. Without a name, one plain link to the dataset.
  const rows: Node[] = [h("div", { class: "vd-watch-cat" }, category)];
  if (entry.list_name !== null) {
    rows.push(h("div", { class: "vd-watch-list" }, entry.list_name));
  } else if (entry.datasets.length > 0) {
    const first = entry.datasets[0] as string;
    rows.push(
      h(
        "div",
        { class: "vd-watch-datasets" },
        h(
          "a",
          {
            class: "vd-watch-dataset",
            href: `https://www.opensanctions.org/datasets/${encodeURIComponent(first)}/`,
            target: "_blank",
            rel: "noopener noreferrer",
          },
          "OpenSanctions dataset",
        ),
      ),
    );
  }
  if (entry.url !== null) {
    rows.push(
      h(
        "a",
        {
          class: "vd-watch-link",
          href: entry.url,
          target: "_blank",
          rel: "noopener noreferrer",
        },
        "Source entry \u2197",
      ),
    );
  }
  return section("Sanctions and shadow fleet", 1, h("div", { class: "vd-watch is-listed" }, ...rows));
}

function alertList(data: VesselDossier): HTMLElement {
  if (data.alerts.length === 0) {
    return section("Alerts", 0, h("div", { class: "vd-none" }, "No alerts"));
  }
  const rows = data.alerts.map((id) => {
    const kind = id.split("-")[0] ?? "";
    const row = h(
      "button",
      {
        class: `vd-alert is-${kind}`,
        type: "button",
        "data-alert-id": id,
        "aria-label": `${alertLabel(id)}: ${id}`,
      },
      h("span", { class: "vd-square", "aria-hidden": "true" }),
      h("span", { class: "vd-alert-kind" }, alertLabel(id)),
      h("span", { class: "vd-alert-id" }, id),
    );
    row.addEventListener("click", () => {
      // The alert's own panel opens where the dossier stands; get out of its way.
      closeDossier();
      location.hash = `#/watch/alert/${encodeURIComponent(id)}`;
    });
    return h("li", null, row);
  });
  return section("Alerts", data.alerts.length, h("ol", { class: "vd-alerts" }, ...rows));
}

/* ---- panel ---------------------------------------------------------------- */

function ensurePanel(): void {
  if (panel !== null) return;

  const id = h("span", { class: "vd-head-id" }, "\u2014");
  closeButton = h(
    "button",
    {
      class: "vd-close",
      type: "button",
      title: "Close dossier",
      "aria-label": "Close vessel dossier",
    },
    "\u00d7",
  );
  closeButton.addEventListener("click", () => {
    closeDossier();
  });
  panelBody = h("div", { class: "vd-body" }, h("div", { class: "vd-state" }, "Loading dossier..."));
  const aside = h(
    "aside",
    {
      class: "vessel-dossier",
      role: "complementary",
      "aria-label": "Vessel dossier",
      "data-mmsi": null,
    },
    h(
      "header",
      { class: "vd-head" },
      h("h2", null, "Vessel dossier"),
      id,
      closeButton,
    ),
    panelBody,
  );
  aside.hidden = true;
  document.body.append(aside);

  panel = aside;
  panelId = id;
}

function render(data: VesselDossier): void {
  if (panelBody === null || panelId === null) return;
  panelId.textContent = `MMSI ${data.mmsi}`;

  const fields = h(
    "dl",
    { class: "vd-fields" },
    ...field("Vessel", h("span", { class: "vd-name" }, dash(data.name))),
    ...field("MMSI", String(data.mmsi), true),
    ...field("Permanent ID number (IMO)", data.imo === null ? "Not recorded" : String(data.imo), true),
    ...field("Callsign", dash(data.callsign), true),
    ...field("Type", dash(data.type ?? data.class)),
    ...field("Flag", dash(data.flag, "No flag broadcast")),
    ...field("Length", data.length_m === null ? "Not recorded" : `${Math.round(data.length_m)} m`, true),
    ...field("Destination", dash(data.destination)),
    ...field("Last seen", seenValue(data.last.t)),
    ...field(
      "Position",
      `${data.last.lat.toFixed(4)}, ${data.last.lon.toFixed(4)}`,
      true,
    ),
  );

  const sections: Node[] = [section("Identity", 10, fields)];
  const watchlist = watchlistBlock(data);
  if (watchlist !== null) sections.push(watchlist);
  sections.push(section("Speed", "12 h", sparkline(speedsFromTrack(data.track))));
  sections.push(alertList(data));

  panelBody.replaceChildren(...sections);
  panelBody.scrollTop = 0;
}

function showState(message: string): void {
  panelBody?.replaceChildren(h("div", { class: "vd-state" }, message));
}

/** Draw (or clear) the 12 h track through the map hook the watch screen installed. */
function applyTrack(track: VesselTrackPoint[] | null): void {
  currentTrack = track !== null && track.length > 0 ? track : null;
  trackRenderer?.(currentTrack);
}

/**
 * Open one hull's dossier, or refresh the open one.
 *
 * The panel exists after the first call and is reused: switching hulls only
 * replaces its body, so the layout never flickers between openings.
 */
export function openDossier(mmsi: number): void {
  if (!Number.isFinite(mmsi) || mmsi <= 0) return;
  ensurePanel();
  if (panel === null) return;

  const aside = panel;
  const changed = currentMmsi !== mmsi;
  aside.hidden = false;
  aside.dataset.mmsi = String(mmsi);
  aside.scrollTop = 0;

  if (changed) {
    currentMmsi = mmsi;
    applyTrack(null);
    showState("Loading dossier...");
    closeButton?.focus({ preventScroll: true });
  }

  controller?.abort();
  const request = new AbortController();
  controller = request;

  fetchVesselDossier(mmsi, request.signal)
    .then((data) => {
      if (controller !== request || currentMmsi !== mmsi || panel === null) return;
      controller = null;
      render(data);
      applyTrack(data.track);
    })
    .catch(() => {
      if (request.signal.aborted || panel === null || currentMmsi !== mmsi) return;
      controller = null;
      applyTrack(null);
      showState("Dossier unavailable");
    });
}

/** Close the panel and clear the track it drew. Safe when nothing is open. */
export function closeDossier(): void {
  controller?.abort();
  controller = null;
  currentMmsi = null;
  applyTrack(null);
  if (panel !== null) {
    panel.hidden = true;
    panelBody?.replaceChildren();
  }
}

/**
 * Hand the dossier a place to draw tracks (the watch map's `showDossierTrack`).
 * Passing null detaches it; a newly mounted map re-receives the open track.
 */
export function setDossierTrackRenderer(
  renderer: ((track: VesselTrackPoint[] | null) => void) | null,
): void {
  trackRenderer = renderer;
  if (renderer !== null) renderer(currentTrack);
}

/** A hull's own alert ids are clickable, and Escape closes the panel first. */
if (typeof document !== "undefined") {
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (panel === null || panel.hidden) return;
    if (document.body.classList.contains("is-presenting")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    closeDossier();
  });
}
