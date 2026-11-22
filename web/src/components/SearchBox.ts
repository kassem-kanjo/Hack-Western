/**
 * Top-bar ship search (#183): one field, at most twenty hulls, keyboard first.
 *
 * The field is mounted once by TopBar and stays with the header across screens.
 * Typing debounces one request to `GET /api/search`; arrows move through the
 * results, Enter dispatches `df:focus-vessel` on `window` with {mmsi, lat, lon}
 * so the map (or a dossier) can fly to the hull, and Escape closes the list.
 *
 * A Hormuz story hull is not on the Watch map: choosing it goes to `#/hormuz`,
 * where its story lives.
 *
 * The list is a dense console table, not a palette: hairline borders, mono
 * MMSI, a small-caps count header and a sanctions tag that is the only colour.
 */

import { h } from "./dom";
import "./search.css";

/** One row of `GET /api/search`. */
export interface SearchHit {
  mmsi: number;
  name: string | null;
  imo: string | null;
  type: string | null;
  watchlist: "sanctioned" | "shadow_fleet" | null;
  lat: number;
  lon: number;
  area: string | null;
  /** "hormuz_story" rows open the Hormuz screen; watch rows omit the field. */
  source: "hormuz_story" | null;
}

/** The payload carried by `df:focus-vessel`. */
export interface FocusVessel {
  mmsi: number;
  lat: number;
  lon: number;
}

/** Window event the search dispatches when a row is chosen. */
export const VESSEL_FOCUS_EVENT = "df:focus-vessel";

/** Typing is cheap; every keystroke starting a request is not. */
const DEBOUNCE_MS = 150;

/** Two characters is the API's own floor; below it the field asks nothing. */
const MIN_QUERY = 2;

let uid = 0;

/**
 * Listen for a vessel picked in the search field. Returns the unsubscribe
 * function so a screen can drop the listener with its map.
 */
export function onVesselFocus(handler: (vessel: FocusVessel) => void): () => void {
  const listener = (event: Event): void => {
    const detail = (event as CustomEvent<Partial<FocusVessel>>).detail;
    if (
      detail === undefined ||
      typeof detail.mmsi !== "number" ||
      typeof detail.lat !== "number" ||
      typeof detail.lon !== "number" ||
      !Number.isFinite(detail.mmsi) ||
      !Number.isFinite(detail.lat) ||
      !Number.isFinite(detail.lon)
    ) {
      return;
    }
    handler({ mmsi: detail.mmsi, lat: detail.lat, lon: detail.lon });
  };
  window.addEventListener(VESSEL_FOCUS_EVENT, listener);
  return () => window.removeEventListener(VESSEL_FOCUS_EVENT, listener);
}

/** Accept a JSON row only when it can be placed on the map. */
function toHit(value: unknown): SearchHit | null {
  if (typeof value !== "object" || value === null) return null;
  const row = value as Record<string, unknown>;
  if (
    typeof row.mmsi !== "number" ||
    typeof row.lat !== "number" ||
    typeof row.lon !== "number" ||
    !Number.isFinite(row.mmsi) ||
    !Number.isFinite(row.lat) ||
    !Number.isFinite(row.lon)
  ) {
    return null;
  }
  const watchlist =
    row.watchlist === "sanctioned" || row.watchlist === "shadow_fleet" ? row.watchlist : null;
  return {
    mmsi: row.mmsi,
    name: typeof row.name === "string" ? row.name : null,
    imo: typeof row.imo === "string" ? row.imo : null,
    type: typeof row.type === "string" ? row.type : null,
    watchlist,
    lat: row.lat,
    lon: row.lon,
    area: typeof row.area === "string" ? row.area : null,
    source: row.source === "hormuz_story" ? "hormuz_story" : null,
  };
}

function displayName(hit: SearchHit): string {
  const name = hit.name?.trim();
  return name !== undefined && name.length > 0 ? name : `MMSI ${hit.mmsi}`;
}

