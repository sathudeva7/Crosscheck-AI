/**
 * The timeline and its review queue.
 *
 * Mirrors app/schemas/timeline.py. The distinction that matters here is
 * between the fact and how it was read: `title`, `drug_name`, `value_num` and
 * the rest are the fact and can be corrected; `confidence`, `source_span`,
 * `page_no` and `review_reasons` are the record of how it was obtained and are
 * read-only. Correcting a value must not rewrite the history of how the app got
 * it wrong — that history is the only measure of how well extraction works.
 */

import { apiFetch } from "./api";

export type EventType =
  | "medication"
  | "lab_result"
  | "vital_sign"
  | "diagnosis"
  | "allergy"
  | "procedure"
  | "note";

export type ReviewStatus = "accepted" | "needs_review" | "confirmed" | "corrected" | "rejected";
export type Risk = "high" | "medium" | "low";

export interface TimelineEvent {
  id: string;
  event_type: EventType;
  event_date: string | null;
  title: string | null;
  display_text: string | null;

  drug_raw: string | null;
  drug_name: string | null;
  rxcui: string | null;
  atc_codes: string[] | null;
  strength_value: string | null;
  strength_unit: string | null;
  dose_count: string | null;
  doses_per_day: string | null;
  frequency_label: string | null;
  prn: boolean;
  start_date: string | null;
  end_date: string | null;

  analyte: string | null;
  value_num: string | null;
  value_text: string | null;
  unit: string | null;
  operator: string | null;
  ref_low: string | null;
  ref_high: string | null;
  flag: string | null;
  computed_flag: string | null;

  description: string | null;
  substance: string | null;
  reaction: string | null;

  details: Record<string, unknown>;
  confidence: string | null;
  review_status: ReviewStatus;
  review_reasons: string[] | null;
  risk: Risk;
  source: "ai" | "user";
  document_id: string | null;
  page_no: number | null;
  source_span: string | null;
}

export interface ReviewItem extends TimelineEvent {
  visit_id: string;
  visit_date: string | null;
  visit_doc_type: string | null;
  visit_provider: string | null;
}

export interface ReviewQueue {
  /** Matches the current filter, so it can be paged against. */
  total: number;
  /** Always the whole queue — it is the badge that says there is urgent work. */
  high_risk: number;
  items: ReviewItem[];
}

export interface TimelineCounts {
  visits: number;
  medications: number;
  lab_values: number;
  needs_review: number;
}

export interface TimelineVisit {
  id: string;
  visit_date: string | null;
  doc_type: string | null;
  provider: string | null;
  summary: string | null;
  events: TimelineEvent[];
}

export interface Timeline {
  counts: TimelineCounts;
  visits: TimelineVisit[];
}

/** Only what the document says is editable — see the note at the top. */
export interface EventCorrection {
  title?: string | null;
  event_date?: string | null;

  drug_raw?: string | null;
  drug_name?: string | null;
  strength_value?: string | null;
  strength_unit?: string | null;
  dose_count?: string | null;
  doses_per_day?: string | null;
  frequency_label?: string | null;
  prn?: boolean | null;
  duration_days?: string | null;

  analyte?: string | null;
  value_num?: string | null;
  value_text?: string | null;
  unit?: string | null;
  ref_low?: string | null;
  ref_high?: string | null;
  flag?: string | null;

  description?: string | null;
  substance?: string | null;
  reaction?: string | null;

  /** Why you changed it. Kept with the row. */
  note?: string | null;
}

export async function getTimeline(token: string | null): Promise<Timeline> {
  return apiFetch<Timeline>("/timeline", { token });
}

export async function getReviewQueue(
  token: string | null,
  risk?: Risk | null,
): Promise<ReviewQueue> {
  const query = risk ? `?risk=${risk}` : "";
  return apiFetch<ReviewQueue>(`/timeline/review${query}`, { token });
}

export async function correctEvent(
  token: string | null,
  id: string,
  changes: EventCorrection,
): Promise<TimelineEvent> {
  return apiFetch<TimelineEvent>(`/timeline/${id}`, { method: "PATCH", token, json: changes });
}

export async function approveEvent(token: string | null, id: string): Promise<TimelineEvent> {
  return apiFetch<TimelineEvent>(`/timeline/${id}/approve`, { method: "POST", token });
}

export async function rejectEvent(
  token: string | null,
  id: string,
  reason?: string,
): Promise<TimelineEvent> {
  return apiFetch<TimelineEvent>(`/timeline/${id}/reject`, {
    method: "POST",
    token,
    json: reason ? { reason } : {},
  });
}

// --- presentation -------------------------------------------------------------

export const EVENT_TYPE_LABELS: Record<EventType, string> = {
  medication: "Medication",
  lab_result: "Lab result",
  vital_sign: "Vital sign",
  diagnosis: "Diagnosis",
  allergy: "Allergy",
  procedure: "Procedure",
  note: "Note",
};

/**
 * `review_reasons` arrives as finished sentences — app/ingest/review.py writes
 * things like "the quoted text could not be found on the page" — so there is
 * nothing to translate. Capitalising the first letter is the whole job, and a
 * lookup table here would only go stale the moment a reason is added.
 */
export function reasonLabel(reason: string): string {
  return reason.charAt(0).toUpperCase() + reason.slice(1);
}

export function riskColour(risk: Risk): { bg: string; text: string; rail: string } {
  switch (risk) {
    case "high":
      return {
        bg: "var(--color-danger-bg)",
        text: "var(--color-danger-text)",
        rail: "var(--color-danger)",
      };
    case "medium":
      return {
        bg: "var(--color-warn-bg)",
        text: "var(--color-warn-text)",
        rail: "var(--color-warn)",
      };
    default:
      return {
        bg: "var(--color-surface)",
        text: "color-mix(in srgb, var(--color-text) 65%, transparent)",
        rail: "var(--color-divider)",
      };
  }
}

/** `0.72` -> `72%`. Null when the fact carries no score at all. */
export function confidencePercent(confidence: string | null): number | null {
  if (confidence === null) return null;
  const value = Number(confidence);
  return Number.isNaN(value) ? null : Math.round(value * 100);
}
