/**
 * Processing — the four steps, while the pipeline runs.
 *
 * Copied from the prototype: the `min(520px,100%)` card at `34px 30px`, the
 * 26px spinning ring, the 5px progress bar over `rgba(29,31,32,.1)`, and the
 * 26px step marks that go from outlined numbers to a filled tick.
 *
 * It follows the real thing. `GET /documents` is polled and the furthest-along
 * document decides which step is lit, so the screen reports what the backend is
 * doing rather than counting seconds.
 *
 * One honest limit. The pipeline reports four statuses — parsing, transcribing,
 * extracting, ready — but does five things: building the timeline and running
 * the cross-checks both happen inside `extracting` and are not separately
 * observable. So steps 3 and 4 light together. Making them independent needs
 * two more status values and a migration; pretending they are already
 * independent would make this screen lie.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Corners } from "../components/chrome";
import { useAuth } from "../lib/useAuth";
import { isSettled, listDocuments } from "../lib/documents";
import type { DocStatus, DocumentRead } from "../lib/documents";
import { describeError } from "../lib/errors";

const STEPS = [
  // Not "Reading documents (OCR)", which is what the prototype says. The
  // pipeline rasterises each page and sends it to a vision model — which is the
  // reason it can read handwriting at all, and the opposite of what OCR does.
  "Reading documents",
  "Extracting medications, doses & lab values",
  "Merging visits into one timeline",
  "Cross-checking interactions, duplicates & trends",
];

/** How many steps are complete once a document reaches each status. */
const STEPS_DONE: Record<DocStatus, number> = {
  uploaded: 0,
  parsing: 0,
  transcribing: 0,
  extracting: 1,
  ready: STEPS.length,
  // A failure stops the display advancing; the message below explains it.
  failed: 1,
};

const POLL_MS = 2000;

export default function ProcessingPage() {
  const navigate = useNavigate();
  const { getToken } = useAuth();
  const [documents, setDocuments] = useState<DocumentRead[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await listDocuments(await getToken()));
      setError(null);
    } catch (problem) {
      setError(describeError(problem));
    }
  }, [getToken]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const working = documents?.filter((doc) => !isSettled(doc)) ?? [];
  const failed = documents?.filter((doc) => doc.status === "failed") ?? [];
  const ready = documents?.filter((doc) => doc.status === "ready") ?? [];
  const finished = documents !== null && working.length === 0;

  // A document that is 'ready' is already on the timeline and already
  // answerable. Holding someone here until the slowest of five finishes hides
  // work that is done — five uploads is five times the reading, and the wait
  // is real, but it is not a wait for *nothing*.
  const usable = ready.length > 0 && working.length > 0;

  // The furthest-along document sets the step, so the display moves forward as
  // soon as any document does rather than waiting for the slowest.
  const step = documents?.length
    ? Math.max(...documents.map((doc) => STEPS_DONE[doc.status]))
    : 0;

  useEffect(() => {
    if (!finished) return;
    const done = setTimeout(() => navigate("/app/timeline", { replace: true }), 700);
    return () => clearTimeout(done);
  }, [finished, navigate]);

  const percent = Math.round((Math.min(step, STEPS.length) / STEPS.length) * 100);

  // Says how many are done out of how many, not just how many are left. On a
  // five-file upload 'reading 5 documents' looks identical for two minutes;
  // '3 of 5 read' moves, and moving is what tells you nothing is stuck.
  const total = documents?.length ?? 0;
  const subtitle = error
    ? error
    : documents === null
      ? "Checking on your documents…"
      : working.length > 0
        ? `${ready.length} of ${total} read · ${working.length} still going`
        : failed.length > 0
          ? `${failed.length} document${failed.length === 1 ? "" : "s"} could not be read.`
          : "Done — opening your timeline.";

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        minHeight: 0,
      }}
    >
      <div
        className="card blueprint"
        style={{ width: "min(520px, 100%)", padding: "34px 30px", gap: 0 }}
        // Announced as one region so a screen reader hears the step change
        // rather than each fragment of it.
        role="status"
        aria-live="polite"
      >
        <Corners />

        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 22 }}>
          <svg
            width="26"
            height="26"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{
              animation: finished ? undefined : "cc-spin 1.1s linear infinite",
              flex: "none",
            }}
            aria-hidden
          >
            {finished ? <path d="M20 6 9 17l-5-5" /> : <path d="M21 12a9 9 0 1 1-6.219-8.56" />}
          </svg>
          <div>
            <div style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 20 }}>
              {finished ? "Records analyzed" : "Analyzing your records"}
            </div>
            <div
              style={{
                fontSize: 13,
                color: error ? "var(--color-danger-text)" : "rgba(29,31,32,.55)",
              }}
            >
              {subtitle}
            </div>
          </div>
        </div>

        <div style={{ height: 5, background: "rgba(29,31,32,.1)", marginBottom: 24 }}>
          <div
            style={{
              height: "100%",
              background: "var(--color-accent)",
              transition: "width .5s ease",
              width: `${percent}%`,
            }}
          />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {STEPS.map((label, index) => {
            const done = index < step;
            const active = index === step && !finished;
            return (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 14 }}>
                <div
                  style={{
                    width: 26,
                    height: 26,
                    flex: "none",
                    display: "grid",
                    placeItems: "center",
                    fontSize: 12,
                    fontFamily: "var(--font-heading)",
                    fontWeight: 600,
                    background: done ? "#5980a6" : active ? "#eef6ff" : "transparent",
                    color: done ? "#fff" : active ? "#2c455d" : "rgba(29,31,32,.4)",
                    border: `1px solid ${done || active ? "#5980a6" : "rgba(29,31,32,.2)"}`,
                  }}
                >
                  {done ? "✓" : index + 1}
                </div>
                <div
                  style={{
                    fontSize: 14.5,
                    opacity: done || active ? 1 : 0.5,
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  {label}
                </div>
              </div>
            );
          })}
        </div>

        {/* Reading can take a while on a long scan, and a card with no way out
            of it is a trap. */}
        {!finished && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 24 }}>
            {/* Offered only once something is actually there to look at.
                Reading continues either way — it runs on the server, not on
                this screen, so leaving costs nothing and the rest arrives on
                the timeline as it finishes. */}
            {usable && (
              <button
                type="button"
                className="btn btn-primary btn-block"
                onClick={() => navigate("/app/timeline")}
              >
                View timeline · {ready.length} ready
              </button>
            )}
            <button
              type="button"
              className="btn btn-secondary btn-block"
              onClick={() => navigate("/app/documents")}
            >
              Back to documents
            </button>
            {usable && (
              <p style={{ margin: 0, fontSize: 12, color: "rgba(29,31,32,.5)", textAlign: "center" }}>
                The remaining {working.length} will keep processing without this page open.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
