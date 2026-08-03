/**
 * Asking questions across every document at once.
 *
 * Layout from the prototype: right-aligned accent bubbles for the person,
 * bordered surface bubbles for the answer, source chips underneath, and the
 * starter questions that disappear once the conversation begins.
 *
 * What the prototype did not have, and what this screen exists to show, is the
 * apparatus under each answer:
 *
 *   The confidence badge is the weakest extracted fact the answer stands on —
 *   computed from the data, never the model's opinion of itself. When it rests
 *   on nothing it says 'Not scored' rather than showing a number.
 *
 *   The trace names which tools ran. A safety answer that never reached the
 *   interaction database is a different thing from one that did, and the
 *   reader should be able to see which they got.
 *
 *   An ungrounded answer is marked. The verifier flags claims it could not
 *   trace back to a tool result, and those must not look like the rest.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { describeError } from "../lib/errors";
import { BAND, STARTERS, askStreaming, toolLabel } from "../lib/qa";
import type { Answer, Citation, Turn } from "../lib/qa";
import { useAuth } from "../lib/useAuth";

const MUTED = "rgba(29,31,32,.6)";
const FAINT = "rgba(29,31,32,.45)";

interface Entry {
  id: number;
  role: "user" | "assistant";
  text: string;
  /** Present on answers only: the apparatus rendered beneath the bubble. */
  detail?: Answer;
}

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "You";
  return parts
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join("");
}

/** One fact the answer stands on, with enough to find it in the original. */
function Source({ citation }: { citation: Citation }) {
  const where = [
    citation.page_no ? `p${citation.page_no}` : null,
    citation.date,
    // A quote from a handwritten page carries its doubt with it.
    citation.modality && citation.modality !== "digital" ? citation.modality : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <span
      style={{
        display: "inline-flex",
        flexDirection: "column",
        padding: "5px 10px",
        border: "1px solid var(--color-divider)",
        background: "var(--color-surface)",
        maxWidth: 300,
      }}
      title={citation.text ?? undefined}
    >
      <span
        style={{
          fontSize: 12.5,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {citation.text || toolLabel(citation.tool)}
      </span>
      {where && <span style={{ fontSize: 11, color: FAINT }}>{where}</span>}
    </span>
  );
}

function Apparatus({ detail }: { detail: Answer }) {
  const band = BAND[detail.band] ?? BAND.none;
  const percent = detail.confidence === null ? null : Math.round(detail.confidence * 100);

  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
      {detail.citations.length > 0 && (
        <div>
          <div
            style={{
              fontSize: 10.5,
              textTransform: "uppercase",
              letterSpacing: ".07em",
              color: FAINT,
              marginBottom: 5,
            }}
          >
            Built from
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {detail.citations.map((citation, index) => (
              <Source key={`${citation.event_id ?? citation.document_id ?? index}`} citation={citation} />
            ))}
          </div>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            fontSize: 11.5,
            padding: "3px 9px",
            background: band.bg,
            color: band.text,
          }}
          title={detail.basis ?? undefined}
        >
          {band.label}
          {percent !== null && ` · ${percent}%`}
        </span>

        {!detail.grounded && (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              fontSize: 11.5,
              padding: "3px 9px",
              background: "var(--color-danger-bg)",
              color: "var(--color-danger-text)",
            }}
          >
            Not fully supported by your records
          </span>
        )}

        {detail.tool_calls.length > 0 && (
          <span style={{ fontSize: 11.5, color: FAINT }}>
            Checked {[...new Set(detail.tool_calls.map(toolLabel))].join(", ")}
          </span>
        )}
      </div>

      {detail.basis && (
        <div style={{ fontSize: 11.5, color: FAINT }}>{detail.basis}</div>
      )}

      {detail.unsupported && detail.unsupported.length > 0 && (
        <div
          style={{
            fontSize: 12.5,
            padding: "8px 11px",
            background: "var(--color-surface)",
            borderLeft: "2px solid var(--color-danger)",
            color: "rgba(29,31,32,.75)",
          }}
        >
          <strong style={{ fontFamily: "var(--font-heading)", color: "var(--color-text)" }}>
            Could not be traced to your records:
          </strong>{" "}
          {detail.unsupported.join(" · ")}
        </div>
      )}

      <div style={{ fontSize: 11.5, color: FAINT }}>{detail.disclaimer}</div>
    </div>
  );
}

