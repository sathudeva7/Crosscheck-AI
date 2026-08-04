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
 *
 * Conversations are the server's. The browser holds an id, not a transcript —
 * see the note at the top of lib/qa.ts for why that distinction matters.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
import { describeError } from "../lib/errors";
import {
  BAND,
  STARTERS,
  askStreaming,
  deleteSession,
  getTranscript,
  listSessions,
  toolLabel,
} from "../lib/qa";
import type { Answer, Citation, Message, Session, WebSource } from "../lib/qa";
import { useAuth } from "../lib/useAuth";

const MUTED = "rgba(29,31,32,.6)";
const FAINT = "rgba(29,31,32,.45)";

/**
 * What renders under an answer bubble.
 *
 * Wider than `Answer` because a replayed message carries one thing a fresh one
 * cannot: `truncated`, meaning the reader left before it finished.
 */
type Detail = Omit<Answer, "answer" | "session_id"> & { truncated?: boolean };

interface Entry {
  id: string;
  role: "user" | "assistant";
  text: string;
  /** Present on answers only: the apparatus rendered beneath the bubble. */
  detail?: Detail;
}

/** A stored message, in the shape the transcript renders. */
function toEntry(message: Message): Entry {
  return {
    id: message.id,
    role: message.role,
    text: message.content,
    detail: message.role === "assistant" ? message : undefined,
  };
}

/** Ids for messages that exist only on screen — the question just typed. */
let localId = 0;
const nextLocalId = () => `local-${(localId += 1)}`;

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

/**
 * A published page an attributed sentence rests on.
 *
 * Rendered apart from the citation chips, and looking unlike them, on purpose.
 * A citation is a fact off this patient's own prescription and carries the
 * confidence that reading earned; this is a drug reference that has never seen
 * them. Both appearing as identical grey chips under one heading would be the
 * single most misleading thing this screen could do — the reader would have no
 * way to tell which sentences were about them.
 */
