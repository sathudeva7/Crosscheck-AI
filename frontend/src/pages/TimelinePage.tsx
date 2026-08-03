/**
 * The merged timeline — every document reduced to structured facts and put in
 * one chronological order.
 *
 * Layout copied from the prototype: the 26px left padding, the 1px rail inset
 * 6px top and bottom, the 11px square dots hanging at `left:-26px`, the 22px
 * gap between visits, the 14px/16px card padding.
 *
 * The dot is the whole idea of the screen in one mark. Hollow means the visit
 * was read and nothing crossed it; filled and red means a cross-check found
 * something involving it. So the shape of a patient's history — quiet stretches
 * and the points where records disagree — is legible before a word is read.
 *
 * Every value here comes from `GET /timeline`. Nothing is computed in the
 * browser that the API already decided: `abnormal` comes from the reference
 * range on the server, `severity` from the cross-checks. A second opinion
 * formed here could disagree with the one on the Findings screen, and on this
 * subject two answers is worse than one.
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Corners } from "../components/chrome";
import { docTypeLabel, formatDocDate } from "../lib/documents";
import type { DocType } from "../lib/documents";
import { describeError } from "../lib/errors";
import { getTimeline } from "../lib/timeline";
import type { Severity, Timeline, TimelineEvent, TimelineVisit } from "../lib/timeline";
import { useAuth } from "../lib/useAuth";

const MUTED = "rgba(29,31,32,.6)";
const FAINT = "rgba(29,31,32,.45)";

/** Out-of-range values are the only red on this screen, so the eye finds them
 *  without the layout having to point. */
function labValueStyle(abnormal: boolean) {
  return {
    fontFamily: "var(--font-heading)",
    fontWeight: 600,
    fontSize: 15,
    color: abnormal ? "var(--color-danger)" : "var(--color-text)",
  };
}

function Dot({ severity }: { severity: Severity }) {
  // 'info' is not worth a red mark — it is a note, not a conflict. Only the
  // two levels that mean something crossed get the filled dot.
  const flagged = severity === "danger" || severity === "warning";
  return (
    <div
      aria-hidden
      style={{
        position: "absolute",
        left: -26,
        top: 4,
        width: 11,
        height: 11,
        border: `2px solid ${flagged ? "var(--color-danger)" : "var(--color-accent)"}`,
        background: flagged ? "var(--color-danger)" : "var(--color-bg)",
      }}
    />
  );
}

/** The small uppercase label above a group inside a visit card. */
function GroupLabel({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        textTransform: "uppercase",
        letterSpacing: ".08em",
        color: FAINT,
        marginBottom: 6,
      }}
    >
      {children}
    </div>
  );
}

function MedicationPill({ event }: { event: TimelineEvent }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 10px",
        border: "1px solid var(--color-divider)",
        fontSize: 13,
      }}
      // The extraction may be unsure; the pill should not look certain.
      title={event.review_status === "needs_review" ? "Awaiting review" : undefined}
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="var(--color-accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flex: "none" }}
        aria-hidden
      >
        <path d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z" />
        <path d="m8.5 8.5 7 7" />
      </svg>
      {event.display_text || event.drug_name || event.drug_raw || "Unnamed medicine"}
      {event.review_status === "needs_review" && (
        <span aria-label="awaiting review" style={{ color: "var(--color-accent)" }}>
          ·
        </span>
      )}
    </span>
  );
}

/** A measurement and its number, red when it fell outside its range. */
function Measurement({ event }: { event: TimelineEvent }) {
  const value =
    event.value_num !== null
      ? `${event.value_num}${event.unit ? ` ${event.unit}` : ""}`
      : event.value_text || "—";

  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
      <span style={{ fontSize: 13, color: MUTED }}>{event.analyte || event.title}</span>
      <span style={labValueStyle(event.abnormal)}>{value}</span>
    </div>
  );
}

/** Everything that is not a drug or a number: diagnoses, allergies, notes. */
function FactLine({ event }: { event: TimelineEvent }) {
  return (
    <li style={{ fontSize: 13.5, marginBottom: 3 }}>
      {event.display_text || event.description || event.substance || event.title}
      {event.reaction && <span style={{ color: MUTED }}> — {event.reaction}</span>}
    </li>
  );
}

const MEASURED = new Set(["lab_result", "vital_sign"]);
const OTHER = new Set(["diagnosis", "allergy", "procedure", "note"]);

