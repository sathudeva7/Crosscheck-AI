/**
 * What a lab report says, and who usually reads one like it.
 *
 * The types mirror `app/schemas/report.py`.
 *
 * A lab report is the one document a patient holds without knowing what to do
 * with it — a prescription came from a doctor who already decided something,
 * but results come back from a machine. This is the answer to "who do I take
 * this to", asked at upload rather than after a check flags something, because
 * most reports never flag anything and "nothing was flagged" is not the same
 * as "nothing to ask about".
 *
 * Two fields need care when rendering.
 *
 * `qualitative` readings carry no verdict. 'detected' is shown beside what the
 * report said to expect and the reader draws the conclusion — nothing here,
 * and no model, rules on an HIV result.
 *
 * `interpretation` is the laboratory's own sentence, quoted exactly. Render it
 * as a quote with the lab named, never as the app's own words: it is the one
 * way this screen can say something specific about a condition without
 * diagnosing anything.
 */

import { apiFetch } from "./api";

export interface Reading {
  analyte: string;
  value: string;
  unit?: string | null;
  reference?: string | null;
  abnormal: boolean;
  lab_flagged: boolean;
  qualitative: boolean;
}

export interface SuggestedSpecialty {
  specialty: string;
  reason: string;
  confidence: string;
  /** Which channelling networks list it. Both is the strongest signal. */
  directories: string[];
}

export interface ReportRecommendation {
  document_id: string;
  filename?: string | null;
  doc_type: string;
  provider?: string | null;
  doc_date?: string | null;

  panel_names: string[];
  about: string;
  result_count: number;
  abnormal_count: number;
  readings: Reading[];

  interpretation?: string | null;
  specialties: SuggestedSpecialty[];
  error?: string | null;
  disclaimer: string;
}

/**
 * The recommendation for one document.
 *
 * Normally a database read — the model ran once, during ingest. It only costs
 * a model call for a report uploaded before this feature existed, which is why
 * the caller should expect this to occasionally take several seconds.
 */
export async function getReportRecommendation(
  documentId: string,
  token: string | null,
): Promise<ReportRecommendation> {
  return apiFetch<ReportRecommendation>(`/referrals/for-document/${documentId}`, { token });
}

/** How a confidence band reads. Words, not percentages — see the API notes. */
export const CONFIDENCE_LABEL: Record<string, string> = {
  high: "Clear match",
  medium: "Likely",
  low: "Best guess",
};