function Bubble({ entry, initials }: { entry: Entry; initials: string }) {
  const mine = entry.role === "user";

  return (
    <div
      style={{
        display: "flex",
        gap: 11,
        maxWidth: "84%",
        ...(mine ? { marginLeft: "auto", flexDirection: "row-reverse" } : {}),
      }}
    >
      <div
        aria-hidden
        style={{
          width: 30,
          height: 30,
          flex: "none",
          display: "grid",
          placeItems: "center",
          fontSize: 12,
          fontFamily: "var(--font-heading)",
          fontWeight: 600,
          ...(mine
            ? { background: "var(--color-accent-200)", color: "var(--color-accent-800)" }
            : { background: "var(--color-accent)", color: "#fff" }),
        }}
      >
        {mine ? initials : "CC"}
      </div>

      <div style={{ minWidth: 0 }}>
        <div
          style={{
            padding: "11px 14px",
            fontSize: 14,
            lineHeight: 1.55,
            whiteSpace: "pre-wrap",
            ...(mine
              ? { background: "var(--color-accent)", color: "#fff" }
              : {
                  background: "var(--color-surface)",
                  border: "1px solid var(--color-divider)",
                }),
          }}
        >
          {entry.text}
        </div>
        {entry.detail && <Apparatus detail={entry.detail} />}
      </div>
    </div>
  );
}

