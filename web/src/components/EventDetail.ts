/**
 * The right-rail panel for one selected live event: where it is, what happened,
 * the one number that carries it, a small picture of the proof, the numbers
 * behind that picture, and two disclosures.
 *
 * Everything is built with h() (or createElementNS for SVG) so snapshot text is
 * never parsed as markup, and every figure is read defensively from
 * `event.evidence`: evidence keys differ by kind and may be missing.
 *
 * Presentation only: the only arithmetic here is unit formatting, simple ratios
 * for bar widths, and picking a position on a time axis.
 */

import type { LiveEvent, LiveVessel, LiveWatchlistEntry } from "../model/live";
import {
  KIND_LABEL,
  bigNumber,
  formatDuration,
  timeAgo,
  timelineFraction,
  vesselName,
} from "../model/liveFacts";
import { h } from "./dom";

export interface EventDetailProps {
  event: LiveEvent;
  vessels: LiveVessel[];
  /** Watchlist hit for this event (watchlistHit result), or null. A red tag shows only when non-null. */
  watch: LiveWatchlistEntry | null;
  /** Coverage window and generation time for the Sources block. */
  window: { start: string; end: string };
  /** Hash to go back to the list, e.g. "#/live/BALTIC_WATCH". */
  backHref: string;
  /** Label for the breadcrumb, e.g. "Baltic". */
  regionLabel: string;
  nowMs: number;
}

/** One em dash stands in for every figure the snapshot did not give us. */
const DASH = "\u2014";
const SVG_NS = "http://www.w3.org/2000/svg";

/* -------------------------------------------------------------------------
 * Defensive readers
 * ---------------------------------------------------------------------- */

