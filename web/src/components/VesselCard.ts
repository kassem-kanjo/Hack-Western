/**
 * The right-rail panel for one vessel: who the transponder says it is, where it
 * was last heard, what we found around it, and why it is on a watchlist.
 *
 * Identity here is deliberately reported as broadcast: the name and IMO come
 * from the ship's own static messages, so the card never presents them as
 * verified registry facts. The watchlist block keeps the same honesty: it names
 * the category, the list text and the datasets, and links the source.
 */

import type { LiveEvent, LiveVessel, LiveWatchlistEntry } from "../model/live";
import { KIND_LABEL, UNNAMED_VESSEL, snapshotNow, timeAgo } from "../model/liveFacts";
import { h } from "./dom";

export interface VesselCardProps {
  vessel: LiveVessel;
  /** This vessel's findings, already in severity order. */
  events: LiveEvent[];
  /** The strongest watchlist entry for this hull, or null. */
  watch: LiveWatchlistEntry | null;
  /** Hash back to the ranked list, e.g. "#/live/BALTIC_WATCH". */
  backHref: string;
  /** Label for the breadcrumb, e.g. "Baltic". */
  regionLabel: string;
  /**
   * The region snapshot's `generated_at` (ISO). Every "last seen ..." on the
   * card counts back from this, not from the browser clock, so the recording
   * reads the same whenever it is opened.
   */
  generatedAt: string;
  /** Hash for one of this vessel's events; null when it has no route. */
  eventHref: (event: LiveEvent) => string | null;
}

const CATEGORY_LABEL: Record<string, string> = {
  sanctioned: "Sanctioned",
  shadow_fleet: "Shadow fleet",
  detained: "Detained",
};

function categoryLabel(category: string): string {
  const known = CATEGORY_LABEL[category];
  if (known !== undefined) return known;
  const words = category.replace(/_/g, " ").trim();
  return words === "" ? "Listed" : words.charAt(0).toUpperCase() + words.slice(1);
}