function WebLink({ source }: { source: WebSource }) {
  // 'drugs.com' tells a reader more about whether to trust it than the article
  // title does, and it is what they would look at in a browser bar.
  const host = (() => {
    try {
      return new URL(source.url).hostname.replace(/^www\./, "");
    } catch {
      return source.url;
    }
  })();

  return (
    <a
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      title={source.title ?? source.url}
      style={{
        display: "inline-flex",
        flexDirection: "column",
        padding: "5px 10px",
        border: "1px dashed var(--color-divider)",
        background: "transparent",
        maxWidth: 300,
        textDecoration: "none",
        color: "inherit",
      }}
    >
      <span
        style={{
          fontSize: 12.5,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {source.title || host}
      </span>
      <span style={{ fontSize: 11, color: FAINT }}>{host} ↗</span>
    </a>
  );
}

function Apparatus({ detail }: { detail: Detail }) {
  const band = BAND[detail.band] ?? BAND.none;
  const percent = detail.confidence === null ? null : Math.round(detail.confidence * 100);

  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
      {detail.truncated && (
        <div
          style={{
            fontSize: 12.5,
            padding: "7px 11px",
            background: "var(--color-warning-bg)",
            color: "var(--color-warning-text)",
            borderLeft: "2px solid var(--color-warning)",
          }}
        >
          This answer was cut off — the page was closed before it finished. Ask again for the
          whole thing.
        </div>
      )}

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

      {detail.web?.length > 0 && (
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
            General background from the web · not your records
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {detail.web.map((source) => (
              <WebLink key={source.url} source={source} />
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

/** When a conversation last had a message, in the words someone would use. */
function ago(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return days < 7 ? `${days}d ago` : new Date(iso).toLocaleDateString();
}

/** A clock, for the control that opens past conversations. */
function ClockIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flex: "none" }}
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.5 2" />
    </svg>
  );
}

/**
 * Past conversations, over the transcript rather than beside it.
 *
 * A dialog rather than a column because opening an old conversation is
 * something a reader does occasionally, and a permanent list charges the full
 * width of the page for it on every visit. Reading the answer is what the
 * screen is for.
 */
function HistoryDialog({
  sessions,
  current,
  onOpen,
  onDelete,
  onClose,
  busy,
}: {
  sessions: Session[];
  current: string | null;
  onOpen(id: string): void;
  onDelete(id: string): void;
  onClose(): void;
  busy: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Escape closes it, and the panel takes focus so a keyboard reader is
    // inside the dialog rather than still on the page behind it.
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    panel.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Rendered into <body> rather than where it sits in the tree. The page
  // container carries the cc-rise entrance animation, and an ancestor that
  // animates `transform` becomes the reference box for `position: fixed`
  // descendants — so `inset: 0` resolved to the chat column and the dialog
  // landed half off the bottom of it instead of centred in the window.
  return createPortal(
    <div
      className="cc-overlay"
      // Only a click that both starts and ends on the backdrop closes it —
      // otherwise a text selection that drifts off the panel dismisses the
      // dialog mid-drag.
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        className="cc-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Past conversations"
        tabIndex={-1}
      >
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: 12,
            padding: "14px 16px",
            borderBottom: "1px solid var(--color-divider)",
          }}
        >
          <div>
            <h6 style={{ color: "var(--color-accent)", margin: "0 0 2px" }}>History</h6>
            <strong style={{ fontFamily: "var(--font-heading)", fontSize: 17 }}>
              Past conversations
            </strong>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              fontSize: 20,
              lineHeight: 1,
              padding: 2,
              color: MUTED,
            }}
          >
            ×
          </button>
        </div>

        <div className="cc-scroll" style={{ overflowY: "auto", minHeight: 0 }}>
          {sessions.length === 0 ? (
            <p style={{ fontSize: 13.5, color: MUTED, margin: 0, padding: "22px 16px" }}>
              Nothing here yet. Conversations are kept as soon as you ask something.
            </p>
          ) : (
            sessions.map((session) => {
              const active = session.id === current;
              return (
                <div
                  key={session.id}
                  className="cc-thread"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "11px 16px",
                    borderBottom: "1px solid var(--color-divider)",
                    borderLeft: `2px solid ${active ? "var(--color-accent)" : "transparent"}`,
                    background: active ? "rgba(89,128,166,.09)" : undefined,
                  }}
                >
                  <button
                    type="button"
                    onClick={() => onOpen(session.id)}
                    disabled={busy}
                    style={{
                      flex: 1,
                      minWidth: 0,
                      textAlign: "left",
                      background: "none",
                      border: "none",
                      padding: 0,
                      cursor: busy ? "default" : "pointer",
                      font: "inherit",
                      color: "inherit",
                    }}
                  >
                    <span
                      style={{
                        display: "block",
                        fontSize: 13.5,
                        lineHeight: 1.4,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontWeight: active ? 600 : 400,
                      }}
                    >
                      {session.title}
                    </span>
                    <span style={{ fontSize: 11.5, color: FAINT }}>
                      {ago(session.updated_at)}
                      {active && " · open"}
                    </span>
                  </button>

                  <button
                    type="button"
                    className="cc-thread-x"
                    title="Delete this conversation"
                    aria-label={`Delete conversation: ${session.title}`}
                    onClick={() => onDelete(session.id)}
                    disabled={busy}
                    style={{
                      flex: "none",
                      background: "none",
                      border: "1px solid var(--color-divider)",
                      padding: "2px 7px",
                      fontSize: 14,
                      lineHeight: 1.3,
                      cursor: "pointer",
                      color: MUTED,
                    }}
                  >
                    ×
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default function ChatPage() {
  const { getToken, state, email } = useAuth();
  const [params, setParams] = useSearchParams();

  /** Which conversation is open. Null until the first question starts one. */
  const sessionId = params.get("s");

  const [sessions, setSessions] = useState<Session[]>([]);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  /** The answer as it arrives, before the verifier has scored it. */
  const [partial, setPartial] = useState("");
  /** What is being looked up right now, for the line above the answer. */
  const [step, setStep] = useState<string[]>([]);

  const bottom = useRef<HTMLDivElement>(null);
  // The conversation a reply belongs to, readable from inside `send` without
  // making it depend on the URL — a new conversation learns its id mid-stream,
  // and re-creating `send` at that moment would strand the request in flight.
  const active = useRef<string | null>(sessionId);
  active.current = sessionId;

  // `pending` again, but readable the instant it is set. The state variable is
  // captured when `send` is built, so two calls made before React re-renders
  // both see the old `false` and both go out — one double-click, two answers.
  const inFlight = useRef(false);

  const name = (state.status === "ready" ? state.patient.full_name : null) ?? email ?? "";
  const initials = initialsOf(name);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries, pending]);

  const refreshList = useCallback(async () => {
    try {
      setSessions(await listSessions(await getToken()));
    } catch {
      // The list is a convenience. Failing to load it must not take the
      // conversation itself down with it.
    }
  }, [getToken]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  // Open whatever the address bar names. Runs on load too, which is what makes
  // a refresh land back in the same conversation.
  useEffect(() => {
    let cancelled = false;

    if (!sessionId) {
      setEntries([]);
      return;
    }

    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const transcript = await getTranscript(await getToken(), sessionId);
        if (!cancelled) setEntries(transcript.messages.map(toEntry));
      } catch (problem) {
        if (cancelled) return;
        // A stale link — the conversation was deleted, or belongs to another
        // account. Drop back to a blank one rather than showing a dead screen.
        setParams({}, { replace: true });
        setEntries([]);
        setError(describeError(problem));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
    // setParams is stable in react-router 7; listing it would re-fetch on every
    // query-string change, including the one this effect makes itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, getToken]);

  const send = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || inFlight.current) return;
      inFlight.current = true;

      // Shown immediately with a local id. The server has already stored it by
      // the time the first event arrives; this is only so the reader sees what
      // they typed without waiting for a round trip.
      setEntries((current) => [
        ...current,
        { id: nextLocalId(), role: "user", text: trimmed },
      ]);
      setDraft("");
      setError(null);
      setPending(true);
      setPartial("");
      setStep([]);

      try {
        const answer = await askStreaming(await getToken(), trimmed, active.current, {
          // Arrives before any answer text. Putting the id in the address bar
          // now means a reader who navigates away mid-answer can still get
          // back to it — the answer is being saved either way.
          onSession: ({ id }) => {
            if (active.current === id) return;
            active.current = id;
            setParams({ s: id }, { replace: true });
          },
          onStep: setStep,
          onToken: (text) => setPartial((current) => current + text),
          onRestart: () => setPartial(""),
        });
        setEntries((current) => [
          ...current,
          { id: nextLocalId(), role: "assistant", text: answer.answer, detail: answer },
        ]);
        // Titles and ordering come from the server, so the list is re-read
        // rather than guessed at.
        void refreshList();
      } catch (problem) {
        setError(describeError(problem));
      } finally {
        inFlight.current = false;
        setPending(false);
        setPartial("");
        setStep([]);
      }
    },
    [getToken, refreshList, setParams],
  );

  const startNew = useCallback(() => {
    if (pending) return;
    active.current = null;
    setEntries([]);
    setError(null);
    setParams({}, { replace: false });
  }, [pending, setParams]);

  const open = useCallback(
    (id: string) => {
      if (pending || id === sessionId) return;
      setError(null);
      setParams({ s: id });
    },
    [pending, sessionId, setParams],
  );

  const remove = useCallback(
    async (id: string) => {
      if (pending) return;
      // Optimistic: the row goes at once and comes back if the delete fails.
      // Waiting on a round trip to remove something the user just dismissed is
      // the kind of lag that makes a list feel broken.
      const previous = sessions;
      setSessions((current) => current.filter((session) => session.id !== id));
      if (id === sessionId) startNew();
      try {
        await deleteSession(await getToken(), id);
      } catch (problem) {
        setSessions(previous);
        setError(describeError(problem));
      }
    },
    [getToken, pending, sessionId, sessions, startNew],
  );

  // Arriving from a finding's 'Ask CrossCheck about this' link.
  //
  // Clearing ?q= is not on its own enough to stop this asking twice. setParams
  // schedules a re-render; it does not change the `params` this pass is holding.
  // So anything that runs the body again before that render — StrictMode's
  // deliberate double-invoke in development, chiefly — reads the same question
  // and sends it again, opening a second conversation. The guard has to be the
  // question itself: once asked, never asked again, however often this runs.
  const asked = useRef<string | null>(null);
  useEffect(() => {
    const question = params.get("q");
    if (!question || asked.current === question) return;
    asked.current = question;
    setParams(sessionId ? { s: sessionId } : {}, { replace: true });
    void send(question);
    // send changes on every message; running this once per ?q= is the intent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  return (
    <div className="cc-rise cc-chat">
      {showHistory && (
        <HistoryDialog
          sessions={sessions}
          current={sessionId}
          onOpen={(id) => {
            open(id);
            setShowHistory(false);
          }}
          onDelete={(id) => void remove(id)}
          onClose={() => setShowHistory(false)}
          busy={pending}
        />
      )}

      <div
        style={{
          flex: "none",
          marginBottom: 14,
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div>
          <h6 style={{ color: "var(--color-accent)", marginBottom: 4 }}>
            Reasoning across every document
          </h6>
          <h2 style={{ margin: 0, fontSize: 30 }}>Ask CrossCheck</h2>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: 13, padding: "8px 12px", display: "flex", gap: 7 }}
            onClick={() => setShowHistory(true)}
            // The count is the reason to open it. Naming it here saves a
            // click that would only have revealed an empty list.
            title={
              sessions.length === 0
                ? "No past conversations yet"
                : `${sessions.length} past conversation${sessions.length === 1 ? "" : "s"}`
            }
          >
            <ClockIcon />
            History
            {sessions.length > 0 && (
              <span style={{ color: FAINT, fontFamily: "var(--font-body)", fontWeight: 400 }}>
                {sessions.length}
              </span>
            )}
          </button>

          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: 13, padding: "8px 12px" }}
            onClick={startNew}
            disabled={pending || (!sessionId && entries.length === 0)}
          >
            + New
          </button>
        </div>
      </div>

      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 16,
          padding: "4px 4px 8px",
        }}
      >
        {loading && (
          <p style={{ color: FAINT, fontSize: 13.5 }}>Opening that conversation…</p>
        )}

        {!loading && entries.length === 0 && !pending && (
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

        {/* The scroll target. Its margin clears the sticky composer, or
            scrolling to the newest message parks it underneath. */}
        <div ref={bottom} style={{ scrollMarginBottom: 120 }} />
      </div>

      <div className="cc-composer">
        {entries.length === 0 && !loading && (
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