export default function ChatPage() {
  const { getToken, state, email } = useAuth();
  const [params, setParams] = useSearchParams();

  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** The answer as it arrives, before the verifier has scored it. */
  const [partial, setPartial] = useState("");
  /** What is being looked up right now, for the line above the answer. */
  const [step, setStep] = useState<string[]>([]);

  const bottom = useRef<HTMLDivElement>(null);
  const nextId = useRef(0);

  const name = (state.status === "ready" ? state.patient.full_name : null) ?? email ?? "";
  const initials = initialsOf(name);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries, pending]);

  const send = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || pending) return;

      // Captured before the question is appended: the API wants the turns that
      // came before it, not including it.
      const history: Turn[] = entries.map((entry) => ({
        role: entry.role,
        content: entry.text,
      }));

      setEntries((current) => [
        ...current,
        { id: nextId.current++, role: "user", text: trimmed },
      ]);
      setDraft("");
      setError(null);
      setPending(true);
      setPartial("");
      setStep([]);

      try {
        const answer = await askStreaming(await getToken(), trimmed, history, {
          onStep: setStep,
          onToken: (text) => setPartial((current) => current + text),
          // The verifier sent that draft back. Throwing away what was shown is
          // the point of the event — keeping it would run two answers together.
          onRestart: () => setPartial(""),
        });
        setEntries((current) => [
          ...current,
          { id: nextId.current++, role: "assistant", text: answer.answer, detail: answer },
        ]);
      } catch (problem) {
        setError(describeError(problem));
      } finally {
        setPending(false);
        setPartial("");
        setStep([]);
      }
    },
    [entries, getToken, pending],
  );

  // Arriving from a finding's 'Ask CrossCheck about this' link. The parameter is
  // cleared first so a refresh does not silently ask it a second time.
  useEffect(() => {
    const question = params.get("q");
    if (!question) return;
    setParams({}, { replace: true });
    void send(question);
    // send changes on every message; running this once per ?q= is the intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  return (
    <div
      className="cc-rise"
      style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 150px)" }}
    >
      <div style={{ flex: "none", marginBottom: 14 }}>
        <h6 style={{ color: "var(--color-accent)", marginBottom: 4 }}>
          Reasoning across every document
        </h6>
        <h2 style={{ margin: 0, fontSize: 30 }}>Ask CrossCheck</h2>
      </div>

      <div
        className="cc-scroll"
        style={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          display: "flex",
          flexDirection: "column",
          gap: 16,
          padding: "4px 4px 8px",
        }}
      >
        {entries.length === 0 && !pending && (
          <p style={{ color: MUTED, maxWidth: "62ch", fontSize: 14 }}>
            Questions are answered by looking things up in your own records — your medicines, lab
            results, allergies and the text of every document. Each answer shows what it was built
            from and how well those details were read.
          </p>
        )}

        {entries.map((entry) => (
          <Bubble key={entry.id} entry={entry} initials={initials} />
        ))}

        {pending && (
          <div style={{ display: "flex", gap: 11, maxWidth: "84%" }}>
            <div
              aria-hidden
              style={{
                width: 30,
                height: 30,
                flex: "none",
                display: "grid",
                placeItems: "center",
                fontSize: 12,
                fontFamily: "var(--font-heading)",
                fontWeight: 600,
                background: "var(--color-accent)",
                color: "#fff",
              }}
            >
              CC
            </div>

            <div style={{ minWidth: 0 }}>
              {/* Names what is being looked up, not just that something is.
                  'Checking the interaction database' is the difference between
                  a wait that reads as work and one that reads as a hang. */}
              <div
                style={{
                  fontSize: 12.5,
                  color: FAINT,
                  marginBottom: partial ? 6 : 0,
                  display: "flex",
                  alignItems: "center",
                  gap: 7,
                }}
              >
                <span
                  aria-hidden
                  style={{
                    width: 7,
                    height: 7,
                    background: "var(--color-accent)",
                    animation: "cc-pulse 1.1s ease-in-out infinite",
                  }}
                />
                {step.length > 0
                  ? `Checking ${[...new Set(step.map(toolLabel))].join(", ")}…`
                  : "Looking through your records…"}
              </div>

              {partial && (
                <div
                  style={{
                    padding: "11px 14px",
                    fontSize: 14,
                    lineHeight: 1.55,
                    whiteSpace: "pre-wrap",
                    background: "var(--color-surface)",
                    border: "1px solid var(--color-divider)",
                  }}
                >
                  {partial}
                </div>
              )}
            </div>
          </div>
        )}

        {error && (
          <div
            style={{
              fontSize: 13.5,
              padding: "10px 12px",
              background: "var(--color-danger-bg)",
              color: "var(--color-danger-text)",
              borderLeft: "2px solid var(--color-danger)",
            }}
          >
            {error}
          </div>
        )}

        <div ref={bottom} />
      </div>

      <div style={{ flex: "none", paddingTop: 12 }}>
        {entries.length === 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            {STARTERS.map((question) => (
              <button
                key={question}
                type="button"
                className="btn btn-secondary"
                style={{
                  fontSize: 13,
                  padding: "7px 12px",
                  fontFamily: "var(--font-body)",
                  fontWeight: 400,
                }}
                onClick={() => void send(question)}
              >
                {question}
              </button>
            ))}
          </div>
        )}

        <form
          style={{ display: "flex", gap: 8, alignItems: "flex-end" }}
          onSubmit={(event) => {
            event.preventDefault();
            void send(draft);
          }}
        >
          <input
            className="input"
            style={{ minHeight: 44 }}
            placeholder="Ask about your medicines, lab results or history…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={pending}
          />
          <button
            type="submit"
            className="btn btn-primary"
            style={{ height: 44, paddingInline: 18 }}
            disabled={pending || !draft.trim()}
          >
            Send
          </button>
        </form>

        <p style={{ fontSize: 11.5, color: FAINT, margin: "8px 2px 0" }}>
          CrossCheck reasons over your uploaded documents. It can be wrong, and it does not replace
          advice from a doctor or pharmacist.
        </p>
      </div>
    </div>
  );
}
