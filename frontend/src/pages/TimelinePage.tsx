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
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { Corners } from "../components/chrome";
import { docTypeLabel, formatDocDate } from "../lib/documents";
import type { DocType } from "../lib/documents";
import { describeError } from "../lib/errors";
import { correctEvent, deleteVisit, getTimeline, rejectEvent } from "../lib/timeline";
import { deleteDocument } from "../lib/documents";
import type {
  EventCorrection,
  Severity,
  Timeline,
  TimelineEvent,
  TimelineVisit,
} from "../lib/timeline";
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

// --- editing -----------------------------------------------------------------

/**
 * Which fields a person may correct, per kind of fact.
 *
 * Deliberately not every column. What is offered here is what the *document
 * said* — a misread drug name, a height typed as 200 instead of 130. The
 * confidence, the page number and the source quote are records of how the fact
 * was obtained, and a correction changes the fact, not the history of reading
 * it. The API enforces the same list; this mirrors it.
 */
const FIELDS: Record<string, { key: keyof EventCorrection; label: string; width: number }[]> = {
  medication: [
    { key: "drug_name", label: "Medicine", width: 190 },
    { key: "strength_value", label: "Strength", width: 90 },
    { key: "strength_unit", label: "Unit", width: 70 },
    { key: "frequency_label", label: "How often", width: 150 },
  ],
  lab_result: [
    { key: "analyte", label: "Test", width: 150 },
    { key: "value_num", label: "Value", width: 90 },
    { key: "unit", label: "Unit", width: 80 },
    { key: "ref_low", label: "Normal from", width: 90 },
    { key: "ref_high", label: "Normal to", width: 90 },
  ],
  allergy: [
    { key: "substance", label: "Substance", width: 190 },
    { key: "reaction", label: "Reaction", width: 190 },
  ],
  diagnosis: [{ key: "description", label: "Description", width: 400 }],
  note: [{ key: "description", label: "Note", width: 400 }],
};
FIELDS.vital_sign = FIELDS.lab_result;
FIELDS.procedure = FIELDS.diagnosis;

/** What a field currently holds, as text an input can show. */
function valueOf(event: TimelineEvent, key: keyof EventCorrection): string {
  const raw = (event as unknown as Record<string, unknown>)[key];
  return raw === null || raw === undefined ? "" : String(raw);
}

/** A short label for a row being edited, so a Remove button says what it removes. */
function describe(event: TimelineEvent): string {
  return (
    event.display_text ||
    event.drug_name ||
    event.drug_raw ||
    event.analyte ||
    event.description ||
    event.substance ||
    event.title ||
    "this item"
  );
}

type Draft = Record<string, Partial<Record<keyof EventCorrection, string>>>;

function EditRow({
  event,
  draft,
  removed,
  onChange,
  onToggleRemove,
}: {
  event: TimelineEvent;
  draft: Partial<Record<keyof EventCorrection, string>>;
  removed: boolean;
  onChange(key: keyof EventCorrection, value: string): void;
  onToggleRemove(): void;
}) {
  // An unknown type still gets a title field rather than nothing: a row with no
  // way to correct it and no way to remove it is a dead end on the screen.
  const fields = FIELDS[event.event_type] ?? [{ key: "title" as const, label: "Text", width: 400 }];

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "flex-end",
        gap: 8,
        padding: "8px 10px",
        border: "1px solid var(--color-divider)",
        background: removed ? "var(--color-danger-bg)" : "var(--color-bg)",
        // Struck through rather than hidden. Something about to be deleted
        // should stay visible until Save, or Cancel has nothing to undo.
        opacity: removed ? 0.65 : 1,
      }}
    >
      {fields.map(({ key, label, width }) => (
        <label key={key} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <span style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".07em", color: FAINT }}>
            {label}
          </span>
          <input
            className="input"
            disabled={removed}
            style={{ width, minHeight: 32, fontSize: 13, padding: "4px 8px" }}
            value={draft[key] ?? valueOf(event, key)}
            onChange={(e) => onChange(key, e.target.value)}
          />
        </label>
      ))}

      <button
        type="button"
        className="btn btn-secondary"
        style={{ marginLeft: "auto", fontSize: 12, padding: "5px 10px" }}
        onClick={onToggleRemove}
        aria-label={removed ? `Keep ${describe(event)}` : `Remove ${describe(event)}`}
      >
        {removed ? "Keep" : "Remove"}
      </button>
    </div>
  );
}

