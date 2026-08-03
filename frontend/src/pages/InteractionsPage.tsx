/**
 * What the cross-checks found, most serious first.
 *
 * Layout from the prototype: the three-up count strip, the 5px severity rail
 * down the left of each card, the involved-drug chips carrying their source
 * underneath, the confidence bar, and the accent-tinted recommendation.
 *
 * Two things on this screen are load-bearing rather than decorative.
 *
 * `coverage` is rendered whether or not anything was found, because a list of
 * zero findings means either 'everything was checked and nothing was wrong' or
 * 'nothing could be checked', and those must never look the same.
 *
 * Each chip names the document and date it came from. A warning about two
 * drugs is only actionable if the reader can see which prescriptions
 * disagreed — that is the whole difference between this and a lookup someone
 * could have done themselves.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Corners } from "../components/chrome";
import { describeError } from "../lib/errors";
import { SEVERITY, SEVERITY_RANK, caveat, involved, listFindings } from "../lib/findings";
import type { Finding, FindingList, Severity } from "../lib/findings";
import { useAuth } from "../lib/useAuth";

const MUTED = "rgba(29,31,32,.6)";
const FAINT = "rgba(29,31,32,.45)";

/** One figure across the top. Order matches the severity ranking. */
function Count({ value, label, colour }: { value: number; label: string; colour: string }) {
  return (
    <div className="card blueprint" style={{ padding: "14px 16px", gap: 2 }}>
      <Corners />
      <div
        style={{
          fontFamily: "var(--font-heading)",
          fontWeight: 600,
          fontSize: 32,
          lineHeight: 1.05,
          color: colour,
        }}
      >
        {value}
      </div>
      <div style={{ fontSize: 12.5, color: MUTED }}>{label}</div>
    </div>
  );
}

/** A drug, allergy or measurement the finding is about, with its source. */
function Chip({ name, source }: { name: string; source: string }) {
  return (
    <span
      style={{
        display: "inline-flex",
        flexDirection: "column",
        padding: "6px 11px",
        border: "1px solid var(--color-divider)",
        background: "var(--color-surface)",
      }}
    >
      <span style={{ fontSize: 13.5, fontWeight: 500 }}>{name}</span>
      {source && <span style={{ fontSize: 11, color: "rgba(29,31,32,.5)" }}>{source}</span>}
    </span>
  );
}

/** The mark on the caveat line — a question, not a warning. */
function Uncertain() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flex: "none", marginTop: 2 }}
      aria-hidden
    >
      <circle cx="12" cy="12" r="10" />
      <path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3" />
      <path d="M12 17h.01" />
    </svg>
  );
}

function FindingCard({ finding, onAsk }: { finding: Finding; onAsk: () => void }) {
  const tone = SEVERITY[finding.severity];
  const parts = involved(finding);
  const score = finding.confidence;
  const assumption = caveat(finding);

  return (
    <div
      className="card blueprint"
      style={{ padding: 0, gap: 0, flexDirection: "row", overflow: "hidden" }}
    >
      <Corners />
      {/* The rail is the only thing readable at a glance in a long list. */}
      <div style={{ width: 5, flex: "none", background: tone.rail }} />

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
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              fontFamily: "var(--font-heading)",
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: ".04em",
              textTransform: "uppercase",
              padding: "3px 9px",
              background: tone.bg,
              color: tone.text,
            }}
          >
            {tone.label}
          </span>
          <span style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 18 }}>
            {finding.title}
          </span>
        </div>

        {parts.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            {parts.map((part) => (
              <Chip key={part.name} name={part.name} source={part.source} />
            ))}
          </div>
        )}

        {finding.detail && (
          <p
            style={{
              margin: "0 0 14px",
              fontSize: 14,
              lineHeight: 1.55,
              color: "rgba(29,31,32,.82)",
            }}
          >
            {finding.detail}
          </p>
        )}

        {/* Absent rather than zero when the finding carries no score: a bar at
            0% reads as 'we are certain this is wrong', which is the opposite. */}
        {score !== null && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              marginBottom: 14,
              flexWrap: "wrap",
            }}
          >
            <span
              style={{
                fontSize: 11,
                textTransform: "uppercase",
                letterSpacing: ".06em",
                color: "rgba(29,31,32,.5)",
                width: 78,
              }}
            >
              Confidence
            </span>
            <div style={{ flex: 1, maxWidth: 200, height: 6, background: "rgba(29,31,32,.1)" }}>
              <div
                style={{
                  height: "100%",
                  width: `${Math.round(score * 100)}%`,
                  background: tone.rail,
                }}
              />
            </div>
            <span style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 14 }}>
              {Math.round(score * 100)}%
            </span>
          </div>
        )}

        {/* Says what the check had to guess. A warning built on an assumed
            overlap must carry that assumption on its face — otherwise the
            reader takes a maybe for a fact, which on this screen is the
            expensive mistake. */}
        {assumption && (
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 9,
              padding: "10px 12px",
              background: "var(--color-surface)",
              borderLeft: "2px solid rgba(29,31,32,.3)",
              color: "rgba(29,31,32,.72)",
            }}
          >
            <Uncertain />
            <div style={{ fontSize: 13, lineHeight: 1.5 }}>
              <strong style={{ fontFamily: "var(--font-heading)", color: "var(--color-text)" }}>
                This may not apply to you.
              </strong>{" "}
              {assumption}
            </div>
          </div>
        )}

        <button
          type="button"
          className="btn btn-ghost"
          style={{ marginTop: 10, paddingInline: 0 }}
          onClick={onAsk}
        >
          Ask CrossCheck about this →
        </button>
      </div>
    </div>
  );
}

