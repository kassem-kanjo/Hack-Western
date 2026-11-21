/**
 * Event proof explainers: small SVG pictures that play once when the event
 * detail opens and can be replayed from one button.
 *
 * EventDetail owns the evidence and the words. These functions own geometry,
 * motion and frame arithmetic only. Every picture is a pure function of a
 * progress value t in [0, 1], so a replay is just drawing t = 0 again, and the
 * final frame is the same picture the reader is left with.
 *
 * Motion appears only where it explains: bars growing past a limit, a track
 * stopping, a loop being drawn, two dots closing, one dot flipping. Nothing
 * loops and nothing idles. Under prefers-reduced-motion every picture is drawn
 * once in its final state.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

/** How long the opening animation runs before the picture is left static. */
const MOTION_MS = 1400;

export interface Explainer {
  /** The picture itself; appended to the proof block in EventDetail. */
  el: SVGSVGElement;
  /** Draw frame 0 again and play through to the static final frame. */
  replay: () => void;
}

/* -------------------------------------------------------------------------
 * Drawing helpers
 * ---------------------------------------------------------------------- */

export function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag) as SVGElementTagNameMap[K];
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  return el;
}

/** Every picture's viewBox is 360 wide, so text below scales to ~12 px in the rail. */
const W = 360;

function sceneSvg(height: number): SVGSVGElement {
  return svgEl("svg", {
    class: "event-detail__scene",
    viewBox: `0 0 ${W} ${height}`,
    preserveAspectRatio: "xMidYMid meet",
    "aria-hidden": "true",
  });
}

function label(
  x: number,
  y: number,
  value: string,
  cls: string,
  anchor: "start" | "middle" | "end" = "start",
): SVGTextElement {
  const el = svgEl("text", {
    x,
    y,
    class: cls,
    ...(anchor === "start" ? {} : { "text-anchor": anchor }),
  });
  el.textContent = value;
  return el;
}

/** 0 before `from`, 1 after `to`, linear in between. */
function span(t: number, from: number, to: number): number {
  if (to <= from) return t >= to ? 1 : 0;
  return Math.min(1, Math.max(0, (t - from) / (to - from)));
}

function easeOut(p: number): number {
  return 1 - (1 - p) ** 3;
}

function easeInOut(p: number): number {
  return p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2;
}

function reducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Run `draw` once from 0 to 1 over MOTION_MS. Returns a restart function the
 * replay button can call at any time, mid-play included.
 */
function animate(draw: (t: number) => void): () => void {
  let frame = 0;
  let started: number | null = null;

  const step = (now: number) => {
    if (started === null) started = now;
    const t = Math.min(1, (now - started) / MOTION_MS);
    draw(t);
    if (t < 1) frame = requestAnimationFrame(step);
  };

  const play = () => {
    cancelAnimationFrame(frame);
    started = null;
    if (reducedMotion()) {
      draw(1);
      return;
    }
    draw(0);
    frame = requestAnimationFrame(step);
  };

  play();
  return play;
}

let hatchSeq = 0;

/* -------------------------------------------------------------------------
 * Jump: how far past the ship's own limit the gap demands
 * ---------------------------------------------------------------------- */

export interface JumpScene {
  /** "Speed needed to cover 38 km in 6 min" */
  neededLabel: string;
  /** "380 kn" */
  neededValue: string;
  /** "Fastest this ship can go" */
  capLabel: string;
  /** "16 kn" */
  capValue: string;
  /** cap / implied: the second bar's share of the first, clamped to (0, 1]. */
  capFraction: number;
  /** implied / cap: the multiple the counter counts up to. */
  ratio: number;
}

