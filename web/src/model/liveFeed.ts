/**
 * Live AIS feed for the Watch map: one SSE stream per recorded region
 * (`/api/live/<REGION>/stream`), merged into one callback and one rate.
 *
 * The server batches positions every 5 s and sends a heartbeat in between, so
 * this module only forwards `positions` frames and counts the items it gets.
 * EventSource reconnects on its own; a refused stream (cap reached, region
 * unknown) simply stays quiet and the counter shows what did arrive.
 */

export interface LivePosition {
  mmsi: number;
  lat: number;
  lon: number;
  sog?: number | null;
  t?: string;
}

export interface LiveFeed {
  /**
   * Position reports in the last 60 s, a true trailing count. Null until the feed
   * has been open for a full 60 s: a shorter window extrapolated to a minute
   * overstated the rate (QA #229 A1), so the UI shows "warming up" instead.
   */
  perMinute(): number | null;
  /** True once any frame (heartbeat or positions) has arrived recently. */
  connected(): boolean;
  close(): void;
}

// Hormuz has no live stream: our receivers hear almost nothing there, so radar covers it instead.
export const LIVE_REGIONS = ["BALTIC_WATCH", "MALAYSIA_EOPL", "BLACK_SEA"] as const;

const WINDOW_MS = 60_000;

export function openLiveFeed(
  regions: readonly string[],
  onPositions: (items: LivePosition[]) => void,
): LiveFeed {
  const sources: EventSource[] = [];
  /** [arrival ms, item count] per frame. */
  let arrivals: [number, number][] = [];
  const startedAt = Date.now();
  let lastFrame = 0;

  if (typeof EventSource !== "undefined") {
    for (const region of regions) {
      const source = new EventSource(`/api/live/${encodeURIComponent(region)}/stream`);
      source.onmessage = (event: MessageEvent<string>) => {
        lastFrame = Date.now();
        let payload: { type?: string; items?: LivePosition[] };
        try {
          payload = JSON.parse(event.data) as typeof payload;
        } catch {
          return;
        }
        if (payload.type !== "positions" || !Array.isArray(payload.items)) return;
        const items = payload.items.filter(
          (item) => Number.isFinite(item.lat) && Number.isFinite(item.lon),
        );
        if (items.length === 0) return;
        arrivals.push([lastFrame, items.length]);
        onPositions(items);
      };
      sources.push(source);
    }
  }

  return {
    perMinute(): number | null {
      const now = Date.now();
      if (now - startedAt < WINDOW_MS) return null;
      arrivals = arrivals.filter(([at]) => now - at <= WINDOW_MS);
      return arrivals.reduce((sum, [, n]) => sum + n, 0);
    },
    connected(): boolean {
      return Date.now() - lastFrame < 15_000;
    },
    close(): void {
      for (const source of sources) source.close();
      sources.length = 0;
    },
  };
}
