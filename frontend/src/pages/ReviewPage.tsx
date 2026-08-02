/**
 * Review — the facts the app is not confident it read correctly.
 *
 * The screen is built around one comparison: what the document says, beside
 * what was extracted from it. Every card shows `source_span`, the exact quote
 * the fact came from, so a decision can be made without opening the original
 * file. Reviewing 30 items by flipping to a PDF each time is work nobody
 * finishes.
 *
 * Order comes from the backend — least confident first, high risk ahead of
 * everything regardless of score. It is not re-sorted here: a medicine at 0.70
 * matters more than a weight at 0.50, and that judgement belongs next to the
 * thresholds that made it, not in a component.
 *
 * Cards leave the list as they are decided, so the queue is always what is
 * left. Nothing is deleted — a rejection is hidden and reversible — but this
 * screen is about getting through the work.
 */

import { useCallback, useEffect, useState } from "react";
import { Corners } from "../components/chrome";
import { useAuth } from "../lib/useAuth";
import { useReviewCount } from "../lib/useReviewCount";
import {
  EVENT_TYPE_LABELS,
  approveEvent,
  confidencePercent,
  correctEvent,
  getReviewQueue,
  reasonLabel,
  rejectEvent,
  riskColour,
} from "../lib/timeline";
import type { EventCorrection, EventType, ReviewItem, Risk } from "../lib/timeline";
import { describeError } from "../lib/errors";

const MUTED = "rgba(29,31,32,.55)";

const RISK_FILTERS: { value: Risk | null; label: string }[] = [
  { value: null, label: "All" },
  { value: "high", label: "High risk" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

/**
 * Which fields are worth showing and editing, per kind of fact.
 *
 * Driven by a table rather than branches so that the read-only view and the
 * correction form can never drift apart — they walk the same list.
 */
const FIELDS: Record<EventType, { key: keyof EventCorrection; label: string }[]> = {
  medication: [
    { key: "drug_raw", label: "As written" },
    { key: "drug_name", label: "Ingredient" },
    { key: "strength_value", label: "Strength" },
    { key: "strength_unit", label: "Unit" },
    { key: "dose_count", label: "Dose" },
    { key: "doses_per_day", label: "Times per day" },
    { key: "frequency_label", label: "Frequency" },
    { key: "duration_days", label: "Duration (days)" },
  ],
  lab_result: [
    { key: "analyte", label: "Test" },
    { key: "value_num", label: "Value" },
    { key: "value_text", label: "Value (text)" },
    { key: "unit", label: "Unit" },
    { key: "ref_low", label: "Range low" },
    { key: "ref_high", label: "Range high" },
    { key: "flag", label: "Flag" },
  ],
  vital_sign: [
    { key: "analyte", label: "Measure" },
    { key: "value_num", label: "Value" },
    { key: "value_text", label: "Value (text)" },
    { key: "unit", label: "Unit" },
  ],
  allergy: [
    { key: "substance", label: "Substance" },
    { key: "reaction", label: "Reaction" },
  ],
  diagnosis: [{ key: "description", label: "Diagnosis" }],
  procedure: [{ key: "description", label: "Procedure" }],
  note: [{ key: "description", label: "Note" }],
};

function Pill({ children, tone }: { children: React.ReactNode; tone: Risk }) {
  const colour = riskColour(tone);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: ".04em",
        textTransform: "uppercase",
        padding: "3px 9px",
        fontFamily: "var(--font-heading)",
        background: colour.bg,
        color: colour.text,
      }}
    >
      {children}
    </span>
  );
}

/** The quote the fact came from. The reason this screen works. */
function SourceQuote({ item }: { item: ReviewItem }) {
  if (!item.source_span) {
    return (
      <div style={{ fontSize: 13, color: "var(--color-danger-text)" }}>
        No supporting text was recorded for this — check the original document.
      </div>
    );
  }
  return (
    <blockquote
      style={{
        margin: 0,
        padding: "10px 13px",
        background: "var(--color-surface)",
        borderLeft: "2px solid var(--color-accent)",
        fontSize: 13.5,
        lineHeight: 1.55,
        whiteSpace: "pre-wrap",
      }}
    >
      {item.source_span}
    </blockquote>
  );
}

function valueOf(item: ReviewItem, key: keyof EventCorrection): string {
  const raw = (item as unknown as Record<string, unknown>)[key];
  if (raw === null || raw === undefined || raw === "") return "";
  return String(raw);
}

