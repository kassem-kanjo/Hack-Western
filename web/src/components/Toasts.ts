/**
 * Live alert toasts (#184): when `/api/watch` is rebuilt and carries alert ids
 * the console has not seen, one toast per new alert appears top-right.
 *
 * The signal is the watch SSE stream (`event: update`), not a timer, so a
 * toast is tied to an actual new build. The first fetch only seeds the known
 * ids — opening the console never replays the whole snapshot as toasts.
 *
 * At most three are stacked; each fades on its own after six seconds and a
 * click opens that alert through the existing hash route. The component owns
 * its own stream, so `watch.ts` needs no hook.
 */

import "../styles/toasts.css";

import type { WatchAlert, WatchResponse } from "../api";
import { fetchWatch, watchStream } from "../api";
import { h } from "./dom";

const MAX_TOASTS = 3;
const TOAST_MS = 6000;

/** One colour square per kind; shape is constant, colour is the secondary cue. */
const KIND_CLASS: Record<string, string> = {
  dark: "is-dark",
  spoof: "is-spoof",
  jump: "is-jump",
  meeting: "is-meeting",
  sanctioned_stop: "is-sanctioned",
  radar_unmatched: "is-radar",
};

export interface ToastsHandle {
  destroy(): void;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function shortTime(iso: string): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return "";
  const date = new Date(ms);
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}Z`;
}

/** The vessel name, or the contract's honest placeholder. */
function alertName(alert: WatchAlert): string {
  const trimmed = alert.name?.trim();
  if (trimmed !== undefined && trimmed.length > 0) return trimmed;
  return alert.mmsi > 0 ? `MMSI ${alert.mmsi}` : "Fleet alert";
}

/** Backend labels are already short; this keeps the rail's six-word rule. */
function shortLabel(label: string): string {
  return label
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 6)
    .join(" ");
}

function areaLabel(snapshot: WatchResponse, alert: WatchAlert): string {
  if (alert.area_id === null) return "No area";
  return snapshot.areas.find((area) => area.id === alert.area_id)?.name ?? alert.area_id;
}

/** Severity, then most recent: the order the rail uses for new work. */
function newestFirst(alerts: WatchAlert[]): WatchAlert[] {
  return [...alerts].sort((a, b) => {
    if (b.severity !== a.severity) return b.severity - a.severity;
    const aStart = Date.parse(a.t_start);
    const bStart = Date.parse(b.t_start);
    if (Number.isFinite(aStart) && Number.isFinite(bStart) && aStart !== bStart) {
      return bStart - aStart;
    }
    return 0;
  });
}

/** Mount the toast stack once; the handle closes the stream and clears timers. */
export function mountToasts(): ToastsHandle {
  const root = h("div", {
    class: "toasts",
    role: "region",
    "aria-label": "New alerts",
    "aria-live": "polite",
  });
  document.body.append(root);

  /** Alert ids seen so far; null until the first snapshot seeds the baseline. */
  let known: Set<string> | null = null;
  let busy = false;
  let queued = false;
  let destroyed = false;
  const timers = new Map<HTMLElement, number>();

  function removeToast(el: HTMLElement): void {
    const timer = timers.get(el);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.delete(el);
    el.remove();
  }

  function showToast(alert: WatchAlert, snapshot: WatchResponse): void {
    const name = alertName(alert);
    const label = shortLabel(alert.label);
    const area = areaLabel(snapshot, alert);
    const kindClass = KIND_CLASS[alert.kind] ?? "is-unknown";

    const el = h(
      "button",
      {
        class: "toast",
        type: "button",
        "aria-label": `${name}, ${label}, ${area}`,
      },
      h("span", { class: `toast-kind ${kindClass}`, "aria-hidden": "true" }),
      h(
        "span",
        { class: "toast-body" },
        h("span", { class: "toast-name" }, name),
        h(
          "span",
          { class: "toast-meta" },
          h("span", { class: "toast-label" }, label),
          h("span", { class: "toast-sep", "aria-hidden": "true" }, "\u00b7"),
          h("span", { class: "toast-area" }, area),
        ),
      ),
      h("span", { class: "toast-time" }, shortTime(alert.t_start)),
    );

    el.addEventListener("click", () => {
      removeToast(el);
      location.hash = `#/watch/alert/${encodeURIComponent(alert.id)}`;
    });

    root.prepend(el);
    while (root.childElementCount > MAX_TOASTS) {
      const oldest = root.lastElementChild;
      if (!(oldest instanceof HTMLElement)) break;
      removeToast(oldest);
    }
    timers.set(
      el,
      window.setTimeout(() => removeToast(el), TOAST_MS),
    );
  }

  async function refresh(): Promise<void> {
    if (destroyed) return;
    if (busy) {
      queued = true;
      return;
    }
    busy = true;
    try {
      const snapshot = await fetchWatch();
      if (destroyed) return;

      if (known === null) {
        // First load: learn the current ids and stay quiet.
        known = new Set(snapshot.alerts.map((alert) => alert.id));
        return;
      }

      const fresh = snapshot.alerts.filter((alert) => !known?.has(alert.id));
      for (const alert of snapshot.alerts) known.add(alert.id);
      if (fresh.length === 0) return;

      for (const alert of newestFirst(fresh).slice(0, MAX_TOASTS)) {
        showToast(alert, snapshot);
      }
    } catch {
      // The API is down or watch.json is not built: no toasts, no error state.
    } finally {
      busy = false;
      if (queued && !destroyed) {
        queued = false;
        void refresh();
      }
    }
  }

  const stream = watchStream(() => {
    void refresh();
  });
  void refresh();

  return {
    destroy(): void {
      destroyed = true;
      stream.close();
      for (const timer of timers.values()) window.clearTimeout(timer);
      timers.clear();
      root.remove();
    },
  };
}