export function jumpExplainer(scene: JumpScene): Explainer {
  const svg = sceneSvg(126);

  svg.append(label(0, 14, scene.neededLabel, "event-detail__scene-label"));
  svg.append(label(W, 14, scene.neededValue, "event-detail__scene-value", "end"));

  const track1 = svgEl("rect", {
    x: 0,
    y: 22,
    width: W,
    height: 8,
    rx: 1,
    class: "event-detail__scene-track",
  });
  const fill1 = svgEl("rect", {
    x: 0,
    y: 22,
    width: 0,
    height: 8,
    rx: 1,
    class: "event-detail__scene-fill",
  });

  svg.append(label(0, 60, scene.capLabel, "event-detail__scene-label"));
  svg.append(label(W, 60, scene.capValue, "event-detail__scene-value", "end"));

  const track2 = svgEl("rect", {
    x: 0,
    y: 68,
    width: W,
    height: 8,
    rx: 1,
    class: "event-detail__scene-track",
  });
  const capWidth = Math.max(2, Math.min(W, W * scene.capFraction));
  const fill2 = svgEl("rect", {
    x: 0,
    y: 68,
    width: 0,
    height: 8,
    rx: 1,
    class: "event-detail__scene-fill is-muted",
  });

  // The multiple and its words share one text line, so they always sit close
  // together whatever the size of the number.
  const ratioLine = svgEl("text", { x: 0, y: 108, class: "event-detail__scene-note" });
  const ratioText = svgEl("tspan", { class: "event-detail__scene-ratio" });
  ratioText.textContent = "";
  const ratioWords = svgEl("tspan", { dx: 6 });
  ratioWords.textContent = "the ship's top speed";
  ratioLine.append(ratioText, ratioWords);
  svg.append(ratioLine);

  svg.append(track1, fill1, track2, fill2);

  const target = Math.max(1, Math.round(scene.ratio));

  const draw = (t: number): void => {
    fill2.setAttribute("width", String(capWidth * easeOut(span(t, 0, 0.4))));
    fill1.setAttribute("width", String(W * easeOut(span(t, 0.18, 0.8))));
    const count = 1 + Math.round((target - 1) * span(t, 0.35, 0.85));
    ratioText.textContent = `\u00d7${count}`;
  };

  return { el: svg, replay: animate(draw) };
}

/* -------------------------------------------------------------------------
 * Silence: one ship stops, every other ship keeps being heard
 * ---------------------------------------------------------------------- */

export interface SilenceScene {
  /** "4 h 12 min" */
  gapLabel: string;
  /** "11:20" at the last fix. */
  startLabel: string;
  /** "15:32" at the next fix (UTC is added here). */
  endLabel: string;
}

/**
 * The event span is the whole gap, so a drawing of the literal axis would hide
 * the argument (a dot row with no dots). A schematic lead-in and lead-out give
 * the two tracks something to stop from and resume into; the axis labels still
 * mark the exact silence boundaries, and the caption carries the times.
 */
const LEAD = 0.16;