function Card({
  item,
  onDecided,
  onError,
}: {
  item: ReviewItem;
  onDecided: (id: string) => void;
  onError: (message: string) => void;
}) {
  const { getToken } = useAuth();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<null | "approve" | "correct" | "reject">(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");

  const fields = FIELDS[item.event_type] ?? [];
  const colour = riskColour(item.risk);
  const percent = confidencePercent(item.confidence);

  function beginEdit() {
    setDraft(Object.fromEntries(fields.map((f) => [f.key, valueOf(item, f.key)])));
    setNote("");
    setEditing(true);
  }

  async function run(action: "approve" | "correct" | "reject") {
    setBusy(action);
    try {
      const token = await getToken();
      if (action === "approve") {
        await approveEvent(token, item.id);
      } else if (action === "reject") {
        await rejectEvent(token, item.id);
      } else {
        // Only what actually changed is sent. `exclude_unset` on the backend
        // treats an omitted key and an explicit null as different instructions,
        // so sending every field would rewrite values nobody touched.
        const changes: EventCorrection = {};
        for (const field of fields) {
          const next = draft[field.key] ?? "";
          if (next !== valueOf(item, field.key)) {
            (changes as Record<string, unknown>)[field.key] = next === "" ? null : next;
          }
        }
        if (note.trim()) changes.note = note.trim();

        if (Object.keys(changes).length === 0) {
          // Nothing edited: approving is what the user meant, and it saves the
          // backend rejecting an empty PATCH with a 400.
          await approveEvent(token, item.id);
        } else {
          await correctEvent(token, item.id, changes);
        }
      }
      onDecided(item.id);
    } catch (problem) {
      onError(describeError(problem));
      setBusy(null);
    }
  }

  return (
    <div
      className="card blueprint"
      style={{ padding: 0, gap: 0, flexDirection: "row", overflow: "hidden" }}
    >
      <Corners />
      <div style={{ width: 5, flex: "none", background: colour.rail }} />

      <div style={{ flex: 1, minWidth: 0, padding: "16px 18px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 10,
            flexWrap: "wrap",
          }}
        >
          <Pill tone={item.risk}>{item.risk} risk</Pill>
          <span className="tag tag-outline">{EVENT_TYPE_LABELS[item.event_type]}</span>
          <span style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 17 }}>
            {item.display_text || item.title || "Untitled"}
          </span>
          {percent !== null && (
            <span style={{ marginLeft: "auto", fontSize: 12.5, color: MUTED }}>
              {percent}% confident
            </span>
          )}
        </div>

        <div style={{ fontSize: 12.5, color: MUTED, marginBottom: 12 }}>
          {[item.visit_date, item.visit_provider, item.visit_doc_type]
            .filter(Boolean)
            .join(" · ")}
          {item.page_no ? ` · page ${item.page_no}` : ""}
        </div>

        {/* Why it is here. Without this the queue is a list of things to guess
            at rather than things to check. */}
        {item.review_reasons && item.review_reasons.length > 0 && (
          <ul
            style={{
              margin: "0 0 14px",
              paddingLeft: 18,
              fontSize: 13,
              lineHeight: 1.6,
              color: "color-mix(in srgb, var(--color-text) 72%, transparent)",
            }}
          >
            {item.review_reasons.map((reason) => (
              <li key={reason}>{reasonLabel(reason)}</li>
            ))}
          </ul>
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(220px, 1fr) minmax(220px, 1fr)",
            gap: 18,
            marginBottom: 14,
          }}
        >
          <div>
            <div style={groupLabel}>What the document says</div>
            <SourceQuote item={item} />
          </div>

          <div>
            <div style={groupLabel}>{editing ? "Correct it" : "What was extracted"}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: editing ? 8 : 4 }}>
              {fields.map((field) => {
                const current = valueOf(item, field.key);
                if (editing) {
                  return (
                    <label key={field.key} style={{ display: "block" }}>
                      <span style={{ fontSize: 11.5, color: MUTED }}>{field.label}</span>
                      <input
                        className="input"
                        style={{ height: 32, fontSize: 13 }}
                        value={draft[field.key] ?? ""}
                        onChange={(e) =>
                          setDraft((d) => ({ ...d, [field.key]: e.target.value }))
                        }
                      />
                    </label>
                  );
                }
                if (!current) return null;
                return (
                  <div key={field.key} style={{ display: "flex", gap: 8, fontSize: 13 }}>
                    <span style={{ color: MUTED, minWidth: 110 }}>{field.label}</span>
                    <span style={{ fontWeight: 500 }}>{current}</span>
                  </div>
                );
              })}

              {editing && (
                <label style={{ display: "block" }}>
                  <span style={{ fontSize: 11.5, color: MUTED }}>Why (optional)</span>
                  <input
                    className="input"
                    style={{ height: 32, fontSize: 13 }}
                    placeholder="e.g. misread the strength"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                </label>
              )}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {editing ? (
            <>
              <button
                className="btn btn-primary"
                disabled={busy !== null}
                onClick={() => void run("correct")}
              >
                {busy === "correct" ? "Saving…" : "Save correction"}
              </button>
              <button
                className="btn btn-ghost"
                disabled={busy !== null}
                onClick={() => setEditing(false)}
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                className="btn btn-primary"
                disabled={busy !== null}
                onClick={() => void run("approve")}
              >
                {busy === "approve" ? "Saving…" : "Looks right"}
              </button>
              <button className="btn btn-secondary" disabled={busy !== null} onClick={beginEdit}>
                Correct
              </button>
              <button
                className="btn btn-ghost"
                disabled={busy !== null}
                style={{ marginLeft: "auto", color: "var(--color-danger-text)" }}
                onClick={() => void run("reject")}
              >
                {busy === "reject" ? "Removing…" : "Not mine / remove"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ReviewPage() {
  const { getToken } = useAuth();
  const { refresh: refreshBadge } = useReviewCount();
  const [risk, setRisk] = useState<Risk | null>(null);
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [highRisk, setHighRisk] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const queue = await getReviewQueue(await getToken(), risk);
      setItems(queue.items);
      setTotal(queue.total);
      setHighRisk(queue.high_risk);
      setError(null);
    } catch (problem) {
      setError(describeError(problem));
    }
  }, [getToken, risk]);

  useEffect(() => {
    void load();
  }, [load]);

  const onDecided = useCallback(
    (id: string) => {
      // Removed locally rather than by refetching: the card disappears the
      // instant the decision lands, and a queue of 40 does not reshuffle under
      // the cursor between one item and the next.
      setItems((current) => current?.filter((item) => item.id !== id) ?? null);
      setTotal((n) => Math.max(0, n - 1));
      void refreshBadge();
    },
    [refreshBadge],
  );

  const empty = items !== null && items.length === 0;

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
          <h6 style={{ color: "var(--color-accent)", marginBottom: 4 }}>Checked by you</h6>
          <h2 style={{ margin: 0, fontSize: 30 }}>Needs review</h2>
        </div>
        <div className="seg">
          {RISK_FILTERS.map((filter) => (
            <label key={filter.label} className="seg-opt">
              <input
                type="radio"
                name="risk"
                checked={risk === filter.value}
                onChange={() => setRisk(filter.value)}
              />
              {filter.label}
            </label>
          ))}
        </div>
      </div>

      <p style={{ color: "rgba(29,31,32,.6)", maxWidth: "64ch", marginBottom: 22 }}>
        Facts the app read but is not confident about. Each one shows the exact text it came from,
        so you can check it without opening the original. Medications need a higher score to pass
        unreviewed than a weight does — the bar is set by what happens if it is wrong.
      </p>

      {error && (
        <div
          style={{
            padding: "11px 14px",
            border: "1px solid var(--color-danger)",
            background: "var(--color-danger-bg)",
            color: "var(--color-danger-text)",
            fontSize: 13,
            marginBottom: 16,
          }}
        >
          {error}{" "}
          <button type="button" className="btn-link" onClick={() => void load()}>
            Try again
          </button>
        </div>
      )}

      {items !== null && !error && (
        <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
          <span className="tag tag-accent">{total} to check</span>
          {highRisk > 0 && (
            <span
              className="tag"
              style={{
                background: "var(--color-danger-bg)",
                color: "var(--color-danger-text)",
              }}
            >
              {highRisk} high risk
            </span>
          )}
        </div>
      )}

      {empty && !error && (
        <div className="card blueprint" style={{ padding: "22px 24px" }}>
          <Corners />
          <div style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 18 }}>
            {risk ? "Nothing at this risk level" : "Nothing to review"}
          </div>
          <p style={{ margin: "6px 0 0", fontSize: 13.5, color: MUTED }}>
            {risk
              ? "Try another filter."
              : "Every fact the app extracted cleared the bar for its kind. Upload more documents and anything uncertain will appear here."}
          </p>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {items?.map((item) => (
          <Card key={item.id} item={item} onDecided={onDecided} onError={setError} />
        ))}
      </div>
    </div>
  );
}

const groupLabel: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: ".08em",
  color: "rgba(29,31,32,.45)",
  marginBottom: 6,
};