/** The ⋯ button and the two things behind it. */
function CardMenu({ onEdit, onDelete }: { onEdit(): void; onDelete(): void }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    // Any click anywhere closes it, including the one that opened the next
    // menu — two menus open at once is the bug this prevents.
    const close = () => setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div style={{ position: "relative", marginLeft: "auto" }}>
      <button
        type="button"
        className="cc-dots"
        aria-label="Actions for this visit"
        aria-expanded={open}
        onClick={(e) => {
          // Or the document listener above closes it in the same tick.
          e.stopPropagation();
          setOpen((current) => !current);
        }}
        style={{
          background: "none",
          border: "1px solid transparent",
          cursor: "pointer",
          padding: "2px 7px",
          fontSize: 17,
          lineHeight: 1,
          color: MUTED,
        }}
      >
        ⋯
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 4px)",
            minWidth: 160,
            background: "var(--color-bg)",
            border: "1px solid var(--color-text)",
            boxShadow: "0 8px 22px rgba(29,31,32,.16)",
            zIndex: 30,
          }}
        >
          <button type="button" role="menuitem" className="cc-menu-item" onClick={onEdit}>
            Edit this visit
          </button>
          <button
            type="button"
            role="menuitem"
            className="cc-menu-item cc-menu-danger"
            onClick={onDelete}
          >
            Delete this visit
          </button>
        </div>
      )}
    </div>
  );
}