export function silenceExplainer(scene: SilenceScene): Explainer {
  const svg = sceneSvg(104);

  const x0 = 116;
  const x1 = 352;
  const width = x1 - x0;
  const gapLeft = x0 + width * LEAD;
  const gapWidth = width * (1 - 2 * LEAD);

  svg.append(label(0, 24, "This ship", "event-detail__scene-label"));
  svg.append(label(0, 52, "Ships within", "event-detail__scene-label"));
  svg.append(label(0, 66, "60 km", "event-detail__scene-label"));

  // This ship: dots everywhere it was heard, none inside the silence. The gap
  // is a hole in the row, not a bar over it.
  const dots: Array<{ el: SVGCircleElement; frac: number }> = [];
  for (let x = x0 + 3; x <= x1; x += 7) {
    const frac = (x - x0) / width;
    if (frac > LEAD && frac < 1 - LEAD) continue;
    const dot = svgEl("circle", {
      cx: x,
      cy: 20,
      r: 1.7,
      class: "event-detail__scene-dot",
    });
    dot.style.opacity = "0";
    dots.push({ el: dot, frac });
    svg.append(dot);
  }

  // The amber block with its hatching: the silent minutes themselves.
  hatchSeq += 1;
  const patternId = `event-hatch-${hatchSeq}`;
  const pattern = svgEl("pattern", {
    id: patternId,
    width: 6,
    height: 6,
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(45)",
  });
  pattern.append(svgEl("line", { x1: 0, y1: 0, x2: 0, y2: 6, class: "event-detail__scene-hatch" }));
  const defs = svgEl("defs", {});
  defs.append(pattern);
  svg.append(defs);

  const gap = svgEl("g", {});
  gap.append(
    svgEl("rect", {
      x: gapLeft,
      y: 8,
      width: gapWidth,
      height: 26,
      fill: `url(#${patternId})`,
    }),
    svgEl("rect", {
      x: gapLeft,
      y: 8,
      width: gapWidth,
      height: 26,
      class: "event-detail__scene-gap-box",
    }),
  );

  const gapCentre = gapLeft + gapWidth / 2;
  const labelWidth = Math.min(gapWidth, scene.gapLabel.length * 6.8 + 10);
  gap.append(
    svgEl("rect", {
      x: gapCentre - labelWidth / 2,
      y: 13,
      width: labelWidth,
      height: 16,
      class: "event-detail__scene-gap-backing",
    }),
    label(gapCentre, 25, scene.gapLabel, "event-detail__scene-gap-label", "middle"),
  );
  gap.style.opacity = "0";
  svg.append(gap);

  // Other ships: dense thin ticks across the whole axis, silence included.
  const ticks: Array<{ el: SVGLineElement; frac: number }> = [];
  for (let x = x0; x <= x1; x += 4) {
    const frac = (x - x0) / width;
    const tick = svgEl("line", {
      x1: x,
      y1: 46,
      x2: x,
      y2: 68,
      class: "event-detail__scene-tick",
    });
    tick.style.opacity = "0";
    ticks.push({ el: tick, frac });
    svg.append(tick);
  }

  svg.append(
    label(x0, 92, scene.startLabel, "event-detail__scene-note event-detail__scene-mono"),
  );
  svg.append(
    label(
      x1,
      92,
      `${scene.endLabel} UTC`,
      "event-detail__scene-note event-detail__scene-mono",
      "end",
    ),
  );

  const draw = (t: number): void => {
    for (const dot of dots) dot.el.style.opacity = dot.frac <= t + 0.001 ? "1" : "0";
    for (const tick of ticks) tick.el.style.opacity = tick.frac <= t + 0.001 ? "1" : "0";
    // The block appears exactly as the sweep reaches the last fix.
    gap.style.opacity = String(span(t, LEAD, LEAD + 0.1));
  };

  return { el: svg, replay: animate(draw) };
}

/* -------------------------------------------------------------------------
 * Spoof: several ships, one shared position, every track out and back
 * ---------------------------------------------------------------------- */

export interface SpoofScene {
  /** Number of ships that reported the same impossible point. */
  ships: number;
}

interface Point {
  x: number;
  y: number;
}

const SPOOF_CENTRE: Point = { x: 180, y: 64 };
const SPOOF_HOMES: Point[] = [
  { x: 30, y: 98 },
  { x: 330, y: 98 },
  { x: 30, y: 30 },
  { x: 330, y: 30 },
  { x: 180, y: 120 },
];

function quad(a: Point, c: Point, b: Point, steps: number): Point[] {
  const points: Point[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const u = i / steps;
    const v = 1 - u;
    points.push({
      x: v * v * a.x + 2 * v * u * c.x + u * u * b.x,
      y: v * v * a.y + 2 * v * u * c.y + u * u * b.y,
    });
  }
  return points;
}

/** An out-and-back loop: two mirrored arcs home -> point -> home. */
function loop(home: Point, centre: Point, bend: number): Point[] {
  const mx = (home.x + centre.x) / 2;
  const my = (home.y + centre.y) / 2;
  const dx = centre.x - home.x;
  const dy = centre.y - home.y;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;

  const out = quad(home, { x: mx + nx * bend, y: my + ny * bend }, centre, 18);
  const back = quad(centre, { x: mx - nx * bend, y: my - ny * bend }, home, 18);
  return out.concat(back.slice(1));
}

