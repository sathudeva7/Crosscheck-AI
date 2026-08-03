/**
 * What the cross-checks found.
 *
 * These types mirror `FindingListRead` in app/schemas/finding.py.
 *
 * Each check writes its own `evidence` keys — an interaction records `drugs`
 * and `dates`, an allergy conflict records `drug` and `allergy`. `involved`
 * below is the single place that knows the difference, so the card can render
 * any finding type without a branch in it.
 *
 * `coverage` is not decoration and must be rendered. Zero findings means one of
 * two very different things — everything was checked and nothing was wrong, or
 * nothing could be checked — and a screen that cannot tell them apart will
 * eventually show a reassuring empty state over an unread prescription.
 */

import { apiFetch } from "./api";

export type FindingType =
  | "interaction"
  | "duplicate_therapy"
  | "allergy_conflict"
  | "dosage_conflict"
  | "lab_trend"
  | "critical_value";

/** What the backend stores. 'danger' is the worst. */
export type Severity = "danger" | "warning" | "info";

export interface Finding {
  id: string;
  finding_type: FindingType;
  severity: Severity;
  title: string;
  detail: string | null;
  /** 'template' means the model was unavailable; the finding itself is unaffected. */
  detail_source: string | null;
  /** What it was built from. Keys differ per check — see `involved` below. */
  evidence: Record<string, unknown>;
  event_ids: string[];
  visit_ids: string[];
  status: string;
  dismissed_reason: string | null;
  first_seen_at: string;
  /**
   * How well the underlying rows were read, 0–1 — the weakest extracted fact
   * the finding rests on. Null when none of them carry a score.
   *
   * Not a probability that the finding is real: whether it exists was decided
   * by SQL against the interaction rules. This says only how well we read the
   * drugs it is about.
   */
  confidence: number | null;
}

export interface CheckCoverage {
  medicines_total: number;
  medicines_checked: number;
  /** Named, not counted: 'we could not read 1 of your medicines' is useless. */
  unchecked: string[];
  rules_available: number;
}

export interface FindingList {
  coverage: CheckCoverage;
  counts: Record<string, number>;
  findings: Finding[];
}

export async function listFindings(token: string | null): Promise<FindingList> {
  return apiFetch<FindingList>("/findings", { token });
}

export async function dismissFinding(
  token: string | null,
  id: string,
  reason?: string,
): Promise<Finding> {
  return apiFetch<Finding>(`/findings/${id}/dismiss`, {
    token,
    method: "POST",
    json: { reason: reason ?? null },
  });
}

// --- presentation ------------------------------------------------------------

/** How a severity reads on screen. Three levels, three colours, no others. */
export const SEVERITY: Record<
  Severity,
  { label: string; rail: string; bg: string; text: string }
> = {
  danger: {
    label: "High risk",
    rail: "var(--color-danger)",
    bg: "var(--color-danger-bg)",
    text: "var(--color-danger-text)",
  },
  warning: {
    label: "Moderate",
    rail: "var(--color-warning)",
    bg: "var(--color-warning-bg)",
    text: "var(--color-warning-text)",
  },
  info: {
    label: "Monitor",
    rail: "var(--color-accent)",
    bg: "var(--color-accent-100)",
    text: "var(--color-accent-800)",
  },
};

/** Most serious first, then alphabetically — the same order the API sorts in. */
export const SEVERITY_RANK: Record<Severity, number> = { danger: 0, warning: 1, info: 2 };

export interface Involved {
  name: string;
  /** Where it came from, so a claim can be followed back to a document. */
  source: string;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** '2025-08-07' → 'Aug 07, 2025'. Returns the input unchanged if unparseable. */
function when(value: unknown): string {
  const raw = text(value);
  if (!raw) return "";
  const parsed = new Date(`${raw}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
  });
}

/**
 * The drugs, allergies or measurements a finding is about.
 *
 * Read out of `evidence` rather than stored twice, and each check writes its
 * own keys — an interaction records `drugs` and `dates`, an allergy conflict
 * records `drug` and `allergy`, a dosage conflict records pre-formatted
 * `strengths`. This is the one place that knows the difference; the card just
 * renders whatever comes back.
 */
export function involved(finding: Finding): Involved[] {
  const evidence = finding.evidence ?? {};

  switch (finding.finding_type) {
    case "interaction":
    case "duplicate_therapy": {
      const dates = list(evidence.dates);
      return list(evidence.drugs).map((drug, index) => ({
        name: text(drug),
        source: when(dates[index]),
      }));
    }

    case "allergy_conflict": {
      // dates[0] is when the allergy was recorded, dates[1] the prescription.
      const dates = list(evidence.dates);
      const reaction = text(evidence.allergy_reaction);
      return [
        { name: text(evidence.drug), source: when(dates[1]) },
        {
          name: `${text(evidence.allergy)} allergy${reaction ? ` (${reaction})` : ""}`,
          source: when(dates[0]),
        },
      ].filter((row) => row.name.trim());
    }

    case "dosage_conflict": {
      // Already formatted by the check as '500 mg on 2025-08-07'.
      const drug = text(evidence.drug);
      return list(evidence.strengths).map((entry) => {
        const [amount, date] = text(entry).split(" on ");
        return { name: `${drug} ${amount}`.trim(), source: when(date) };
      });
    }

    case "lab_trend": {
      const readings = list(evidence.readings) as { date?: string; value?: number }[];
      if (readings.length === 0) return [];
      const first = readings[0];
      const last = readings[readings.length - 1];
      const unit = text(evidence.unit);
      return [
        {
          name: `${text(evidence.analyte)} ${first.value} → ${last.value}${unit ? ` ${unit}` : ""}`,
          source: `${readings.length} tests · ${when(first.date)} – ${when(last.date)}`,
        },
      ];
    }

    case "critical_value": {
      const unit = text(evidence.unit);
      return [
        {
          name: `${text(evidence.analyte)} ${evidence.value}${unit ? ` ${unit}` : ""}`,
          source: when(evidence.date),
        },
      ];
    }

    default:
      return [];
  }
}

/**
 * The sentence that has to accompany an assumed overlap.
 *
 * Two prescriptions with no stated duration might never have been taken at the
 * same time. The check assumes they were — which is the safe way round, but it
 * means the warning may not apply, and that has to be said on the card rather
 * than buried in the data.
 */
export function caveat(finding: Finding): string | null {
  const note = finding.evidence?.assumption;
  return typeof note === "string" && note.trim() ? note : null;
}