function Visit({
  visit,
  onChanged,
  onDelete,
}: {
  visit: TimelineVisit;
  onChanged(): Promise<void> | void;
  onDelete(visit: TimelineVisit): void;
}) {
  const { getToken } = useAuth();

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>({});
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const medications = visit.events.filter((e) => e.event_type === "medication");
  const measurements = visit.events.filter((e) => MEASURED.has(e.event_type));
  const others = visit.events.filter((e) => OTHER.has(e.event_type));

  const when = formatDocDate(visit.visit_date) ?? visit.date_raw ?? "Undated";
  const who = visit.clinician_name || visit.provider;

  function stopEditing() {
    setEditing(false);
    setDraft({});
    setRemoved(new Set());
    setProblem(null);
  }

  /** What actually changed, per row. A field typed back to its original value
   *  is not a change and must not be sent — every PATCH marks the row as
   *  user-edited, which permanently exempts it from re-processing. */
  function changesFor(event: TimelineEvent): EventCorrection | null {
    const edits = draft[event.id];
    if (!edits) return null;

    const changed: Record<string, string | null> = {};
    for (const [key, value] of Object.entries(edits)) {
      const before = valueOf(event, key as keyof EventCorrection);
      if (value.trim() === before.trim()) continue;
      // An emptied box means 'this was not on the document', which is null
      // rather than an empty string — the two read differently downstream.
      changed[key] = value.trim() === "" ? null : value.trim();
    }
    return Object.keys(changed).length > 0 ? (changed as EventCorrection) : null;
  }

  async function save() {
    setSaving(true);
    setProblem(null);
    try {
      const token = await getToken();
      // Removals first: a row being taken off does not need correcting, and
      // sending both would mark it edited on its way out.
      for (const event of visit.events) {
        if (removed.has(event.id)) {
          await rejectEvent(token, event.id, "removed while editing the visit");
          continue;
        }
        const changes = changesFor(event);
        if (changes) await correctEvent(token, event.id, changes);
      }
      stopEditing();
      await onChanged();
    } catch (failure) {
      // Left in edit mode on purpose. Some rows may already be saved, and
      // closing the form would hide which ones still are not.
      setProblem(describeError(failure));
    } finally {
      setSaving(false);
    }
  }

  const pending =
    removed.size + visit.events.filter((event) => changesFor(event) !== null).length;

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

        {editing ? (
          <span style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--color-accent)" }}>
            Editing
          </span>
        ) : (
          <CardMenu onEdit={() => setEditing(true)} onDelete={() => onDelete(visit)} />
        )}
      </div>

      <div
        className="card"
        style={{
          padding: "14px 16px",
          gap: 12,
          ...(editing ? { borderColor: "var(--color-accent)" } : {}),
        }}
      >
        {visit.summary && <p style={{ margin: 0, fontSize: 14 }}>{visit.summary}</p>}

        {editing ? (
          <>
            {visit.events.length === 0 ? (
              <p style={{ margin: 0, fontSize: 13.5, color: MUTED }}>
                There is nothing on this visit to edit.
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {visit.events.map((event) => (
                  <EditRow
                    key={event.id}
                    event={event}
                    draft={draft[event.id] ?? {}}
                    removed={removed.has(event.id)}
                    onChange={(key, value) =>
                      setDraft((current) => ({
                        ...current,
                        [event.id]: { ...current[event.id], [key]: value },
                      }))
                    }
                    onToggleRemove={() =>
                      setRemoved((current) => {
                        const next = new Set(current);
                        if (!next.delete(event.id)) next.add(event.id);
                        return next;
                      })
                    }
                  />
                ))}
              </div>
            )}

            {problem && (
              <p style={{ margin: 0, fontSize: 13, color: "var(--color-danger-text)" }}>{problem}</p>
            )}

            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void save()}
                disabled={saving || pending === 0}
              >
                {saving ? "Saving…" : "Save changes"}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={stopEditing}
                disabled={saving}
              >
                Cancel
              </button>
              <span style={{ fontSize: 12, color: FAINT }}>
                {pending === 0
                  ? "Nothing changed yet."
                  : `${pending} change${pending === 1 ? "" : "s"} to save`}
                {removed.size > 0 &&
                  ` · removed items can be restored from Needs review`}
              </span>
            </div>
          </>
        ) : (
          <>
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
          </>
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
  /** The visit awaiting confirmation. Deleting is the one thing here that
   *  cannot be undone, so it does not happen on a single click. */
  const [confirming, setConfirming] = useState<TimelineVisit | null>(null);
  const [deleting, setDeleting] = useState(false);

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

  async function confirmDelete() {
    if (!confirming) return;
    setDeleting(true);
    try {
      const token = await getToken();
      // A card built from an upload is removed through its document, so the
      // file goes too and re-processing cannot bring the visit back. A
      // hand-added card has no document to remove.
      if (confirming.document_id) {
        await deleteDocument(token, confirming.document_id);
      } else {
        await deleteVisit(token, confirming.id);
      }
      setConfirming(null);
      await load();
    } catch (problem) {
      setError(describeError(problem));
      setConfirming(null);
    } finally {
      setDeleting(false);
    }
  }

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
              <Visit key={visit.id} visit={visit} onChanged={load} onDelete={setConfirming} />
            ))}
          </div>
        </div>
      )}

      {confirming &&
        createPortal(
          // Portalled to <body>. The page root carries the cc-rise entrance
          // animation, and an ancestor that animates `transform` becomes the
          // reference box for `position: fixed` children — the dialog would
          // centre itself inside the timeline column instead of the window.
          <div className="cc-overlay" onMouseDown={() => !deleting && setConfirming(null)}>
            <div
              className="cc-dialog"
              role="alertdialog"
              aria-modal="true"
              aria-label="Delete this visit"
              style={{ maxWidth: 430, padding: "18px 20px", gap: 12 }}
              onMouseDown={(event) => event.stopPropagation()}
            >
              <h3 style={{ margin: 0, fontSize: 19 }}>Delete this visit?</h3>

              <p style={{ margin: 0, fontSize: 14, color: MUTED }}>
                {formatDocDate(confirming.visit_date) ?? confirming.date_raw ?? "This visit"}
                {confirming.events.length > 0 && (
                  <>
                    {" · "}
                    {confirming.events.length}{" "}
                    {confirming.events.length === 1 ? "item" : "items"}
                  </>
                )}
              </p>

              {/* Said plainly. This is the one action in the app that does not
                  keep what it removes, and the reader should know that before
                  pressing it rather than after. */}
              <p style={{ margin: 0, fontSize: 13.5 }}>
                {confirming.document_id
                  ? "The uploaded document and everything read from it will be permanently deleted. This cannot be undone."
                  : "This visit and everything on it will be permanently deleted. This cannot be undone."}
              </p>

              <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setConfirming(null)}
                  disabled={deleting}
                >
                  Keep it
                </button>
                <button
                  type="button"
                  className="btn cc-danger"
                  onClick={() => void confirmDelete()}
                  disabled={deleting}
                >
                  {deleting ? "Deleting…" : "Delete permanently"}
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