function pointAt(points: Point[], fraction: number): Point {
  const lengths: number[] = [];
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    const step = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    lengths.push(step);
    total += step;
  }

  let left = Math.min(1, Math.max(0, fraction)) * total;
  for (let i = 1; i < points.length; i += 1) {
    const step = lengths[i - 1];
    if (left <= step) {
      const u = step === 0 ? 0 : left / step;
      return {
        x: points[i - 1].x + (points[i].x - points[i - 1].x) * u,
        y: points[i - 1].y + (points[i].y - points[i - 1].y) * u,
      };
    }
    left -= step;
  }
  return points[points.length - 1];
}

export function spoofExplainer(scene: SpoofScene): Explainer {
  const svg = sceneSvg(136);
  const ships = Math.max(1, Math.min(scene.ships, SPOOF_HOMES.length));

  const tracks: Array<{
    polyline: SVGPolylineElement;
    dot: SVGCircleElement;
    points: Point[];
  }> = [];

  for (let i = 0; i < ships; i += 1) {
    const home = SPOOF_HOMES[i];
    const points = loop(home, SPOOF_CENTRE, 14);

    const polyline = svgEl("polyline", {
      points: points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "),
      pathLength: 100,
      class: "event-detail__scene-candidate-line",
      "stroke-dasharray": "100",
      "stroke-dashoffset": "100",
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    });

    const dot = svgEl("circle", {
      cx: home.x,
      cy: home.y,
      r: 3,
      class: "event-detail__scene-candidate",
    });
    dot.style.opacity = "0";

    svg.append(polyline, dot);
    svg.append(
      svgEl("circle", { cx: home.x, cy: home.y, r: 2.4, class: "event-detail__scene-ais" }),
    );

    tracks.push({ polyline, dot, points });
  }

  // The shared (impossible) point, drawn like the map's crosshair.
  svg.append(
    svgEl("circle", {
      cx: SPOOF_CENTRE.x,
      cy: SPOOF_CENTRE.y,
      r: 7,
      class: "event-detail__scene-centre",
    }),
  );
  for (const [x1, y1, x2, y2] of [
    [SPOOF_CENTRE.x, SPOOF_CENTRE.y - 11, SPOOF_CENTRE.x, SPOOF_CENTRE.y - 17],
    [SPOOF_CENTRE.x, SPOOF_CENTRE.y + 11, SPOOF_CENTRE.x, SPOOF_CENTRE.y + 17],
    [SPOOF_CENTRE.x - 11, SPOOF_CENTRE.y, SPOOF_CENTRE.x - 17, SPOOF_CENTRE.y],
    [SPOOF_CENTRE.x + 11, SPOOF_CENTRE.y, SPOOF_CENTRE.x + 17, SPOOF_CENTRE.y],
  ] as const) {
    svg.append(svgEl("line", { x1, y1, x2, y2, class: "event-detail__scene-cross" }));
  }

  svg.append(
    label(SPOOF_CENTRE.x, 18, "one shared position", "event-detail__scene-note", "middle"),
  );

  const slice = 0.85 / ships;

  const draw = (t: number): void => {
    tracks.forEach((track, i) => {
      const local = easeInOut(span(t, 0.05 + i * slice, 0.05 + (i + 1) * slice));
      track.polyline.setAttribute("stroke-dashoffset", String(100 * (1 - local)));
      if (local <= 0 || local >= 1) {
        track.dot.style.opacity = "0";
        return;
      }
      const p = pointAt(track.points, local);
      track.dot.setAttribute("cx", String(p.x));
      track.dot.setAttribute("cy", String(p.y));
      track.dot.style.opacity = "1";
    });
  };

  return { el: svg, replay: animate(draw) };
}

/* -------------------------------------------------------------------------
 * Meeting: two dots close, hold, and the hold is counted
 * ---------------------------------------------------------------------- */

export interface MeetingScene {
  /** Closest approach, in metres. */
  closestM: number;
  /** Widest separation during the encounter, in metres (closest when missing). */
  widestM: number;
  minutes: number;
}

