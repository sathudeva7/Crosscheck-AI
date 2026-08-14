/**
 * Finding a real doctor for a finding the cross-checks flagged.
 *
 * The types mirror `app/schemas/referral.py` exactly.
 *
 * Two fields carry more weight than they look. `attempts` is the search showing
 * its working: without it a list of doctors 150 km away reads as a bad result
 * rather than the honest answer to a question with no closer one. `sourceUrl`
 * is what makes a row checkable — a reader can open the listing and see the
 * same name and number, which is the only thing that makes "this is real data"
 * mean anything.
 *
 * Three directories answer, and they are shown in separate tabs rather than
 * merged. eChannelling and Doc990 are the two private channelling networks and
 * sign different hospitals; Google carries the government hospitals neither of
 * them lists. A patient in Vavuniya has no cardiologist within 30 km on
 * eChannelling and one 800 m away on Doc990, so which tab has your doctor
 * depends on which network your local hospital joined.
 */

import { apiFetch } from "./api";

export type Confidence = "high" | "medium" | "low";
export type ReferralKind = "doctor" | "clinic";

/** One kind of doctor a finding could be taken to. Never the only one. */
export interface Specialty {
  label: string;
  reason: string;
  confidence: Confidence;
  search_terms: string[];
}

export interface SpecialtyList {
  finding_id: string;
  finding_type: string;
  severity: "danger" | "warning" | "info";
  title: string;
  detail?: string | null;
  specialties: Specialty[];
  disclaimer: string;
}

/** One doctor or clinic, as fetched a moment ago. */
export interface Referral {
  kind: ReferralKind;
  name: string;
  specialty?: string | null;
  clinic_name?: string | null;
  address?: string | null;
  city?: string | null;
  distance_km?: number | null;
  rating?: number | null;
  reviews?: number | null;
  phone?: string | null;
  open_now?: boolean | null;
  hours: string[];
  website?: string | null;
  slmc_number?: string | null;
  other_clinics: string[];
  source: string;
  source_url?: string | null;
  booking_url?: string | null;
}

/** One rung of a widening ladder. */
export interface Attempt {
  term: string;
  radius_km: number;
  found: number;
  provider: string;
}

export interface ReferralSearch {
  specialty: string;
  location: string;
  resolved_location?: string | null;
  availability?: string | null;
  results: Referral[];
  attempts: Attempt[];
  widened_to_km?: number | null;
  notice?: string | null;
  sources: string[];
  degraded: string[];
  attribution: string[];
  disclaimer: string;
}

/** Which directory a results tab is showing. */
export type ProviderId = "echannelling" | "doc990" | "google_places";

export const PROVIDERS: { id: ProviderId; label: string; blurb: string }[] = [
  {
    id: "echannelling",
    label: "eChannelling",
    blurb: "Doctors you can book on the eChannelling network.",
  },
  {
    id: "doc990",
    label: "Doc990",
    blurb: "Doctors you can book on the Doc990 network.",
  },
  {
    id: "google_places",
    label: "Hospitals nearby",
    blurb: "Places you can walk into, including government hospitals.",
  },
];

/** The endpoint behind each tab. */
export const PROVIDER_PATH: Record<ProviderId, string> = {
  echannelling: "doctors",
  doc990: "doc990",
  google_places: "clinics",
};

export const AVAILABILITY = ["This week", "Evenings", "Weekends", "As soon as possible"];
export const RADII = [5, 10, 25];

export interface ProviderHealth {
  name: string;
  configured: boolean;
  reachable: boolean;
  detail: string | null;
}

export interface ReferralHealth {
  providers: ProviderHealth[];
  usable: boolean;
}

export interface SearchRequest {
  location: string;
  specialty?: string | null;
  availability?: string | null;
  radius_km?: number | null;
}

// --- the API ---------------------------------------------------------------

/** Who this finding should be taken to. Needs no location. */
export async function getSpecialties(
  findingId: string,
  token: string | null,
): Promise<SpecialtyList> {
  return apiFetch<SpecialtyList>(`/referrals/for-finding/${findingId}`, { token });
}

/**
 * What a search is being run for.
 *
 * Two things send a patient looking for a doctor, and they arrive by different
 * routes. A *finding* is something the cross-checks flagged — two medicines
 * that interact — and its specialties come from the routing table. A *document*
 * is a lab report, whose specialties were chosen by reading the report itself.
 * Most reports never raise a finding, so without the second kind the whole of
 * lab-driven referral would be unreachable.
 */
export type Subject = { kind: "finding" | "document"; id: string };

const SUBJECT_PATH: Record<Subject["kind"], string> = {
  finding: "for-finding",
  document: "for-document",
};

/**
 * One directory's answer for one finding or report.
 *
 * Called once per tab rather than once per search, and deliberately not
 * awaited together: eChannelling's gateway takes seconds where Google answers
 * in under two, so the caller fires all three and paints each as it lands.
 * Waiting for the slowest would hold the whole screen for a tab the reader may
 * never open.
 */
export async function searchProvider(
  subject: Subject,
  provider: ProviderId,
  body: SearchRequest,
  token: string | null,
): Promise<ReferralSearch> {
  return apiFetch<ReferralSearch>(
    `/referrals/${SUBJECT_PATH[subject.kind]}/${subject.id}/${PROVIDER_PATH[provider]}`,
    { token, method: "POST", json: body },
  );
}

/**
 * Place names matching what is being typed.
 *
 * Proxied through our own API rather than called from the page, so the Maps
 * key stays on the server where it is restricted to one address. Returns an
 * empty list rather than throwing when autocomplete is unavailable — a dead
 * suggestion list must not stop someone searching for a town they can spell.
 */
export async function suggestLocations(
  typed: string,
  token: string | null,
): Promise<string[]> {
  if (typed.trim().length < 2) return [];
  try {
    const found = await apiFetch<{ suggestions: string[] }>(
      `/referrals/locations?q=${encodeURIComponent(typed.trim())}`,
      { token },
    );
    return found.suggestions;
  } catch {
    return [];
  }
}

/** Whether the directories are answering. Worth checking before a demo. */
export async function getReferralHealth(token: string | null): Promise<ReferralHealth> {
  return apiFetch<ReferralHealth>("/referrals/health", { token });
}

/** How far away, in the units a person would say it in. */
export function distanceLabel(km?: number | null): string | null {
  if (km === null || km === undefined) return null;
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(1)} km`;
}

/**
 * When this place is open today.
 *
 * Today is decided here rather than on the server, because the reader's day is
 * the one that matters: at 1 am in Colombo a server running in UTC is still on
 * yesterday, and would quote the wrong line to anyone up late.
 *
 * Returns null when the directory publishes no hours — which the row prints as
 * "not published, call to confirm" rather than leaving blank. An empty slot
 * reads as closed.
 */
export function hoursToday(hours: string[]): string | null {
  if (hours.length < 7) return hours[0] ?? null;
  if (hours.every((line) => /open 24 hours/i.test(line))) return "Open 24 hours, every day";
  // Google writes the week starting Monday; getDay() starts it on Sunday.
  const line = hours[(new Date().getDay() + 6) % 7];
  return line ? line.replace(/^[A-Za-z]+:\s*/, "Today · ") : null;
}

/** Initials for the tile beside a row. "Nawaloka Hospital" -> "NH". */
export function initialsOf(name: string): string {
  const words = name
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter((word) => word && !/^(dr|prof|mr|mrs|ms|the|of|and)$/i.test(word));
  return words.slice(0, 2).map((word) => word[0].toUpperCase()).join("") || "?";
}
