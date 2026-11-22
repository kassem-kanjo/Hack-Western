/**
 * The one Hormuz story card that should be open, shared by presentation mode and
 * the Hormuz screen. Present can request a card before the Hormuz screen exists
 * (the route change is async), so the last request is kept and applied when the
 * screen renders. Requests clear on screen exit and on screen destroy.
 */

export type HormuzCardKey = "sea1" | "cluster";

type Listener = (card: HormuzCardKey | null) => void;

let requested: HormuzCardKey | null = null;
const listeners = new Set<Listener>();

/** Set the open card (null closes it) and tell the mounted Hormuz screen. */
export function requestHormuzCard(card: HormuzCardKey | null): void {
  requested = card;
  for (const listener of listeners) listener(card);
}

/** The card a screen should show when it renders. */
export function pendingHormuzCard(): HormuzCardKey | null {
  return requested;
}

export function onHormuzCardRequest(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