/** "59.44 N, 24.75 E", the same order the event table uses. */
function coordText(lat: number, lon: number): string {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(2)} ${ns}, ${Math.abs(lon).toFixed(2)} ${ew}`;
}

/** The ship class the feed knows, with the raw AIS type code as a quiet qualifier. */
function typeText(vessel: LiveVessel): string | null {
  const shipClass = vessel.class.trim();
  const known = shipClass !== "" && shipClass !== "default";
  if (vessel.ship_type === null) {
    return known ? shipClass.charAt(0).toUpperCase() + shipClass.slice(1) : null;
  }
  if (!known) return `AIS type ${vessel.ship_type}`;
  return `${shipClass.charAt(0).toUpperCase() + shipClass.slice(1)} (code ${vessel.ship_type})`;
}

function row(label: string, value: string | null): HTMLElement | null {
  if (value === null || value === "") return null;
  return h("div", { class: "vessel-card__row" }, h("dt", null, label), h("dd", null, value));
}

function headBlock(props: VesselCardProps, name: string): HTMLElement {
  const { vessel, backHref, regionLabel } = props;
  const last = vessel.last;
  // Times are read against the recording, not the wall clock: an unreadable
  // snapshot time leaves the relative age off rather than guessing.
  const nowMs = snapshotNow(props.generatedAt);
  const seen = last === null || nowMs === null ? "" : timeAgo(last.t, nowMs);
  const position =
    last === null
      ? "No position broadcast in the recording"
      : `${coordText(last.lat, last.lon)}${seen === "" ? "" : ` \u00b7 last seen ${seen}`}`;

  const facts = [
    row("MMSI", String(vessel.mmsi)),
    // The ship types this number; we did not verify it.
    row("IMO as broadcast by the ship", vessel.imo === null ? "Not broadcast" : String(vessel.imo)),
    row("Type", typeText(vessel)),
    row("Last position", position),
  ].filter((entry): entry is HTMLElement => entry !== null);

  return h(
    "div",
    { class: "vessel-card__head" },
    h(
      "div",
      { class: "vessel-card__crumbs" },
      h("a", { class: "vessel-card__back", href: backHref }, `\u2039 ${regionLabel}`),
      h("span", { class: "vessel-card__crumb-sep" }, " / Vessels"),
    ),
    h(
      "div",
      { class: "vessel-card__identity" },
      h("h2", { class: "vessel-card__name" }, name),
      h("span", { class: "vessel-card__broadcast" }, "name as broadcast"),
    ),
    h("dl", { class: "vessel-card__facts" }, ...facts),
  );
}

function eventsBlock(props: VesselCardProps): HTMLElement {
  const items: HTMLElement[] = [];
  // Same snapshot clock as the head: "12 min ago" means 12 minutes before the
  // recording was written, whichever day the card is opened on.
  const nowMs = snapshotNow(props.generatedAt);

  if (props.events.length === 0) {
    items.push(h("li", { class: "vessel-card__none" }, "No unusual events in this recording."));
  }

  for (const event of props.events) {
    const href = props.eventHref(event);
    const why = event.explanation.trim();
    const body = h(
      "span",
      { class: "vessel-card__event-main" },
      h(
        "span",
        { class: "vessel-card__event-head" },
        h("span", { class: "vessel-card__event-kind" }, KIND_LABEL[event.kind]),
        nowMs === null
          ? null
          : h("span", { class: "vessel-card__event-time" }, timeAgo(event.start, nowMs)),
      ),
      why === "" ? null : h("span", { class: "vessel-card__event-why" }, why),
    );

    items.push(
      h(
        "li",
        null,
        href === null
          ? h("div", { class: "vessel-card__event" }, body)
          : h("a", { class: "vessel-card__event", href }, body),
      ),
    );
  }

  return h(
    "section",
    { class: "vessel-card__block" },
    h(
      "div",
      { class: "vessel-card__heading" },
      h("h3", null, "Its events"),
      h("span", { class: "meta" }, String(props.events.length)),
    ),
    h("ul", { class: "vessel-card__events" }, ...items),
  );
}

function watchBlock(watch: LiveWatchlistEntry, vessel: LiveVessel): HTMLElement {
  const isAlert = watch.category === "sanctioned" || watch.category === "shadow_fleet";
  const listName = watch.entry.caption?.trim() ?? "";
  const broadcast = vessel.name?.trim() ?? "";
  // Only claim "listed as" when the list spells the hull differently from the
  // transponder; otherwise the two names are the same fact twice. Case is not
  // a difference a reader needs to reconcile.
  const differing =
    listName !== "" && listName.toLocaleUpperCase() !== broadcast.toLocaleUpperCase();

  const datasets = Array.isArray(watch.entry.datasets)
    ? watch.entry.datasets.filter((name) => typeof name === "string" && name.trim() !== "")
    : [];
  const url = watch.entry.url?.trim() ?? "";

  const rows = [
    row("Category", categoryLabel(watch.category)),
    row("List name", listName === "" ? null : differing ? `listed as ${listName}` : listName),
    row("Datasets", datasets.length === 0 ? null : datasets.join(" \u00b7 ")),
  ].filter((entry): entry is HTMLElement => entry !== null);

  return h(
    "section",
    { class: `vessel-card__watch${isAlert ? "" : " is-info"}` },
    h("h3", { class: "vessel-card__watch-title" }, "On the watchlist"),
    h("dl", { class: "vessel-card__facts" }, ...rows),
    url === ""
      ? null
      : h(
          "a",
          {
            class: "vessel-card__link",
            href: url,
            target: "_blank",
            rel: "noopener noreferrer",
          },
          url,
        ),
  );
}

export function vesselCard(props: VesselCardProps): HTMLElement {
  const name = props.vessel.name?.trim() || UNNAMED_VESSEL;

  return h(
    "article",
    { class: "vessel-card" },
    headBlock(props, name),
    eventsBlock(props),
    props.watch === null ? null : watchBlock(props.watch, props.vessel),
  );
}
