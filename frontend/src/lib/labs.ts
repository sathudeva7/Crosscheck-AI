/**
 * Measurements over time.
 *
 * Mirrors `LabSeries` in app/schemas/lab.py. Three fields are null in ways the
 * screen has to render as an absence rather than a zero:
 *
 *   `value` null means the result was words — 'positive for S. aureus'. Real,
 *   listed, never plotted.
 *
 *   `status` null means the document recorded no reference range. There is
 *   then no such thing as high or low, and showing a verdict anyway would be
 *   diagnosis rather than reading.
 *
 *   `direction` null means fewer than two comparable readings. One result is a
 *   result, not a trend.
 */

import { apiFetch } from "./api";

export type Direction = "rising" | "falling" | "steady";
export type Status = "high" | "low" | "normal";

export interface Reading {
  event_id: string;
  date: string | null;
  /** Null when the result was words rather than a number. */
  value: number | null;
  text: string | null;
  unit: string | null;
  ref_low: number | null;
  ref_high: number | null;
  /** '<' or '>' — the lab declining to state a value exactly. */
  operator: string | null;
  document_id: string | null;
  page_no: number | null;
  /** Null when no reference range was recorded. */
  status: Status | null;
  plottable: boolean;
  needs_review: boolean;
}

export interface Series {
  key: string;
  name: string;
  unit: string | null;
  readings: Reading[];
  ref_low: number | null;
  ref_high: number | null;
  latest: Reading | null;
  direction: Direction | null;
  change: number | null;
  /** The document spellings this was assembled from, when more than one. */
  source_names: string[];
  chartable: boolean;
}

export function listSeries(token: string | null): Promise<Series[]> {
  return apiFetch<Series[]>("/labs", { token });
}

// --- presentation ------------------------------------------------------------

/** How a reading against its range reads. Null status renders nothing at all. */
export const STATUS: Record<Status, { label: string; bg: string; text: string }> = {
  high: { label: "Above range", bg: "var(--color-danger-bg)", text: "var(--color-danger-text)" },
  low: { label: "Below range", bg: "var(--color-warning-bg)", text: "var(--color-warning-text)" },
  normal: { label: "In range", bg: "#e9f0eb", text: "#3c5f48" },
};

/**
 * The direction in words, said plainly.
 *
 * Deliberately describes the movement and nothing else. 'Rising' is an
 * observation; 'worsening' would be a judgement, and without a reference range
 * there is nothing to judge against.
 */
export const TREND: Record<Direction, string> = {
  rising: "Rising",
  falling: "Falling",
  steady: "Steady",
};

/** '12 Jan 2025' — short enough for an axis, unambiguous about the month. */
export function shortDate(iso: string | null): string {
  if (!iso) return "undated";
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** A number as a person would write it: 6.8, not 6.800000000000001. */
export function num(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)));
}

/** The reading as text, whichever kind it is. */
export function shown(reading: Reading): string {
  if (reading.value === null) return reading.text ?? "—";
  return `${reading.operator ?? ""}${num(reading.value)}${reading.unit ? ` ${reading.unit}` : ""}`;
}
