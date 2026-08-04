/**
 * Documents: upload, list, and the labels the UI needs.
 *
 * Mirrors app/schemas/document.py. The important thing to know about the shape
 * below is that almost all of it is null on arrival: a freshly uploaded row has
 * a filename, a size and `status: "uploaded"`, and gains its provider, date,
 * type and page count only once the pipeline has read it. The UI is built to
 * show that filling in rather than wait for it.
 */

import { apiFetch } from "./api";

export type DocStatus =
  | "uploaded"
  | "parsing"
  | "transcribing"
  | "extracting"
  | "ready"
  | "failed";

export type DocType =
  | "prescription"
  | "lab_report"
  | "discharge_summary"
  | "doctor_note"
  | "radiology_report"
  | "pathology_report"
  | "referral_letter"
  | "vaccination_record"
  | "operative_note"
  | "mixed"
  | "other"
  | "unknown";

export interface DocumentRead {
  id: string;
  doc_type: DocType;
  filename: string | null;
  mime_type: string;
  size_bytes: number | null;
  page_count: number | null;

  doc_date: string | null;
  provider: string | null;
  clinician_name: string | null;
  clinician_specialty: string | null;
  summary: string | null;
  patient_name_extracted: string | null;

  status: DocStatus;
  error_message: string | null;
  processing_started_at: string | null;
  processing_completed_at: string | null;

  created_at: string;
  updated_at: string;
}

/** The database stores slugs; people read words. */
const DOC_TYPE_LABELS: Record<DocType, string> = {
  prescription: "Prescription",
  lab_report: "Lab Report",
  discharge_summary: "Discharge Summary",
  doctor_note: "Doctor's Notes",
  radiology_report: "Radiology Report",
  pathology_report: "Pathology Report",
  referral_letter: "Referral Letter",
  vaccination_record: "Vaccination Record",
  operative_note: "Operative Note",
  mixed: "Mixed",
  other: "Other",
  unknown: "Unknown",
};

export function docTypeLabel(type: DocType): string {
  return DOC_TYPE_LABELS[type] ?? "Unknown";
}

/** What the row says while the pipeline works. Empty once it is done, because
 *  by then the document's own details are worth more than its state. */
const STATUS_LABELS: Partial<Record<DocStatus, string>> = {
  uploaded: "Queued",
  parsing: "Splitting pages",
  transcribing: "Reading pages",
  extracting: "Extracting details",
  failed: "Could not be read",
};

export function statusLabel(status: DocStatus): string | null {
  return STATUS_LABELS[status] ?? null;
}

/** Done, one way or the other — nothing more will change. */
export function isSettled(document: DocumentRead): boolean {
  return document.status === "ready" || document.status === "failed";
}

/** `2025-01-12` -> `Jan 12, 2025`, matching the timeline's format. */
export function formatDocDate(iso: string | null): string | null {
  if (!iso) return null;
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
  });
}

export interface UploadResult {
  document: DocumentRead;
  /** True when the server recognised the bytes and returned the existing row.
   *  Worth saying out loud — silently doing nothing looks like a failure. */
  duplicate: boolean;
}

/**
 * Upload one file.
 *
 * The endpoint answers 201 for a new document and 200 when the same bytes are
 * already stored, so the status code is the only thing that distinguishes a
 * fresh upload from a duplicate.
 */
export async function uploadDocument(
  token: string | null,
  file: File,
  signal?: AbortSignal,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);

  // apiFetch returns the parsed body, not the response, so the status has to
  // come back another way — this is the one endpoint that needs it.
  let duplicate = false;
  const document = await apiFetch<DocumentRead>("/documents", {
    method: "POST",
    token,
    form,
    signal,
    onStatus: (status) => {
      duplicate = status === 200;
    },
  });

  return { document, duplicate };
}

export async function listDocuments(token: string | null): Promise<DocumentRead[]> {
  return apiFetch<DocumentRead[]>("/documents", { token });
}

export async function getDocument(token: string | null, id: string): Promise<DocumentRead> {
  return apiFetch<DocumentRead>(`/documents/${id}`, { token });
}

/**
 * Delete a document and everything read out of it.
 *
 * The only destructive call in the API. Rejecting a timeline row hides it and
 * keeps it recoverable; this does not — the visits, the extracted facts and
 * the stored file all go. Findings are recomputed server-side afterwards,
 * because a finding about facts that no longer exist is worse than no finding.
 *
 * Ask before calling it.
 */
export async function deleteDocument(token: string | null, id: string): Promise<void> {
  return apiFetch<void>(`/documents/${id}`, { method: "DELETE", token });
}