/**
 * How much of the record the checks could see.
 *
 * Shown as a plain line when everything was covered and as a warning when it
 * was not, naming the medicines that could not be identified. An unidentified
 * drug matches nothing in the interaction table, so it comes back clean for
 * the same reason a safe drug does.
 */
function Coverage({ coverage }: { coverage: FindingList["coverage"] }) {
  const complete = coverage.medicines_checked === coverage.medicines_total;

  if (complete) {
    return (
      <p style={{ fontSize: 12.5, color: FAINT, margin: "0 0 22px" }}>
        All {coverage.medicines_total} medicines were identified and checked against{" "}
        {coverage.rules_available.toLocaleString()} interaction rules.
      </p>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 9,
        padding: "10px 12px",
        margin: "0 0 22px",
        background: "var(--color-warning-bg)",
        borderLeft: "2px solid var(--color-warning)",
        fontSize: 13,
        color: "var(--color-warning-text)",
      }}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flex: "none", marginTop: 1 }}
        aria-hidden
      >
        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
        <path d="M12 9v4" />
        <path d="M12 17h.01" />
      </svg>
      <div>
        <strong style={{ fontFamily: "var(--font-heading)" }}>
          {coverage.medicines_checked} of {coverage.medicines_total} medicines were checked.
        </strong>{" "}
        {coverage.unchecked.join(", ")} could not be identified, so nothing below covers{" "}
        {coverage.unchecked.length === 1 ? "it" : "them"}. Ask a pharmacist to check{" "}
        {coverage.unchecked.length === 1 ? "it" : "them"} directly.
      </div>
    </div>
  );
}

function Empty({ coverage }: { coverage: FindingList["coverage"] }) {
  const complete = coverage.medicines_checked === coverage.medicines_total;
  return (
    <div className="card blueprint" style={{ padding: "30px 24px", textAlign: "center", gap: 8 }}>
      <Corners />
      <div style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 19 }}>
        {complete ? "Nothing crossed" : "Nothing crossed in what could be read"}
      </div>
      <p style={{ margin: 0, fontSize: 13.5, color: MUTED, maxWidth: "52ch", marginInline: "auto" }}>
        {complete
          ? "No interactions, duplicates or conflicting doses were found across your documents. This is not the same as being told your medicines are safe — take any new medicine to a pharmacist."
          : "The medicines that could be identified came back clean. The ones that could not are named above and remain unchecked."}
      </p>
    </div>
  );
}

export default function InteractionsPage() {
  const navigate = useNavigate();
  const { getToken } = useAuth();

  const [data, setData] = useState<FindingList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await listFindings(await getToken()));
      setError(null);
    } catch (problem) {
      setError(describeError(problem));
    }
  }, [getToken]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="cc-rise">
      <h6 style={{ color: "var(--color-accent)", marginBottom: 4 }}>Safety cross-check</h6>
      <h2 style={{ margin: "0 0 6px", fontSize: 30 }}>Flagged findings</h2>
      <p style={{ color: MUTED, maxWidth: "64ch", marginBottom: 20 }}>
        CrossCheck compared every medication and lab value across all of your documents. Each flag
        lists what it was built from and how well those details were read — none is a diagnosis.
      </p>

      {error && (
        <div className="card" style={{ padding: "14px 16px", gap: 8 }}>
          <p style={{ margin: 0, fontSize: 14, color: "var(--color-danger-text)" }}>{error}</p>
          <button type="button" className="btn btn-secondary" onClick={() => void load()}>
            Try again
          </button>
        </div>
      )}

      {/* `null` is 'not asked yet', not 'nothing found'. Saying the record is
          clear before the first response would be a guess, and on this screen
          a reassuring guess is the one that costs something. */}
      {!error && data === null && (
        <p style={{ color: FAINT, fontSize: 14 }}>Checking your medicines…</p>
      )}

      {!error && data !== null && <Results data={data} onAsk={navigate} />}
    </div>
  );
}

function Results({
  data,
  onAsk,
}: {
  data: FindingList;
  onAsk: (to: string) => void;
}) {
  const findings = [...data.findings].sort(
    (a, b) =>
      SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || a.title.localeCompare(b.title),
  );

  const counts: [Severity, string][] = [
    ["danger", "High-risk flags"],
    ["warning", "Moderate flags"],
    ["info", "To monitor"],
  ];

  return (
    <>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 12,
          marginBottom: 22,
        }}
      >
        {counts.map(([severity, label]) => (
          <Count
            key={severity}
            value={data.counts[severity] ?? 0}
            label={label}
            colour={SEVERITY[severity].rail}
          />
        ))}
      </div>

      <Coverage coverage={data.coverage} />

      {findings.length === 0 ? (
        <Empty coverage={data.coverage} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {findings.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              onAsk={() =>
                onAsk(`/app/chat?q=${encodeURIComponent(`Tell me about: ${finding.title}`)}`)
              }
            />
          ))}
        </div>
      )}
    </>
  );
}
