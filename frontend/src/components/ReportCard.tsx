/**
 * What a lab report looks at, and who usually reads one.
 *
 * Shown when a lab report is opened, because that is the moment the question
 * arises: results have come back and the person holding them does not know
 * what to do next. It deliberately does not wait for a finding — most reports
 * raise none, and both of this app's real examples (a creatinine 0.04 below
 * range, an HIV result reported as a word) produce nothing on the findings
 * page while plainly being worth asking a doctor about.
 *
 * Three rules the layout has to keep:
 *
 * Results reported in words carry no verdict. 'detected' is printed beside
 * 'expected: Not detected' and the reader concludes; nothing on this screen
 * marks it right or wrong, because an HIV result decided wrongly is the worst
 * thing this app could do.
 *
 * The laboratory's note is a quotation, attributed, in their words. It is the
 * only place a condition may be named, and the name is theirs.
 *
 * No disease is stated anywhere else. "This looks at kidney function" is what
 * the app may say; what that means is a doctor's to tell them.
 */

import { useEffect, useState } from "react";
import { Corners } from "./chrome";
import { describeError } from "../lib/errors";
import { CONFIDENCE_LABEL, getReportRecommendation } from "../lib/reportCard";
import type { ReportRecommendation, Reading } from "../lib/reportCard";
import { useAuth } from "../lib/useAuth";

const MUTED = "rgba(29,31,32,.6)";
const FAINT = "rgba(29,31,32,.45)";

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 9.5,
        letterSpacing: ".11em",
        textTransform: "uppercase",
        color: FAINT,
        marginBottom: 7,
      }}
    >
      {children}
    </div>
  );
}

/** One result. Out-of-range rows are marked; word results are only shown. */
function Row({ item }: { item: Reading }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 12,
        alignItems: "baseline",
        padding: "7px 0",
        borderTop: "1px solid var(--color-divider)",
        fontSize: 13,
      }}
    >
      <span style={{ flex: "1 1 auto", minWidth: 0 }}>{item.analyte}</span>
      <span
        style={{
          flex: "none",
          fontVariantNumeric: "tabular-nums",
          color: item.abnormal ? "var(--color-danger-text)" : "var(--color-text)",
        }}
      >
        {item.value}
        {item.unit ? ` ${item.unit}` : ""}
      </span>
      <span style={{ flex: "none", width: 150, textAlign: "right", color: FAINT, fontSize: 12 }}>
        {item.qualitative
          ? item.reference
            ? `expected: ${item.reference}`
            : "no expected value printed"
          : item.reference || "no range printed"}
      </span>
      <span style={{ flex: "none", width: 74, textAlign: "right", fontSize: 11.5 }}>
        {item.abnormal ? (
          <span style={{ color: "var(--color-danger-text)" }}>
            outside{item.lab_flagged ? " ⚑" : ""}
          </span>
        ) : item.qualitative ? (
          <span style={{ color: FAINT }}>—</span>
        ) : (
          <span style={{ color: FAINT }}>normal</span>
        )}
      </span>
    </div>
  );
}