function Visit({ visit }: { visit: TimelineVisit }) {
  const medications = visit.events.filter((e) => e.event_type === "medication");
  const measurements = visit.events.filter((e) => MEASURED.has(e.event_type));
  const others = visit.events.filter((e) => OTHER.has(e.event_type));

  const when = formatDocDate(visit.visit_date) ?? visit.date_raw ?? "Undated";
  const who = visit.clinician_name || visit.provider;

  return (
    <div style={{ position: "relative" }}>
      <Dot severity={visit.severity} />

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          marginBottom: 8,
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 16 }}>
          {when}
        </span>
        {/* A date read from an ambiguous format is a real source of error —
            04-07 is April in one country and July in another — so it says so
            rather than presenting a guess as a fact. */}
        {visit.date_ambiguous && (
          <span className="tag tag-neutral" title={visit.date_raw ?? undefined}>
            date unclear
          </span>
        )}
        <span className="tag tag-outline">{docTypeLabel((visit.doc_type ?? "unknown") as DocType)}</span>
        {who && <span style={{ fontSize: 13, color: "rgba(29,31,32,.55)" }}>{who}</span>}
      </div>

      <div className="card" style={{ padding: "14px 16px", gap: 12 }}>
        {visit.summary && <p style={{ margin: 0, fontSize: 14 }}>{visit.summary}</p>}

        {medications.length > 0 && (
          <div>
            <GroupLabel>Medications</GroupLabel>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {medications.map((event) => (
                <MedicationPill key={event.id} event={event} />
              ))}
            </div>
          </div>
        )}

        {measurements.length > 0 && (
          <div>
            <GroupLabel>Lab values</GroupLabel>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
              {measurements.map((event) => (
                <Measurement key={event.id} event={event} />
              ))}
            </div>
          </div>
        )}

        {others.length > 0 && (
          <div>
            <GroupLabel>Also recorded</GroupLabel>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {others.map((event) => (
                <FactLine key={event.id} event={event} />
              ))}
            </ul>
          </div>
        )}

        {visit.events.length === 0 && (
          <p style={{ margin: 0, fontSize: 13.5, color: MUTED }}>
            This document was read, but nothing structured could be taken from it.
          </p>
        )}
      </div>
    </div>
  );
}

/** Shown before anything has been uploaded — the timeline has no meaning yet,
 *  and an empty rail would read as 'nothing found in your records'. */
function Empty() {
  return (
    <div className="card blueprint" style={{ padding: "34px 24px", textAlign: "center", gap: 8 }}>
      <Corners />
      <div style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 19 }}>
        Nothing on the timeline yet
      </div>
      <p style={{ margin: 0, fontSize: 13.5, color: MUTED }}>
        Upload a lab report, prescription or discharge summary and it will appear here, merged
        with everything else.
      </p>
      <Link to="/app/documents" className="btn btn-secondary" style={{ marginTop: 12 }}>
        Add documents
      </Link>
    </div>
  );
}

export default function TimelinePage() {
  const { state, email, getToken } = useAuth();
  const patientName =
    (state.status === "ready" ? state.patient.full_name : null) ?? email ?? "your records";

  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTimeline(await getTimeline(await getToken()));
      setError(null);
    } catch (problem) {
      setError(describeError(problem));
    }
  }, [getToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = timeline?.counts;

  return (
    <div className="cc-rise">
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          marginBottom: 6,
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <div>
          <h6 style={{ color: "var(--color-accent)", marginBottom: 4 }}>Merged patient record</h6>
          <h2 style={{ margin: 0, fontSize: 30 }}>Timeline · {patientName}</h2>
        </div>
        {/* Held back until the real numbers arrive. A count that appears as 0
            and then changes reads as data being lost. */}
        {counts && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <span className="tag tag-accent">
              {counts.visits} {counts.visits === 1 ? "visit" : "visits"}
            </span>
            <span className="tag tag-accent">
              {counts.medications} {counts.medications === 1 ? "medication" : "medications"}
            </span>
            <span className="tag tag-neutral">
              {counts.lab_values} {counts.lab_values === 1 ? "lab value" : "lab values"}
            </span>
            {counts.needs_review > 0 && (
              <Link to="/app/review" className="tag tag-danger" style={{ textDecoration: "none" }}>
                {counts.needs_review} to review
              </Link>
            )}
          </div>
        )}
      </div>

      <p style={{ color: MUTED, maxWidth: "64ch", marginBottom: 26 }}>
        Structured data extracted from every document and ordered chronologically. Values outside
        their normal range are marked in red.
      </p>

      {error && (
        <div className="card" style={{ padding: "14px 16px", gap: 8 }}>
          <p style={{ margin: 0, fontSize: 14, color: "var(--color-danger-text)" }}>{error}</p>
          <button type="button" className="btn btn-secondary" onClick={() => void load()}>
            Try again
          </button>
        </div>
      )}

      {/* `timeline === null` is 'not asked yet', not 'nothing there'. Saying
          the record is empty before the first response would be a guess. */}
      {!error && timeline === null && (
        <p style={{ color: FAINT, fontSize: 14 }}>Loading your record…</p>
      )}

      {!error && timeline !== null && timeline.visits.length === 0 && <Empty />}

      {!error && timeline !== null && timeline.visits.length > 0 && (
        <div style={{ position: "relative", paddingLeft: 26 }}>
          {/* Inset 6px top and bottom so the rail stops level with the first and
              last dot instead of running past them. */}
          <div
            aria-hidden
            style={{
              position: "absolute",
              left: 5,
              top: 6,
              bottom: 6,
              width: 1,
              background: "var(--color-divider)",
            }}
          />
          <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
            {timeline.visits.map((visit) => (
              <Visit key={visit.id} visit={visit} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
