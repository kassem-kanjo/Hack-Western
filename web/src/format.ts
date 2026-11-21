/**
 * Presentation helpers. All times render in UTC (the snapshot is UTC) and every
 * function is pure, so they can be unit-free and reused by any component.
 */

import type { Vessel } from "./api";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** 5624 -> "5,624" */
export function fmtInt(n: number): string {
  return Math.round(n).toLocaleString("en-US");
}

/** 22.9 -> "22.9 h" */
export function fmtHours(h: number): string {
  return `${h.toFixed(1)} h`;
}

/** "2026-11-03T14:20:00Z" -> "03 Nov 14:20" */
export function fmtUtc(iso: string): string {
  const d = new Date(iso);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mi = String(d.getUTCMinutes()).padStart(2, "0");
  return `${dd} ${MONTHS[d.getUTCMonth()]} ${hh}:${mi}`;
}

/** "2026-11-03T14:20:00Z" -> "03 Nov 2026 14:20 UTC" */
export function fmtUtcFull(iso: string): string {
  const d = new Date(iso);
  return `${fmtUtc(iso).replace(/^(\d\d \w{3}) /, `$1 ${d.getUTCFullYear()} `)} UTC`;
}

/** Hours between two ISO times, shown as "+17 h" (rounded). */
export function fmtOffset(fromIso: string, toIso: string): string {
  const h = (new Date(toIso).getTime() - new Date(fromIso).getTime()) / 3_600_000;
  return `+${Math.round(h)} h`;
}

/** Name, else MMSI, else a plain placeholder. Some snapshot vessels have no name. */
export function vesselLabel(v: Vessel): string {
  return v.name?.trim() || v.mmsi || "Unidentified vessel";
}

/** Human label for the status chip text. */
export function statusLabel(status: string): string {
  if (status === "candidate") return "Unexplained";
  if (status === "ruled_out") return "Ruled out";
  return "No evidence";
}