export default function ReportCard({
  documentId,
  onFindDoctor,
  chosenSpecialty = null,
}: {
  documentId: string;
  /** Wired, this card becomes the specialty picker for the search below it. */
  onFindDoctor?: (specialty: string, reason: string, confidence: string) => void;
  chosenSpecialty?: string | null;
}) {
  const { getToken } = useAuth();
  const [data, setData] = useState<ReportRecommendation | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let live = true;
    setData(null);
    setProblem(null);
    void (async () => {
      try {
        const found = await getReportRecommendation(documentId, await getToken());
        if (live) setData(found);
      } catch (error) {
        if (live) setProblem(describeError(error));
      }
    })();
    return () => {
      live = false;
    };
  }, [documentId, getToken]);

  if (problem) {
    return (
      <div style={{ padding: "14px 18px", fontSize: 13, color: MUTED }}>
        This report could not be summarised — {problem}
      </div>
    );
  }

  if (!data) {
    return (
      <div style={{ padding: "14px 18px", fontSize: 13, color: FAINT }}>
        Reading this report…
      </div>
    );
  }

  const abnormal = data.readings.filter((r) => r.abnormal || r.qualitative);
  const shown = showAll ? data.readings : abnormal;

  return (
    <div className="card blueprint" style={{ padding: "16px 18px", gap: 0, margin: "0 0 2px" }}>
      <Corners />

      <div style={{ display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--font-heading)", fontSize: 17, flex: "1 1 auto" }}>
          {data.panel_names.join(" · ") || data.filename || "Lab report"}
        </span>
        <span style={{ fontSize: 12.5, color: MUTED }}>
          {data.result_count} results
          {data.abnormal_count > 0 && (
            <>
              {" · "}
              <span style={{ color: "var(--color-danger-text)" }}>
                {data.abnormal_count} outside range
              </span>
            </>
          )}
        </span>
      </div>

      {data.about && (
        <div style={{ marginTop: 10 }}>
          <Label>What this report looks at</Label>
          <div style={{ fontSize: 14, lineHeight: 1.5 }}>{data.about}</div>
        </div>
      )}

      {/* The laboratory's own sentence, quoted and attributed. The only place
          on this screen a condition may be named — and the name is theirs. */}
      {data.interpretation && (
        <div style={{ marginTop: 14 }}>
          <Label>Your laboratory wrote</Label>
          <blockquote
            style={{
              margin: 0,
              padding: "10px 13px",
              borderLeft: "2px solid var(--color-accent)",
              background: "color-mix(in srgb, var(--color-accent) 7%, transparent)",
              fontSize: 13.5,
              lineHeight: 1.55,
              whiteSpace: "pre-wrap",
            }}
          >
            {data.interpretation}
            <footer style={{ marginTop: 6, fontSize: 11.5, color: FAINT }}>
              — {data.provider || "your laboratory"}, from this report
            </footer>
          </blockquote>
        </div>
      )}

      {shown.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <Label>
            {showAll ? "All results" : abnormal.length > 0 ? "Worth looking at" : "Results"}
          </Label>
          <div>
            {shown.map((item) => (
              <Row key={`${item.analyte}-${item.value}`} item={item} />
            ))}
          </div>
          {data.readings.length > abnormal.length && (
            <button
              type="button"
              className="btn-link"
              onClick={() => setShowAll((v) => !v)}
              style={{ marginTop: 9, fontSize: 12.5 }}
            >
              {showAll
                ? "Show only what is outside range"
                : `Show all ${data.readings.length} results`}
            </button>
          )}
          {abnormal.some((r) => r.lab_flagged) && (
            <div style={{ fontSize: 11.5, color: FAINT, marginTop: 8 }}>
              ⚑ your laboratory marked this abnormal too
            </div>
          )}
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <Label>Usually reviewed by</Label>
        {data.specialties.length === 0 ? (
          <div style={{ fontSize: 13, color: FAINT, lineHeight: 1.5 }}>
            {data.error ||
              "We could not work out which specialist reads reports like this. A general doctor can review it."}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
            {data.specialties.map((item, index) => {
              const selected = item.specialty === chosenSpecialty;
              return (
              <div
                key={item.specialty}
                style={{
                  display: "flex",
                  gap: 12,
                  alignItems: "flex-start",
                  padding: "10px 12px",
                  border: "1px solid",
                  borderColor: selected ? "var(--color-accent)" : "var(--color-divider)",
                  background: selected
                    ? "color-mix(in srgb, var(--color-accent) 8%, transparent)"
                    : "transparent",
                }}
              >
                <span
                  style={{
                    flex: "none",
                    fontSize: 11.5,
                    color: FAINT,
                    fontVariantNumeric: "tabular-nums",
                    marginTop: 2,
                  }}
                >
                  {index + 1}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ fontFamily: "var(--font-heading)", fontSize: 15 }}>
                      {item.specialty}
                    </span>
                    <span style={{ fontSize: 11, color: FAINT }}>
                      {CONFIDENCE_LABEL[item.confidence] ?? item.confidence}
                    </span>
                    {item.directories.length > 1 && (
                      <span
                        style={{
                          fontSize: 10,
                          letterSpacing: ".06em",
                          textTransform: "uppercase",
                          padding: "2px 6px",
                          border: "1px solid var(--color-divider)",
                          color: MUTED,
                        }}
                        title="Listed by both eChannelling and Doc990"
                      >
                        Both directories
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12.5, color: MUTED, lineHeight: 1.5, marginTop: 3 }}>
                    {item.reason}
                  </div>
                </div>
                {onFindDoctor && (
                  <button
                    type="button"
                    className={selected ? "btn btn-primary" : "btn btn-secondary"}
                    style={{ flex: "none" }}
                    onClick={() =>
                      onFindDoctor(item.specialty, item.reason, item.confidence)
                    }
                  >
                    {selected ? "Selected" : "Find one"}
                  </button>
                )}
              </div>
              );
            })}
          </div>
        )}
      </div>

      <p
        style={{
          margin: "14px 0 0",
          fontSize: 11.5,
          color: FAINT,
          lineHeight: 1.5,
        }}
      >
        {data.disclaimer}
      </p>
    </div>
  );
}