function metres(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)} km` : `${Math.round(value)} m`;
}

export function meetingExplainer(scene: MeetingScene): Explainer {
  const svg = sceneSvg(110);

  const y = 56;
  const startLeft = 96;
  const endLeft = 162;
  const startRight = 264;
  const endRight = 198;

  const ringLeft = svgEl("circle", {
    cx: startLeft,
    cy: y,
    r: 9,
    class: "event-detail__scene-rdv-ring",
  });
  const ringRight = svgEl("circle", {
    cx: startRight,
    cy: y,
    r: 9,
    class: "event-detail__scene-rdv-ring",
  });
  const dotLeft = svgEl("circle", {
    cx: startLeft,
    cy: y,
    r: 4.6,
    class: "event-detail__scene-rdv",
  });
  const dotRight = svgEl("circle", {
    cx: startRight,
    cy: y,
    r: 4.6,
    class: "event-detail__scene-rdv",
  });

  const distanceText = label(W / 2, 24, "", "event-detail__scene-value", "middle");
  const counterText = label(W / 2, 96, "", "event-detail__scene-value", "middle");

  svg.append(ringLeft, ringRight, dotLeft, dotRight, distanceText, counterText);

  const draw = (t: number): void => {
    const move = easeInOut(span(t, 0, 0.5));
    const left = startLeft + (endLeft - startLeft) * move;
    const right = startRight + (endRight - startRight) * move;

    for (const [ring, cx] of [
      [ringLeft, left],
      [ringRight, right],
    ] as const) {
      ring.setAttribute("cx", String(cx));
    }
    dotLeft.setAttribute("cx", String(left));
    dotRight.setAttribute("cx", String(right));

    const distance = scene.widestM + (scene.closestM - scene.widestM) * move;
    distanceText.textContent = `${metres(distance)} apart`;

    const hold = Math.round(scene.minutes * span(t, 0.5, 0.98));
    counterText.textContent = `${hold} min together`;
  };

  return { el: svg, replay: animate(draw) };
}

/* -------------------------------------------------------------------------
 * Clone: one transmitter, two places, counted switches
 * ---------------------------------------------------------------------- */

export interface CloneScene {
  /** "38 km" */
  separationLabel: string;
  /** Real switch count; the dot shows at most six of them. */
  flips: number | null;
}

function switches(count: number): string {
  return `${count} ${count === 1 ? "switch" : "switches"}`;
}

export function cloneExplainer(scene: CloneScene): Explainer {
  const svg = sceneSvg(126);

  const a = { x: 48, y: 92 };
  const b = { x: 312, y: 36 };
  const midX = (a.x + b.x) / 2;
  const midY = (a.y + b.y) / 2;

  svg.append(
    svgEl("line", {
      x1: a.x,
      y1: a.y,
      x2: b.x,
      y2: b.y,
      class: "event-detail__scene-rule",
    }),
  );

  for (const place of [a, b]) {
    svg.append(
      svgEl("rect", {
        x: place.x - 4.5,
        y: place.y - 4.5,
        width: 9,
        height: 9,
        class: "event-detail__scene-place",
      }),
    );
  }

  svg.append(
    label(a.x, a.y + 24, "Position A", "event-detail__scene-note", "middle"),
    label(b.x, b.y - 14, "Position B", "event-detail__scene-note", "middle"),
    label(midX, midY - 10, scene.separationLabel, "event-detail__scene-value is-strong", "middle"),
  );

  const counterText =
    scene.flips === null
      ? null
      : label(midX, 22, switches(Math.round(scene.flips)), "event-detail__scene-note", "middle");
  if (counterText !== null) svg.append(counterText);

  const dot = svgEl("circle", { cx: a.x, cy: a.y, r: 4.5, class: "event-detail__scene-candidate" });
  svg.append(dot);

  const flips = scene.flips;
  const hops = Math.max(1, Math.min(6, Math.round(flips ?? 2)));

  const draw = (t: number): void => {
    const index = Math.min(hops, Math.floor(t * hops + 0.0001));
    const atB = index % 2 === 1;
    dot.setAttribute("cx", String(atB ? b.x : a.x));
    dot.setAttribute("cy", String(atB ? b.y : a.y));
    if (counterText !== null && flips !== null) {
      counterText.textContent = switches(Math.round(flips * t));
    }
  };

  return { el: svg, replay: animate(draw) };
}