/** Evidence values arrive as unknown JSON; only a real finite number is usable. */
function num(evidence: Record<string, unknown>, key: string): number | null {
  const raw = evidence[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

/**
 * Spoof evidence carries the ships as objects with an `mmsi` (or, from older
 * snapshots, as bare numbers). Anything else is dropped rather than printed.
 */
function evidenceMmsis(evidence: Record<string, unknown>): number[] {
  const raw = evidence["vessels"];
  if (!Array.isArray(raw)) return [];

  const out: number[] = [];
  for (const item of raw) {
    if (typeof item === "number" && Number.isFinite(item)) {
      out.push(item);
      continue;
    }
    if (item !== null && typeof item === "object") {
      const mmsi = (item as Record<string, unknown>)["mmsi"];
      if (typeof mmsi === "number" && Number.isFinite(mmsi)) out.push(mmsi);
    }
  }
  return out;
}

/* -------------------------------------------------------------------------
 * Formatting
 * ---------------------------------------------------------------------- */

/** One decimal, but "380" not "380.0" (the tenth only appears when it says something). */
function fmt1(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

function fmtInt(value: number): string {
  return String(Math.round(value));
}

/** A value with its unit, or an em dash when the snapshot has no number. */
function unitValue(value: number | null, unit: string): string {
  return value === null ? DASH : `${fmt1(value)} ${unit}`;
}

function countValue(value: number | null): string {
  return value === null ? DASH : fmtInt(value);
}

/** HH:MM in UTC; derived from the timestamp itself so the machine's zone never leaks in. */
function utcHm(iso: string): string | null {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return null;
  const date = new Date(ms);
  const hours = String(date.getUTCHours()).padStart(2, "0");
  const minutes = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

/** A clock time with its zone spelled out, for the numbers table. */
function clockValue(iso: string): string {
  const hm = utcHm(iso);
  return hm === null ? DASH : `${hm} UTC`;
}

/* -------------------------------------------------------------------------
 * Small DOM helpers
 * ---------------------------------------------------------------------- */

function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag) as SVGElementTagNameMap[K];
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  return el;
}

/** The stand-in for a proof block whose key evidence is missing. */
function missingPicture(): HTMLElement {
  return h("p", { class: "event-detail__missing" }, "Not enough data for a picture");
}

/** Kind glyphs, drawn to the same shapes the map uses so the two read as one system. */
function kindGlyph(kind: LiveEvent["kind"]): SVGSVGElement {
  const svg = svgEl("svg", {
    class: "event-detail__glyph",
    viewBox: "0 0 20 20",
    width: 16,
    height: 16,
    "aria-hidden": "true",
  });
  svg.setAttribute("data-kind", kind);

  const stroke = { fill: "none", stroke: "currentColor", "stroke-width": 2 } as const;

  if (kind === "silence") {
    svg.append(svgEl("circle", { cx: 7, cy: 10, r: 5, ...stroke }));
    svg.append(svgEl("path", { d: "M13 10h6", ...stroke, "stroke-dasharray": "2 2" }));
  } else if (kind === "jump") {
    svg.append(svgEl("path", { d: "M10 3 L17.5 16.5 H2.5 Z", ...stroke, "stroke-linejoin": "round" }));
  } else if (kind === "clone") {
    svg.append(svgEl("rect", { x: 2.5, y: 2.5, width: 9, height: 9, ...stroke }));
    svg.append(
      svgEl("rect", {
        x: 8.5,
        y: 8.5,
        width: 9,
        height: 9,
        ...stroke,
        class: "event-detail__glyph-panel",
      }),
    );
  } else if (kind === "meeting") {
    svg.append(svgEl("circle", { cx: 10, cy: 10, r: 4, ...stroke }));
    svg.append(svgEl("circle", { cx: 10, cy: 10, r: 8, ...stroke }));
  } else {
    // Spoof: a crosshair, same as the map's false-position mark.
    svg.append(svgEl("circle", { cx: 10, cy: 10, r: 5, ...stroke }));
    svg.append(svgEl("path", { d: "M10 1v4 M10 15v4 M1 10h4 M15 10h4", ...stroke }));
  }
  return svg;
}

/* -------------------------------------------------------------------------
 * 1-5: breadcrumb, kind, ship, one big number, one sentence
 * ---------------------------------------------------------------------- */

function headBlock(props: EventDetailProps, vessel: LiveVessel | null): HTMLElement {
  const { event, watch, backHref, regionLabel, nowMs } = props;

  // The class comes from the vessel row; a bare ship_type code is the fallback
  // when the feed has not filled the class in.
  let meta: string | null = null;
  if (vessel !== null) {
    const shipClass = vessel.class.trim();
    if (shipClass !== "") meta = shipClass;
    else if (vessel.ship_type !== null) meta = `type ${vessel.ship_type}`;
  }

  const big = bigNumber(event);
  const sentence = event.explanation.trim();

  return h(
    "div",
    { class: "event-detail__head" },
    h(
      "div",
      { class: "event-detail__crumbs" },
      h("a", { class: "event-detail__back", href: backHref }, `\u2039 ${regionLabel}`),
      h("span", { class: "event-detail__crumb-sep" }, " / Events"),
    ),
    h(
      "div",
      { class: "event-detail__kind" },
      kindGlyph(event.kind),
      h("span", null, KIND_LABEL[event.kind]),
      h("span", { class: "event-detail__ago" }, timeAgo(event.start, nowMs)),
    ),
    h(
      "div",
      { class: "event-detail__vessel" },
      h("span", { class: "event-detail__ship" }, vesselName(event.mmsi, props.vessels)),
      h("span", { class: "event-detail__ids" }, `MMSI ${event.mmsi}`),
      meta !== null ? h("span", { class: "event-detail__class" }, `\u00b7 ${meta}`) : null,
    ),
    // The red tag is a separate line: it is a caveat about the ship, not part of its name.
    watch !== null ? h("div", { class: "event-detail__watch" }, "On sanctions watchlist") : null,
    h(
      "div",
      { class: "event-detail__big" },
      h("span", { class: "event-detail__big-value" }, big.value),
      big.tail !== "" ? h("span", { class: "event-detail__big-tail" }, big.tail) : null,
    ),
    sentence !== "" ? h("p", { class: "event-detail__sentence" }, sentence) : null,
  );
}

/* -------------------------------------------------------------------------
 * 6: proof pictures
 * ---------------------------------------------------------------------- */

/** One labelled bar: the track is the shared scale, the fill is drawn with a numeric percent. */
function barRow(label: string, value: string, percent: number, muted: boolean): HTMLElement {
  const fill = h("div", {
    class: muted ? "event-detail__bar-fill is-muted" : "event-detail__bar-fill",
  });
  fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;

  return h(
    "div",
    { class: "event-detail__bar-row" },
    h(
      "div",
      { class: "event-detail__bar-head" },
      h("span", { class: "event-detail__bar-label" }, label),
      h("span", { class: "event-detail__bar-value" }, value),
    ),
    h("div", { class: "event-detail__bar" }, fill),
  );
}

function jumpProof(event: LiveEvent, vessel: LiveVessel | null): HTMLElement {
  const km = num(event.evidence, "distance_km");
  const minutes = num(event.evidence, "minutes");
  const implied = num(event.evidence, "implied_kn");
  const cap = num(event.evidence, "cap_kn");
  if (km === null || minutes === null || implied === null || cap === null || implied <= 0) {
    return missingPicture();
  }

  const shipClass = vessel?.class.trim() ?? "";
  const capLabel =
    shipClass !== "" ? `Fastest a ${shipClass} this size can go` : "Fastest a ship this size can go";

  return h(
    "div",
    { class: "event-detail__proof" },
    barRow(
      `Speed needed to cover ${fmt1(km)} km in ${fmtInt(minutes)} min`,
      `${fmt1(implied)} kn`,
      100,
      false,
    ),
    barRow(capLabel, `${fmt1(cap)} kn`, (cap / implied) * 100, true),
    h(
      "p",
      { class: "event-detail__caption" },
      "The ship cannot have made this trip. The two positions came from different transmitters, or one was false.",
    ),
  );
}

/**
 * The axis is the coverage window, so the dotted line has real room on both
 * sides of the gap. If the window is unusable we fall back to the event's own
 * span, where the gap fills the whole axis.
 */
function timeAxis(
  event: LiveEvent,
  coverage: { start: string; end: string },
): { start: string; end: string } | null {
  const windowStart = Date.parse(coverage.start);
  const windowEnd = Date.parse(coverage.end);
  if (Number.isFinite(windowStart) && Number.isFinite(windowEnd) && windowEnd > windowStart) {
    return { start: coverage.start, end: coverage.end };
  }
  const eventStart = Date.parse(event.start);
  const eventEnd = Date.parse(event.end);
  if (Number.isFinite(eventStart) && Number.isFinite(eventEnd) && eventEnd > eventStart) {
    return { start: event.start, end: event.end };
  }
  return null;
}

let hatchSeq = 0;

/** Amber hatching drawn as an SVG pattern: a hatch, but no gradient and no colour outside the palette. */
function amberHatch(): SVGSVGElement {
  hatchSeq += 1;
  const id = `event-hatch-${hatchSeq}`;

  const pattern = svgEl("pattern", {
    id,
    width: 6,
    height: 6,
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(45)",
  });
  pattern.append(svgEl("line", { x1: 0, y1: 0, x2: 0, y2: 6, class: "event-detail__hatch-line" }));

  const defs = svgEl("defs", {});
  defs.append(pattern);

  const svg = svgEl("svg", { class: "event-detail__hatch", "aria-hidden": "true" });
  svg.append(defs);
  svg.append(svgEl("rect", { x: 0, y: 0, width: "100%", height: "100%", fill: `url(#${id})` }));
  return svg;
}

function silenceProof(
  event: LiveEvent,
  coverage: { start: string; end: string },
): HTMLElement {
  const minutes = num(event.evidence, "minutes");
  const axis = timeAxis(event, coverage);
  if (minutes === null || axis === null) return missingPicture();

  const startPct = timelineFraction(event.start, axis) * 100;
  const endPct = timelineFraction(event.end, axis) * 100;
  const gapLeft = Math.min(startPct, endPct);
  const gapWidth = Math.abs(endPct - startPct);

  const before = h("span", { class: "event-detail__dots" });
  before.style.left = "0%";
  before.style.width = `${gapLeft}%`;

  const after = h("span", { class: "event-detail__dots" });
  after.style.left = `${gapLeft + gapWidth}%`;
  after.style.right = "0";

  const gap = h(
    "div",
    { class: "event-detail__gap" },
    amberHatch(),
    h("span", { class: "event-detail__gap-label" }, formatDuration(minutes)),
  );
  gap.style.left = `${gapLeft}%`;
  gap.style.width = `${Math.max(1, gapWidth)}%`;

  const shipTrack = h("div", { class: "event-detail__track" }, before, gap, after);

  // The other row is dense on purpose: many thin ticks = "still being heard".
  const TICKS = 72;
  const ticks = h("div", { class: "event-detail__track is-ticks" });
  for (let i = 0; i < TICKS; i += 1) {
    const tick = h("span", { class: "event-detail__tick" });
    tick.style.left = `${(i / (TICKS - 1)) * 100}%`;
    ticks.append(tick);
  }

  const rows = h(
    "div",
    { class: "event-detail__time-grid" },
    h("span", { class: "event-detail__time-name" }, "This ship"),
    shipTrack,
    h("span", { class: "event-detail__time-name" }, "Ships within 60 km"),
    ticks,
  );

  const axisRow = h(
    "div",
    { class: "event-detail__axis" },
    h("span", null),
    h(
      "span",
      { class: "event-detail__axis-times" },
      h("span", null, utcHm(axis.start) ?? DASH),
      h("span", null, `${utcHm(axis.end) ?? DASH} UTC`),
    ),
  );

  const others = num(event.evidence, "local_other_vessels");
  const caption =
    others !== null
      ? h(
          "p",
          { class: "event-detail__caption" },
          "Our receivers kept hearing about ",
          h("span", { class: "event-detail__mono" }, fmtInt(others)),
          " other ships nearby. Only this one stopped.",
        )
      : h(
          "p",
          { class: "event-detail__caption" },
          "Our receivers kept hearing other ships nearby. Only this one stopped.",
        );

  return h("div", { class: "event-detail__proof" }, rows, axisRow, caption);
}

function cloneProof(event: LiveEvent): HTMLElement {
  const separation = num(event.evidence, "separation_km");
  if (separation === null) return missingPicture();

  const svg = svgEl("svg", {
    class: "event-detail__schematic",
    viewBox: "0 0 300 110",
    preserveAspectRatio: "xMidYMid meet",
    "aria-hidden": "true",
  });

  svg.append(
    svgEl("line", {
      x1: 44,
      y1: 76,
      x2: 256,
      y2: 32,
      class: "event-detail__schema-line",
    }),
  );

  for (const [cx, cy] of [
    [44, 76],
    [256, 32],
  ] as const) {
    svg.append(svgEl("circle", { cx, cy, r: 4, class: "event-detail__schema-dot" }));
  }

  const lineLabel = svgEl("text", {
    x: 150,
    y: 46,
    "text-anchor": "middle",
    class: "event-detail__schema-label is-strong",
  });
  lineLabel.textContent = `${fmt1(separation)} km`;
  svg.append(lineLabel);

  const labelA = svgEl("text", {
    x: 44,
    y: 96,
    "text-anchor": "middle",
    class: "event-detail__schema-label",
  });
  labelA.textContent = "Position A";
  svg.append(labelA);

  const labelB = svgEl("text", {
    x: 256,
    y: 16,
    "text-anchor": "middle",
    class: "event-detail__schema-label",
  });
  labelB.textContent = "Position B";
  svg.append(labelB);

  return h(
    "div",
    { class: "event-detail__proof" },
    svg,
    h(
      "p",
      { class: "event-detail__caption" },
      "One ID, two places, ",
      h("span", { class: "event-detail__mono" }, countValue(num(event.evidence, "flips"))),
      " switches.",
    ),
  );
}

function meetingProof(event: LiveEvent): HTMLElement {
  const closest = num(event.evidence, "min_distance_m");
  const minutes = num(event.evidence, "minutes");
  const sog = num(event.evidence, "mean_sog");
  if (closest === null || minutes === null) return missingPicture();

  const together = `${fmtInt(minutes)} min`;

  return h(
    "div",
    { class: "event-detail__proof" },
    h(
      "div",
      { class: "event-detail__pair" },
      h(
        "div",
        { class: "event-detail__stat" },
        h("span", { class: "event-detail__stat-label" }, "Closest"),
        h("span", { class: "event-detail__stat-value" }, `${fmtInt(closest)} m`),
      ),
      h(
        "div",
        { class: "event-detail__stat" },
        h("span", { class: "event-detail__stat-label" }, "Together"),
        h("span", { class: "event-detail__stat-value" }, together),
        // The speed is a qualifier, not the headline, so it sits under the figure.
        sog !== null ? h("span", { class: "event-detail__stat-note" }, `at ${fmt1(sog)} kn`) : null,
      ),
    ),
    h(
      "p",
      { class: "event-detail__caption" },
      "Two ships stayed this close and this still.",
    ),
  );
}

function spoofProof(event: LiveEvent): HTMLElement {
  const mmsis = evidenceMmsis(event.evidence);
  if (mmsis.length === 0) return missingPicture();

  const list = h("ul", { class: "event-detail__mmsis" });
  for (const mmsi of mmsis) {
    list.append(h("li", { class: "event-detail__mmsi" }, String(mmsi)));
  }

  return h(
    "div",
    { class: "event-detail__proof" },
    list,
    h(
      "p",
      { class: "event-detail__caption" },
      "These ships all reported one position that cannot be real.",
    ),
  );
}

function proofPicture(
  event: LiveEvent,
  vessel: LiveVessel | null,
  coverage: { start: string; end: string },
): HTMLElement {
  switch (event.kind) {
    case "jump":
      return jumpProof(event, vessel);
    case "silence":
      return silenceProof(event, coverage);
    case "clone":
      return cloneProof(event);
    case "meeting":
      return meetingProof(event);
    default:
      return spoofProof(event);
  }
}

/* -------------------------------------------------------------------------
 * 7: the numbers
 * ---------------------------------------------------------------------- */

function tableRow(label: string, value: string): HTMLElement {
  return h("div", { class: "event-detail__row" }, h("dt", null, label), h("dd", null, value));
}

function numbersTable(event: LiveEvent): HTMLElement {
  const evidence = event.evidence;
  const rows: Array<[string, string]> = [];

  if (event.kind === "jump") {
    rows.push([
      "Distance between positions",
      unitValue(num(evidence, "distance_km"), "km"),
    ]);
    rows.push(["Time between them", unitValue(num(evidence, "minutes"), "min")]);
    rows.push(["Speed needed", unitValue(num(evidence, "implied_kn"), "kn")]);
    rows.push(["Class maximum", unitValue(num(evidence, "cap_kn"), "kn")]);
  } else if (event.kind === "silence") {
    rows.push(["Last heard", clockValue(event.start)]);
    rows.push(["Heard again", clockValue(event.end)]);
    rows.push(["Speed before going quiet", unitValue(num(evidence, "implied_kn"), "kn")]);
    rows.push([
      "Distance between last and next fix",
      unitValue(num(evidence, "distance_km"), "km"),
    ]);
    rows.push([
      "Heard by receivers nearby",
      countValue(num(evidence, "local_other_vessels")),
    ]);
  } else if (event.kind === "clone") {
    rows.push(["Separation", unitValue(num(evidence, "separation_km"), "km")]);
    rows.push(["Switches", countValue(num(evidence, "flips"))]);
    rows.push(["Max implied speed", unitValue(num(evidence, "max_implied_kn"), "kn")]);
  } else if (event.kind === "meeting") {
    rows.push(["Closest distance", unitValue(num(evidence, "min_distance_m"), "m")]);
    rows.push(["Widest distance", unitValue(num(evidence, "max_distance_m"), "m")]);
    rows.push(["Duration", unitValue(num(evidence, "minutes"), "min")]);
    rows.push(["Offshore", unitValue(num(evidence, "offshore_km"), "km")]);
  } else {
    const mmsis = evidenceMmsis(evidence);
    const ships = mmsis.length > 0 ? mmsis.length : num(evidence, "count");
    rows.push(["Ships", countValue(ships)]);
  }

  const list = h("dl", { class: "event-detail__table" });
  for (const [label, value] of rows) list.append(tableRow(label, value));
  return list;
}

/* -------------------------------------------------------------------------
 * 8-9: disclosures
 * ---------------------------------------------------------------------- */

function disclosure(title: string, items: HTMLElement[]): HTMLElement {
  return h(
    "details",
    { class: "event-detail__disc" },
    h(
      "summary",
      { class: "event-detail__summary" },
      h("span", null, title),
      h("span", { class: "event-detail__count" }, String(items.length)),
    ),
    h("ul", { class: "event-detail__list" }, ...items),
  );
}

function sourcesBlock(
  watch: LiveWatchlistEntry | null,
  coverage: { start: string; end: string },
): HTMLElement {
  const items: HTMLElement[] = [];

  const from = utcHm(coverage.start);
  const to = utcHm(coverage.end);
  const recording =
    from !== null && to !== null
      ? `Our AIS recording, ${from} to ${to} UTC`
      : "Our AIS recording";
  items.push(h("li", null, recording));

  if (watch !== null) {
    const which = watch.entry.caption?.trim() || watch.name?.trim() || "";
    const strength = watch.strength.trim();
    const listName = which !== "" ? `OpenSanctions list: ${which}` : "OpenSanctions list";
    items.push(h("li", null, strength !== "" ? `${listName}, matched by ${strength}` : listName));
  }

  return disclosure("Sources", items);
}

function caveatsBlock(event: LiveEvent): HTMLElement {
  const items: HTMLElement[] = [];

  if (event.kind === "silence") {
    items.push(h("li", null, "Silence is not proof: transmitters fail."));
    items.push(h("li", null, "We count only silences where we were listening."));
  }
  if (event.kind === "jump" || event.kind === "silence") {
    items.push(h("li", null, "Speed limits are class defaults, not measured."));
  }
  items.push(h("li", null, "Positions come from one receiver network, not from radar."));

  return disclosure("What we cannot tell you", items);
}

/* -------------------------------------------------------------------------
 * The panel
 * ---------------------------------------------------------------------- */

export function eventDetail(props: EventDetailProps): HTMLElement {
  const { event, vessels, watch, window: coverage } = props;
  const vessel = vessels.find((v) => v.mmsi === event.mmsi) ?? null;

  return h(
    "section",
    { class: "event-detail" },
    headBlock(props, vessel),
    h("div", { class: "event-detail__block" }, proofPicture(event, vessel, coverage)),
    h(
      "div",
      { class: "event-detail__block" },
      h("h3", { class: "event-detail__heading" }, "The numbers"),
      numbersTable(event),
    ),
    sourcesBlock(watch, coverage),
    caveatsBlock(event),
  );
}
