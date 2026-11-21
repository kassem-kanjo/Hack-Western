/**
 * The right-rail panel for one selected live event: where it is, what happened,
 * the one number that carries it, a small picture of the proof, the numbers
 * behind that picture, and two disclosures.
 *
 * Everything is built with h() (or createElementNS for SVG) so snapshot text is
 * never parsed as markup, and every figure is read defensively from
 * `event.evidence`: evidence keys differ by kind and may be missing.
 *
 * Presentation only: unit formatting and the words stay here; the pictures'
 * geometry and motion live in explainers.ts.
 */

import type { LiveEvent, LiveVessel, LiveWatchlistEntry } from "../model/live";
import {
  KIND_LABEL,
  bigNumber,
  formatDuration,
  timeAgo,
  vesselName,
} from "../model/liveFacts";
import { h } from "./dom";
import {
  cloneExplainer,
  jumpExplainer,
  meetingExplainer,
  silenceExplainer,
  spoofExplainer,
  svgEl,
  type Explainer,
} from "./explainers";

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

/* -------------------------------------------------------------------------
 * Defensive readers
 * ---------------------------------------------------------------------- */

/** Evidence values arrive as unknown JSON; only a real finite number is usable. */
function num(evidence: Record<string, unknown>, key: string): number | null {
  const raw = evidence[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

/**
 * A [lat, lon] pair as the engine writes it (clone clusters, spoof centre).
 * Anything that is not two finite numbers is dropped rather than printed.
 */
function coord(evidence: Record<string, unknown>, key: string): [number, number] | null {
  const raw = evidence[key];
  if (!Array.isArray(raw) || raw.length < 2) return null;
  const lat = raw[0];
  const lon = raw[1];
  if (typeof lat !== "number" || !Number.isFinite(lat)) return null;
  if (typeof lon !== "number" || !Number.isFinite(lon)) return null;
  return [lat, lon];
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

/** A boolean evidence flag; null when the key is absent or not a boolean. */
function flag(evidence: Record<string, unknown>, key: string): boolean | null {
  const raw = evidence[key];
  return typeof raw === "boolean" ? raw : null;
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

/** A value with its unit, or null when the evidence has no such number (the row is then hidden). */
function unitValue(value: number | null, unit: string): string | null {
  return value === null ? null : `${fmt1(value)} ${unit}`;
}

function countValue(value: number | null): string | null {
  return value === null ? null : fmtInt(value);
}

/** "59.44 N, 24.75 E": a position a person can read, in the order the table lists lat/lon. */
function coordValue(value: [number, number] | null): string | null {
  if (value === null) return null;
  const [lat, lon] = value;
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(2)} ${ns}, ${Math.abs(lon).toFixed(2)} ${ew}`;
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

/** A clock time with its zone spelled out, for the numbers table; null when unreadable. */
function clockValue(iso: string): string | null {
  const hm = utcHm(iso);
  return hm === null ? null : `${hm} UTC`;
}

/* -------------------------------------------------------------------------
 * Small DOM helpers
 * ---------------------------------------------------------------------- */

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
    // A missing figure hides the block: a dash is not a finding.
    big.value !== ""
      ? h(
          "div",
          { class: "event-detail__big" },
          h("span", { class: "event-detail__big-value" }, big.value),
          big.tail !== "" ? h("span", { class: "event-detail__big-tail" }, big.tail) : null,
        )
      : null,
    sentence !== "" ? h("p", { class: "event-detail__sentence" }, sentence) : null,
  );
}

/* -------------------------------------------------------------------------
 * 6: proof pictures
 * ---------------------------------------------------------------------- */

/**
 * The replay control under a picture. The icon is a small circular arrow; the
 * word beside it keeps the control findable without a tooltip.
 */
function replayFoot(replay: () => void): HTMLElement {
  const icon = svgEl("svg", {
    viewBox: "0 0 16 16",
    width: 13,
    height: 13,
    class: "event-detail__replay-icon",
    "aria-hidden": "true",
  });
  icon.append(
    svgEl("path", { d: "M2.6 8a5.4 5.4 0 1 0 1.6-3.8" }),
    svgEl("path", { d: "M2.4 2.6v3.8h3.8" }),
  );

  const button = h(
    "button",
    {
      type: "button",
      class: "event-detail__replay",
      "aria-label": "Replay this explanation",
      title: "Replay",
    },
    icon,
    "Replay",
  );
  button.addEventListener("click", () => replay());

  return h("div", { class: "event-detail__proof-foot" }, button);
}

/** Picture, the words that carry it, and its replay control, in that order. */
function proofBlock(scene: Explainer, ...body: Array<HTMLElement | null>): HTMLElement {
  return h("div", { class: "event-detail__proof" }, scene.el, ...body, replayFoot(scene.replay));
}

function jumpProof(event: LiveEvent): HTMLElement {
  // Engine keys (core/kinematics.py jumps): km, minutes, implied_kn, cap_kn, ratio.
  const km = num(event.evidence, "km");
  const minutes = num(event.evidence, "minutes");
  const implied = num(event.evidence, "implied_kn");
  const cap = num(event.evidence, "cap_kn");
  if (
    km === null ||
    minutes === null ||
    implied === null ||
    cap === null ||
    implied <= 0 ||
    cap <= 0
  ) {
    return missingPicture();
  }

  const scene = jumpExplainer({
    neededLabel: `Speed needed to cover ${fmt1(km)} km in ${fmtInt(minutes)} min`,
    neededValue: `${fmt1(implied)} kn`,
    capLabel: "Fastest this ship can go",
    capValue: `${fmt1(cap)} kn`,
    capFraction: cap / implied,
    ratio: num(event.evidence, "ratio") ?? implied / cap,
  });

  return proofBlock(
    scene,
    h(
      "p",
      { class: "event-detail__caption" },
      "The ship cannot have made this trip. The two positions came from different transmitters, or one was false.",
    ),
  );
}

/**
 * The axis is labelled with the gap itself: last heard on the left, heard again
 * on the right. That is what the picture is about, and its two labels are then
 * the two event times rather than two ends of a 24 h window that would squeeze
 * the gap into a sliver. The coverage window is only a fallback for an unusable
 * event span.
 */
function timeAxis(
  event: LiveEvent,
  coverage: { start: string; end: string },
): { start: string; end: string } | null {
  const eventStart = Date.parse(event.start);
  const eventEnd = Date.parse(event.end);
  if (Number.isFinite(eventStart) && Number.isFinite(eventEnd) && eventEnd > eventStart) {
    return { start: event.start, end: event.end };
  }
  const windowStart = Date.parse(coverage.start);
  const windowEnd = Date.parse(coverage.end);
  if (Number.isFinite(windowStart) && Number.isFinite(windowEnd) && windowEnd > windowStart) {
    return { start: coverage.start, end: coverage.end };
  }
  return null;
}

function silenceProof(
  event: LiveEvent,
  coverage: { start: string; end: string },
): HTMLElement {
  const minutes = num(event.evidence, "minutes");
  const axis = timeAxis(event, coverage);
  if (minutes === null || axis === null) return missingPicture();

  const scene = silenceExplainer({
    gapLabel: formatDuration(minutes),
    startLabel: utcHm(axis.start) ?? "",
    endLabel: utcHm(axis.end) ?? "",
  });

  const others = num(event.evidence, "local_other_vessels");
  const gapWords = formatDuration(minutes);
  const caption =
    others !== null
      ? h(
          "p",
          { class: "event-detail__caption" },
          "Our receivers kept hearing about ",
          h("span", { class: "event-detail__mono" }, fmtInt(others)),
          " other ships nearby. Only this one stopped, for ",
          h("span", { class: "event-detail__mono" }, gapWords),
          ".",
        )
      : h(
          "p",
          { class: "event-detail__caption" },
          "Our receivers kept hearing other ships nearby. Only this one stopped, for ",
          h("span", { class: "event-detail__mono" }, gapWords),
          ".",
        );

  return proofBlock(scene, caption);
}

function cloneProof(event: LiveEvent): HTMLElement {
  // Engine keys (core/kinematics.py _clone_event): cluster_a, cluster_b, separation_km, flips.
  const separation = num(event.evidence, "separation_km");
  if (separation === null) return missingPicture();

  const scene = cloneExplainer({
    separationLabel: `${fmt1(separation)} km`,
    flips: num(event.evidence, "flips"),
  });

  // The clusters' own coordinates are the proof the two places are real places.
  const clusterA = coordValue(coord(event.evidence, "cluster_a"));
  const clusterB = coordValue(coord(event.evidence, "cluster_b"));
  const places =
    clusterA !== null || clusterB !== null
      ? h(
          "p",
          { class: "event-detail__caption" },
          clusterA !== null ? `A ${clusterA}` : null,
          clusterA !== null && clusterB !== null ? " \u00b7 " : null,
          clusterB !== null ? `B ${clusterB}` : null,
        )
      : null;

  const flips = countValue(num(event.evidence, "flips"));
  const sentence =
    flips !== null
      ? h(
          "p",
          { class: "event-detail__caption" },
          "One ID, two places, ",
          h("span", { class: "event-detail__mono" }, flips),
          " switches.",
        )
      : null;

  return proofBlock(scene, sentence, places);
}

function meetingProof(event: LiveEvent, vessels: LiveVessel[]): HTMLElement {
  // Engine keys (core/encounters.py): min_distance_m, max_distance_m, minutes, mean_sog.
  const closest = num(event.evidence, "min_distance_m");
  const minutes = num(event.evidence, "minutes");
  const sog = num(event.evidence, "mean_sog");
  if (closest === null || minutes === null) return missingPicture();

  const scene = meetingExplainer({
    closestM: closest,
    widestM: num(event.evidence, "max_distance_m") ?? closest,
    minutes,
  });

  const other = event.other_mmsi;

  return proofBlock(
    scene,
    other !== null
      ? h(
          "p",
          { class: "event-detail__caption" },
          "The other ship: ",
          h("span", { class: "event-detail__mono" }, vesselName(other, vessels)),
          ` (MMSI ${other}).`,
        )
      : null,
    h(
      "p",
      { class: "event-detail__caption" },
      // The speed is a qualifier, not the headline; it stays in the sentence.
      sog !== null
        ? `Two ships stayed this close and this still, at ${fmt1(sog)} kn.`
        : "Two ships stayed this close and this still.",
    ),
  );
}

function spoofProof(event: LiveEvent, vessels: LiveVessel[]): HTMLElement {
  // Engine keys (core/spoofing.py): vessels [{mmsi, n_reports, first, last}], n_vessels, on_land, inland_km.
  const mmsis = evidenceMmsis(event.evidence);
  if (mmsis.length === 0) return missingPicture();

  // n_vessels is authoritative; the MMSI list is the fallback (same rule as bigNumber).
  const ships = num(event.evidence, "n_vessels") ?? mmsis.length;
  const scene = spoofExplainer({ ships: Math.max(1, Math.round(ships)) });

  const list = h("ul", { class: "event-detail__mmsis" });
  for (const mmsi of mmsis) {
    list.append(
      h(
        "li",
        { class: "event-detail__mmsi" },
        h("span", { class: "event-detail__mmsi-name" }, vesselName(mmsi, vessels)),
        h("span", { class: "event-detail__mmsi-id" }, `MMSI ${mmsi}`),
      ),
    );
  }

  const onLand = flag(event.evidence, "on_land");

  return proofBlock(
    scene,
    list,
    h(
      "p",
      { class: "event-detail__caption" },
      onLand === true
        ? "These ships all reported one position that lies inside land, where no ship can be."
        : "These ships all reported one position that cannot be real.",
    ),
  );
}

function proofPicture(
  event: LiveEvent,
  coverage: { start: string; end: string },
  vessels: LiveVessel[],
): HTMLElement {
  switch (event.kind) {
    case "jump":
      return jumpProof(event);
    case "silence":
      return silenceProof(event, coverage);
    case "clone":
      return cloneProof(event);
    case "meeting":
      return meetingProof(event, vessels);
    default:
      return spoofProof(event, vessels);
  }
}

/* -------------------------------------------------------------------------
 * 7: the numbers
 * ---------------------------------------------------------------------- */

function tableRow(label: string, value: string | null): HTMLElement | null {
  if (value === null) return null;
  return h("div", { class: "event-detail__row" }, h("dt", null, label), h("dd", null, value));
}

/**
 * The numbers behind the picture, read with the engine's own key names. A row
 * whose key is absent is dropped: the table never prints a dash.
 */
function numbersTable(event: LiveEvent): HTMLElement | null {
  const evidence = event.evidence;
  const rows: Array<[string, string | null]> = [];

  if (event.kind === "jump") {
    rows.push(["Distance between positions", unitValue(num(evidence, "km"), "km")]);
    rows.push(["Time between them", unitValue(num(evidence, "minutes"), "min")]);
    rows.push(["Speed needed", unitValue(num(evidence, "implied_kn"), "kn")]);
    rows.push(["Class maximum", unitValue(num(evidence, "cap_kn"), "kn")]);
  } else if (event.kind === "silence") {
    const minutes = num(evidence, "minutes");
    rows.push(["Silent for", minutes === null ? null : formatDuration(minutes)]);
    rows.push(["Last heard", clockValue(event.start)]);
    rows.push(["Heard again", clockValue(event.end)]);
    rows.push(["Implied speed across the gap", unitValue(num(evidence, "implied_kn"), "kn")]);
    rows.push([
      "Distance between last and next fix",
      unitValue(num(evidence, "distance_km"), "km"),
    ]);
    rows.push(["Other ships nearby", countValue(num(evidence, "local_other_vessels"))]);
  } else if (event.kind === "clone") {
    rows.push(["Separation", unitValue(num(evidence, "separation_km"), "km")]);
    rows.push(["Switches", countValue(num(evidence, "flips"))]);
    rows.push(["Max implied speed", unitValue(num(evidence, "max_implied_kn"), "kn")]);
    rows.push(["Position A", coordValue(coord(evidence, "cluster_a"))]);
    rows.push(["Position B", coordValue(coord(evidence, "cluster_b"))]);
  } else if (event.kind === "meeting") {
    rows.push(["Closest distance", unitValue(num(evidence, "min_distance_m"), "m")]);
    rows.push(["Widest distance", unitValue(num(evidence, "max_distance_m"), "m")]);
    rows.push(["Duration", unitValue(num(evidence, "minutes"), "min")]);
    rows.push(["Mean speed", unitValue(num(evidence, "mean_sog"), "kn")]);
    rows.push(["Offshore", unitValue(num(evidence, "offshore_km"), "km")]);
    const twin = flag(evidence, "twin_transponders");
    rows.push(["One hull, two transponders", twin === true ? "Yes" : null]);
  } else {
    const mmsis = evidenceMmsis(evidence);
    const ships = num(evidence, "n_vessels") ?? (mmsis.length > 0 ? mmsis.length : null);
    rows.push(["Ships", countValue(ships)]);
    const onLand = flag(evidence, "on_land");
    rows.push(["Inside land", onLand === null ? null : onLand ? "Yes" : "No"]);
    // inland_km comes from a coarse coastline without lakes; we show only "on land", not a distance.
    rows.push(["Ghost position", coordValue(coord(evidence, "centre"))]);
  }

  const built: HTMLElement[] = [];
  for (const [label, value] of rows) {
    const row = tableRow(label, value);
    if (row !== null) built.push(row);
  }
  if (built.length === 0) return null;

  return h("dl", { class: "event-detail__table" }, ...built);
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
  const table = numbersTable(event);

  return h(
    "section",
    { class: "event-detail" },
    headBlock(props, vessel),
    h("div", { class: "event-detail__block" }, proofPicture(event, coverage, vessels)),
    table !== null
      ? h(
          "div",
          { class: "event-detail__block" },
          h("h3", { class: "event-detail__heading" }, "The numbers"),
          table,
        )
      : null,
    sourcesBlock(watch, coverage),
    caveatsBlock(event),
  );
}
