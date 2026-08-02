/**
 * The merged timeline — every document reduced to structured facts and put in
 * one chronological order.
 *
 * Copied from the prototype: the 26px left padding, the 1px rail inset 6px top
 * and bottom, the 11px square dots hanging at `left:-26px`, the 22px gap
 * between visits, the 14px/16px card padding.
 *
 * The dot is the whole idea of the screen in one mark. Hollow means the visit
 * was read and nothing crossed it; filled and red means a cross-check found
 * something involving it. So the shape of a patient's history — quiet stretches
 * and the points where records disagree — is legible before a word is read.
 */

import type { ReactNode } from "react";
import { useAuth } from "../lib/useAuth";
import { DEMO_COUNTS, DEMO_TIMELINE } from "../lib/demo";
import type { DemoLab, DemoVisit } from "../lib/demo";

/** Out-of-range values are the only red on this screen, so the eye finds them
 *  without the layout having to point. */
function labValueStyle(lab: DemoLab) {
  return {
    fontFamily: "var(--font-heading)",
    fontWeight: 600,
    fontSize: 15,
    color: lab.inRange ? "var(--color-text)" : "var(--color-danger)",
  };
}

function Dot({ flagged }: { flagged: boolean }) {
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
        color: "rgba(29,31,32,.45)",
        marginBottom: 6,
      }}
    >
      {children}
    </div>
  );
}

function MedicationPill({ label }: { label: string }) {
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
      {label}
    </span>
  );
}

function Visit({ visit }: { visit: DemoVisit }) {
  return (
    <div style={{ position: "relative" }}>
      <Dot flagged={visit.flagged} />

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
          {visit.date}
        </span>
        <span className="tag tag-outline">{visit.type}</span>
        <span style={{ fontSize: 13, color: "rgba(29,31,32,.55)" }}>{visit.provider}</span>
      </div>

      <div className="card" style={{ padding: "14px 16px", gap: 12 }}>
        <p style={{ margin: 0, fontSize: 14 }}>{visit.note}</p>

        {visit.medications.length > 0 && (
          <div>
            <GroupLabel>Medications</GroupLabel>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {visit.medications.map((med) => (
                <MedicationPill key={med} label={med} />
              ))}
            </div>
          </div>
        )}

        {visit.labs.length > 0 && (
          <div>
            <GroupLabel>Lab values</GroupLabel>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
              {visit.labs.map((lab) => (
                <div key={lab.name} style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                  <span style={{ fontSize: 13, color: "rgba(29,31,32,.6)" }}>{lab.name}</span>
                  <span style={labValueStyle(lab)}>{lab.value}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function TimelinePage() {
  const { state, email } = useAuth();
  const patientName =
    (state.status === "ready" ? state.patient.full_name : null) ?? email ?? "your records";

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
        <div style={{ display: "flex", gap: 8 }}>
          <span className="tag tag-accent">{DEMO_COUNTS.visits} visits</span>
          <span className="tag tag-accent">{DEMO_COUNTS.medications} medications</span>
          <span className="tag tag-neutral">{DEMO_COUNTS.labs} lab values</span>
        </div>
      </div>

      <p style={{ color: "rgba(29,31,32,.6)", maxWidth: "64ch", marginBottom: 26 }}>
        Structured data extracted from every document and ordered chronologically. Values outside
        their normal range are marked in red.
      </p>

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
          {DEMO_TIMELINE.map((visit) => (
            <Visit key={visit.id} visit={visit} />
          ))}
        </div>
      </div>
    </div>
  );
}
