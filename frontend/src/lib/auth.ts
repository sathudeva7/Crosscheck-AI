/**
 * Who the user is, and whether they have a patient profile yet.
 *
 * Identity comes from Clerk; the patient profile comes from FastAPI. Those are
 * two different questions, and the app needs both answered before it can show
 * anything — hence the state machine below, which the router reads as its
 * single source of truth:
 *
 *   loading      -> hold, decide nothing
 *   signed-out   -> /login
 *   needs-setup  -> /setup      (Clerk knows them, `/patients/me` returns 404)
 *   ready        -> /app
 *
 * That third state is the backend's own: `current_patient` raises 404 rather
 * than 401 precisely so the frontend can tell "not signed in" from "signed in,
 * no profile yet".
 */

import { ApiError, apiFetch } from "./api";

/** Mirrors the `Sex` literal in app/schemas/patient.py, and the CHECK
 *  constraint on `patient.sex` behind it. */
export type Sex = "male" | "female" | "other" | "unknown";

/** Mirrors `PatientRead` in app/schemas/patient.py. */
export interface Patient {
  id: string;
  email: string | null;
  full_name: string | null;
  dob: string | null;
  sex: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

/** Mirrors `PatientCreate`. `clerk_user_id` is absent on purpose — identity
 *  comes from the verified token, never from the request body. */
export interface PatientDraft {
  email?: string | null;
  full_name?: string | null;
  dob?: string | null;
  sex?: Sex | null;
  description?: string | null;
}

/**
 * Consent has no column on the patient table, so it lives in Clerk's user
 * metadata: it is account-level data, it follows the user across devices, and
 * it needs no migration.
 *
 * Worth knowing: `unsafeMetadata` is writable from the browser, which is thin
 * for a consent record. Moving `consentedAt` to a real column is a small
 * migration whenever that matters.
 */
export interface AccountMetadata {
  consentedAt?: string;
}

export type AuthState =
  | { status: "loading" }
  | { status: "signed-out" }
  | { status: "needs-setup" }
  | { status: "ready"; patient: Patient };

/** Where a given state belongs right now. The router's single source of truth. */
export function landingRoute(state: AuthState): string {
  switch (state.status) {
    case "signed-out":
      return "/login";
    case "needs-setup":
      return "/setup";
    case "ready":
      return "/app";
    default:
      return "/";
  }
}

/**
 * The signed-in account's profile, or null when it has not been created yet.
 *
 * The 404 is expected, so it is translated rather than thrown. Every other
 * failure propagates: a network error must not be mistaken for "no profile",
 * or the user is sent to setup only to fail again on submit.
 */
export async function fetchPatient(token: string | null): Promise<Patient | null> {
  try {
    return await apiFetch<Patient>("/patients/me", { token });
  } catch (problem) {
    if (problem instanceof ApiError && problem.status === 404) return null;
    throw problem;
  }
}

export async function createPatient(token: string | null, draft: PatientDraft): Promise<Patient> {
  return apiFetch<Patient>("/patients", { method: "POST", token, json: draft });
}