/** "cargo" -> "Cargo"; a missing class names what is missing, never a dash. */
function typeLabel(type: string | null): string {
  const text = type?.trim();
  if (text === undefined || text.length === 0) return "No flag broadcast";
  const plain = text.replace(/_/g, " ");
  return plain.charAt(0).toUpperCase() + plain.slice(1);
}

function watchTag(watchlist: SearchHit["watchlist"]): { label: string; className: string } {
  if (watchlist === "sanctioned") return { label: "Sanctioned", className: "is-sanctioned" };
  if (watchlist === "shadow_fleet") return { label: "Shadow fleet", className: "is-shadow" };
  return { label: "Not on a sanctions list", className: "is-none" };
}

/**
 * Build the top-bar field. The header owns the node; the module owns one
 * document keydown listener (for "/") that removes itself once the field
 * leaves the DOM, so a rebuilt header does not stack listeners.
 */
export function mountSearchBox(): HTMLElement {
  uid += 1;
  const listId = `df-search-list-${uid}`;

  const input = h("input", {
    class: "df-search-input",
    type: "search",
    placeholder: "Search ship, MMSI, IMO",
    "aria-label": "Search ship, MMSI, IMO",
    role: "combobox",
    "aria-autocomplete": "list",
    "aria-expanded": "false",
    "aria-controls": listId,
    autocomplete: "off",
    spellcheck: "false",
  });

  const count = h("span", { class: "df-search-count" }, "0");
  const list = h("ul", { class: "df-search-list", role: "listbox", id: listId });
  const pop = h(
    "div",
    { class: "df-search-pop", hidden: true },
    h(
      "div",
      { class: "df-search-head", "aria-hidden": "true" },
      h("span", null, "Ships"),
      count,
    ),
    list,
  );
  const box = h(
    "div",
    { class: "df-search", role: "search" },
    input,
    h("kbd", { class: "df-search-kbd", "aria-hidden": "true" }, "/"),
    pop,
  );

  let rows: SearchHit[] = [];
  let active = -1;
  let expanded = false;
  let message = "No matches";
  let timer: number | null = null;
  let controller: AbortController | null = null;
  let requestId = 0;

  function optionId(index: number): string {
    return `${listId}-opt-${index}`;
  }

  function setExpanded(next: boolean): void {
    expanded = next;
    pop.hidden = !next;
    input.setAttribute("aria-expanded", next ? "true" : "false");
    if (!next) {
      active = -1;
      input.removeAttribute("aria-activedescendant");
    }
  }

  function setActive(index: number): void {
    if (rows.length === 0) return;
    active = Math.max(0, Math.min(index, rows.length - 1));
    const options = list.querySelectorAll<HTMLElement>(".df-search-row");
    options.forEach((option, optionIndex) => {
      const selected = optionIndex === active;
      option.classList.toggle("is-active", selected);
      option.setAttribute("aria-selected", selected ? "true" : "false");
    });
    input.setAttribute("aria-activedescendant", optionId(active));
    const current = options[active];
    current?.scrollIntoView({ block: "nearest" });
  }

  function close(): void {
    setExpanded(false);
  }

  function render(): void {
    list.replaceChildren();
    count.textContent = String(rows.length);

    if (rows.length === 0) {
      active = -1;
      input.removeAttribute("aria-activedescendant");
      list.append(h("li", { class: "df-search-none", role: "presentation" }, message));
      return;
    }

    rows.forEach((hit, index) => {
      const tag = watchTag(hit.watchlist);
      const row = h(
        "li",
        {
          class: "df-search-row",
          role: "option",
          id: optionId(index),
          "aria-selected": "false",
          title: hit.area,
        },
        h("span", { class: "df-search-name" }, displayName(hit)),
        h("span", { class: "df-search-mmsi" }, String(hit.mmsi)),
        h("span", { class: "df-search-type" }, typeLabel(hit.type)),
        h("span", { class: `df-search-tag ${tag.className}` }, tag.label),
      );
      row.addEventListener("mouseenter", () => setActive(index));
      row.addEventListener("click", () => choose(hit));
      list.append(row);
    });

    setExpanded(true);
    setActive(0);
  }

  function choose(hit: SearchHit): void {
    // A story hull lives on the Hormuz screen, not on the Watch map the event drives.
    if (hit.source === "hormuz_story") {
      window.location.hash = "#/hormuz";
    } else {
      window.dispatchEvent(
        new CustomEvent<FocusVessel>(VESSEL_FOCUS_EVENT, {
          detail: { mmsi: hit.mmsi, lat: hit.lat, lon: hit.lon },
        }),
      );
    }
    input.value = "";
    rows = [];
    close();
    input.blur();
  }

  async function load(query: string): Promise<void> {
    controller?.abort();
    const current = new AbortController();
    controller = current;
    const id = ++requestId;
    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`, {
        headers: { Accept: "application/json" },
        signal: current.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = (await response.json()) as unknown;
      if (id !== requestId || current.signal.aborted) return;
      rows = Array.isArray(body)
        ? body.map(toHit).filter((hit): hit is SearchHit => hit !== null)
        : [];
      message = "No matches";
      render();
    } catch {
      if (id !== requestId || current.signal.aborted) return;
      rows = [];
      message = "Search unavailable";
      render();
    }
  }

  function schedule(): void {
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
    const query = input.value.trim();
    if (query.length < MIN_QUERY) {
      controller?.abort();
      requestId += 1;
      rows = [];
      close();
      return;
    }
    timer = window.setTimeout(() => {
      timer = null;
      void load(query);
    }, DEBOUNCE_MS);
  }

  input.addEventListener("input", schedule);
  input.addEventListener("focus", () => {
    if (input.value.trim().length >= MIN_QUERY && rows.length === 0) schedule();
    else if (rows.length > 0) {
      setExpanded(true);
      setActive(Math.max(0, active));
    }
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!expanded) {
        if (rows.length === 0) return;
        setExpanded(true);
        setActive(event.key === "ArrowDown" ? 0 : rows.length - 1);
      } else {
        const step = event.key === "ArrowDown" ? 1 : -1;
        const next =
          active < 0
            ? step > 0
              ? 0
              : rows.length - 1
            : (active + step + rows.length) % rows.length;
        setActive(next);
      }
      event.preventDefault();
      return;
    }
    if (event.key === "Enter") {
      if (!expanded) return;
      event.preventDefault();
      // Enter with nothing highlighted still opens the best match.
      const hit = rows[active] ?? rows[0];
      if (hit !== undefined) choose(hit);
      return;
    }
    if (event.key === "Escape") {
      // Always keep Escape inside the field: the watch screen uses it to leave
      // an open alert, and closing a search list must not also navigate.
      event.stopPropagation();
      if (expanded) {
        event.preventDefault();
        close();
      } else {
        input.blur();
      }
    }
  });

  // "/" focuses the field from anywhere that is not already typing.
  const onSlash = (event: KeyboardEvent): void => {
    if (!box.isConnected) {
      document.removeEventListener("keydown", onSlash);
      return;
    }
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    if (target instanceof HTMLElement) {
      const tag = target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) {
        return;
      }
    }
    event.preventDefault();
    input.focus();
    input.select();
  };
  document.addEventListener("keydown", onSlash);

  // A click elsewhere dismisses the list; remove the listener once this box is gone.
  const onPointerDown = (event: PointerEvent): void => {
    if (!box.isConnected) {
      document.removeEventListener("pointerdown", onPointerDown);
      return;
    }
    if (!box.contains(event.target as Node)) close();
  };
  document.addEventListener("pointerdown", onPointerDown);

  return box;
}
