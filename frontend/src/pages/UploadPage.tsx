/**
 * Documents — what has been uploaded, and how to add more.
 *
 * The prototype's upload screen, moved inside the app shell as the Documents
 * tab. Everything below the header is the prototype's: the dashed blueprint
 * drop zone at `34px 24px` with its `rgba(89,128,166,.55)` border, the file
 * rows at `11px 14px`, the 46px button and the footnote.
 *
 * The header is not. The prototype's version is a landing page — its own logo,
 * its own wordmark, a 40px hero line — and all three are wrong here: the sidebar
 * carries the logo 60px to the left, and a hero headline is not what a tab says.
 * So it uses the same eyebrow / title / description as Timeline.
 *
 * The list is real. A row arrives with nothing but a filename and a size, and
 * gains its provider, date, type and page count as the pipeline reads it — so
 * the screen shows that filling in rather than waiting for it. Watching a file
 * turn into "City Medical Lab · Jan 12, 2025 · Lab Report · 3 pp" is the app
 * demonstrating the thing it is for.
 */

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { Corners } from "../components/chrome";
import { useAuth } from "../lib/useAuth";
import { ACCEPT_ATTRIBUTE, MAX_UPLOAD_MB, acceptFiles, formatSize } from "../lib/files";
import type { PickedFile } from "../lib/files";
import {
  docTypeLabel,
  formatDocDate,
  isSettled,
  listDocuments,
  openDocument,
  statusLabel,
} from "../lib/documents";
import type { DocumentRead } from "../lib/documents";
import { useUpload } from "../lib/useUpload";
import { describeError } from "../lib/errors";

const MUTED = "rgba(29,31,32,.55)";

/** How often to re-read the list while anything is still being processed. */
const POLL_MS = 2500;

function DocumentIcon({ tone = "var(--color-accent)" }: { tone?: string }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke={tone}
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flex: "none" }}
      aria-hidden
    >
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v4a2 2 0 0 0 2 2h4" />
    </svg>
  );
}

/** One line in the list. Shared by every kind of row, so a sample, an upload in
 *  flight and a finished document all read as the same kind of thing. */
function FileRow({
  title,
  subtitle,
  tag,
  trailing,
  tone = "normal",
  busy,
  onRemove,
  onOpen,
}: {
  title: string;
  subtitle: string;
  tag?: string;
  trailing?: string;
  tone?: "normal" | "danger" | "pending";
  busy?: boolean;
  onRemove?: () => void;
  /** Set on stored documents: the row opens the original. */
  onOpen?: () => void;
}) {
  const danger = tone === "danger";
  return (
    <div
      // A row that does something has to say so before it is clicked. Given a
      // button role and a key handler rather than wrapped in a <button>, which
      // cannot legally contain the remove button already inside it.
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      onClick={onOpen}
      onKeyDown={
        onOpen &&
        ((event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onOpen();
          }
        })
      }
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "11px 14px",
        border: `1px solid ${danger ? "var(--color-danger)" : "var(--color-divider)"}`,
        background: danger ? "var(--color-danger-bg)" : "var(--color-surface)",
        // Not yet read: dimmed, so a finished document is visibly the settled
        // state rather than just a longer line of text.
        opacity: tone === "pending" ? 0.72 : 1,
        cursor: onOpen ? "pointer" : undefined,
      }}
    >
      {busy ? (
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="1.6"
          strokeLinecap="round"
          style={{ flex: "none", animation: "cc-spin 1.1s linear infinite" }}
          aria-hidden
        >
          <path d="M21 12a9 9 0 1 1-6.219-8.56" />
        </svg>
      ) : (
        <DocumentIcon tone={danger ? "var(--color-danger)" : "var(--color-accent)"} />
      )}

      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 14,
            fontWeight: 500,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
          title={title}
        >
          {title}
        </div>
        <div style={{ fontSize: 12, color: danger ? "var(--color-danger-text)" : MUTED }}>
          {subtitle}
        </div>
      </div>

      {tag && <span className="tag tag-neutral">{tag}</span>}
      {trailing && (
        <span style={{ fontSize: 12, color: "rgba(29,31,32,.4)", width: 52, textAlign: "right" }}>
          {trailing}
        </span>
      )}
      {onRemove && (
        <button
          type="button"
          className="btn-link"
          onClick={onRemove}
          aria-label={`Remove ${title}`}
          style={{ display: "grid", placeItems: "center", flex: "none" }}
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M18 6 6 18" />
            <path d="m6 6 12 12" />
          </svg>
        </button>
      )}
    </div>
  );
}

/**
 * The original, as it was uploaded.
 *
 * Portalled to the body. `.cc-rise` on the page animates `transform`, and a
 * transformed ancestor becomes the containing block for `position: fixed`
 * descendants — so a fixed overlay rendered in place lands inside the column
 * rather than over the screen. Learned once, on the chat history dialog.
 */
function Viewer({ document: doc, onClose }: { document: DocumentRead; onClose: () => void }) {
  const { getToken } = useAuth();
  const [src, setSrc] = useState<string | null>(null);
  const [gone, setGone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let release = () => {};

    void (async () => {
      try {
        const opened = await openDocument(await getToken(), doc.id);
        if (cancelled) {
          // Resolved after the dialog closed. Release it here or the object URL
          // holds the file in memory for the life of the tab.
          opened.release();
          return;
        }
        release = opened.release;
        setSrc(opened.src);
        setGone(!opened.file.available);
      } catch (problem) {
        if (!cancelled) setError(describeError(problem));
      }
    })();

    return () => {
      cancelled = true;
      release();
    };
  }, [doc.id, getToken]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isPdf = doc.mime_type === "application/pdf";

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={doc.filename ?? "Document"}
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20,21,22,.62)",
        display: "grid",
        placeItems: "center",
        padding: 24,
        zIndex: 100,
      }}
    >
      <div
        className="card blueprint"
        onClick={(event) => event.stopPropagation()}
        style={{
          width: "min(920px, 100%)",
          maxHeight: "calc(100vh - 48px)",
          display: "flex",
          flexDirection: "column",
          padding: 0,
          gap: 0,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "14px 18px",
            borderBottom: "1px solid var(--color-divider)",
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontFamily: "var(--font-heading)",
                fontSize: 16,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {doc.filename || "Untitled document"}
            </div>
            <div style={{ fontSize: 12, color: MUTED }}>
              {[docTypeLabel(doc.doc_type), formatDocDate(doc.doc_date), doc.provider]
                .filter(Boolean)
                .join(" · ")}
            </div>
          </div>
          {src && (
            <a href={src} target="_blank" rel="noopener noreferrer" className="btn-link">
              Open in new tab ↗
            </a>
          )}
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>

        <div
          style={{
            flex: 1,
            overflow: "auto",
            background: "var(--color-surface)",
            display: "flex",
            flexDirection: "column",
            minHeight: 220,
          }}
        >
          <div
            style={{
              flex: 1,
              display: "grid",
              placeItems: "center",
              minHeight: 200,
            }}
          >
          {error && <p style={{ padding: 28, color: "var(--color-danger-text)" }}>{error}</p>}

          {/* A row whose file did not survive an early redeploy. Said plainly:
              the reader should know the page is gone, not think we broke. */}
          {gone && !error && (
            <div style={{ padding: "36px 28px", textAlign: "center", maxWidth: 460 }}>
              <p style={{ fontFamily: "var(--font-heading)", fontSize: 17, margin: "0 0 8px" }}>
                The original is no longer stored
              </p>
              <p style={{ fontSize: 13.5, color: MUTED, margin: 0 }}>
                This document was uploaded before files were kept in permanent storage, so the
                scan itself is gone. Everything read out of it is still on your timeline.
              </p>
            </div>
          )}

          {!src && !gone && !error && (
            <p style={{ padding: 40, color: MUTED, fontSize: 13.5 }}>Loading…</p>
          )}

          {src &&
            (isPdf ? (
              // Browsers render PDFs natively. A viewer library would be a
              // megabyte of JavaScript to do what an iframe already does.
              <iframe
                src={src}
                title={doc.filename ?? "Document"}
                style={{ width: "100%", height: "calc(100vh - 190px)", border: 0 }}
              />
            ) : (
              <img
                src={src}
                alt={doc.filename ?? "Uploaded document"}
                style={{ maxWidth: "100%", display: "block" }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>,
    window.document.body,
  );
}

/** A stored document, however far through the pipeline it is. */
function DocumentRow({ document, onOpen }: { document: DocumentRead; onOpen: () => void }) {
  const failed = document.status === "failed";
  const settled = isSettled(document);
  const state = statusLabel(document.status);

  // Falls back through what is actually known: the summary once extracted, the
  // filename before that. Never an empty line.
  const title = document.summary?.trim() || document.filename || "Untitled document";

  const details = [document.provider, formatDocDate(document.doc_date)].filter(Boolean).join(" · ");

  const subtitle = failed
    ? document.error_message || "Could not be read"
    : details || state || formatSize(document.size_bytes ?? 0);

  return (
    <FileRow
      title={title}
      subtitle={subtitle}
      tag={settled && !failed ? docTypeLabel(document.doc_type) : (state ?? undefined)}
      trailing={document.page_count ? `${document.page_count} pp` : undefined}
      tone={failed ? "danger" : settled ? "normal" : "pending"}
      busy={!settled}
      // Only once it has been read. A row still being processed has a file, but
      // opening it mid-pipeline invites the reader to compare a scan against a
      // timeline that is not finished being written.
      onOpen={settled ? onOpen : undefined}
    />
  );
}

export default function UploadPage() {
  const { getToken } = useAuth();
  const navigate = useNavigate();

  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [documents, setDocuments] = useState<DocumentRead[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  /** The document whose original is open, if any. */
  const [viewing, setViewing] = useState<DocumentRead | null>(null);

  const { outcomes, busy, start, reset } = useUpload();

  const refresh = useCallback(async () => {
    try {
      setDocuments(await listDocuments(await getToken()));
      setListError(null);
    } catch (problem) {
      setListError(describeError(problem));
    }
  }, [getToken]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll only while something is still moving. A list of finished documents
  // does not change on its own, and polling it forever would be a request every
  // few seconds for an answer that is already known.
  const working = documents?.some((doc) => !isSettled(doc)) ?? false;
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [working, refresh]);

  const usable = picked.filter((row) => row.problem === null);
  const hasDocuments = (documents?.length ?? 0) > 0;
  // `documents !== null` matters: before the first response there is nothing to
  // be empty about, and saying "no documents yet" then would be a guess.
  const isEmpty = documents !== null && !hasDocuments && picked.length === 0 && !listError;

  function add(files: FileList | File[]) {
    setPicked((current) => [...current, ...acceptFiles(files, current)]);
  }

  function onDrop(event: React.DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length > 0) add(event.dataTransfer.files);
  }

  async function analyse() {
    const created = await start(usable);
    await refresh();

    // Anything at all got through: the processing screen has something real to
    // follow. Nothing did: stay here, where each row says why it failed.
    if (created.length > 0) {
      setPicked([]);
      reset();
      navigate("/processing");
    }
  }

  return (
    <div className="cc-rise">
      {viewing && <Viewer document={viewing} onClose={() => setViewing(null)} />}

      <div style={{ maxWidth: 760 }}>
        <h6 style={{ color: "var(--color-accent)", marginBottom: 4 }}>Sources</h6>
        <h2 style={{ margin: "0 0 6px", fontSize: 30 }}>Documents</h2>
        <p style={{ color: "rgba(29,31,32,.65)", maxWidth: "64ch", marginBottom: 26 }}>
          Upload lab reports, prescriptions, discharge summaries and doctor's notes from any visit
          or provider. CrossCheck reads them, merges them into a single patient timeline, and flags
          drug interactions, duplicates, conflicting doses and lab trends.
        </p>

        {/* The panel is a drop target and nothing more.

            It used to open the picker on click too, and that is what was
            broken: the only way to do it is `input.click()` from inside a
            click handler, and every attempt to make that reliable — moving the
            input out, stopping propagation on the button — failed, while
            dropping a file kept working because it never touches the input.
            So the script is gone. The label below activates the input the way
            the browser intends, and there is no second path left to fail. */}
        <div
          className="card blueprint"
          onDragOver={(e) => {
            // Without this the browser navigates to the dropped file instead.
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          style={{
            padding: "34px 24px",
            alignItems: "center",
            textAlign: "center",
            gap: 8,
            borderStyle: "dashed",
            borderColor: dragging ? "var(--color-accent)" : "rgba(89,128,166,.55)",
            background: dragging ? "rgba(89,128,166,.06)" : undefined,
          }}
        >
          <Corners />
          <svg
            width="34"
            height="34"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" x2="12" y1="3" y2="15" />
          </svg>
          <div
            style={{
              fontFamily: "var(--font-heading)",
              fontWeight: 600,
              fontSize: 19,
              marginTop: 4,
            }}
          >
            {dragging ? "Release to add" : "Drop medical documents here"}
          </div>
          <div style={{ fontSize: 13, color: MUTED }}>
            PDF, JPG or PNG · lab reports · prescriptions · discharge summaries · notes
          </div>
          {/* The input lives inside its label. Wrapping rather than pairing by
              id makes the association structural — there is no name to get
              wrong and no ref to be null — and clicking the label is what opens
              the picker, natively.

              Hidden by size, not by `display: none`, so it stays focusable:
              tabbing to it and pressing Enter opens the picker as well. */}
          <label className="btn btn-secondary" style={{ marginTop: 12, cursor: "pointer" }}>
            Browse files
            <input
              type="file"
              multiple
              accept={ACCEPT_ATTRIBUTE}
              style={{ position: "absolute", width: 1, height: 1, opacity: 0 }}
              onChange={(e) => {
                // Copied into an array *before* the reset below, and this
                // ordering is the whole point. `e.target.files` is a live view
                // of the element's current selection, and clearing `value`
                // empties it. `add` hands the list to a state updater, which
                // React runs after this handler returns — so passing the live
                // list meant the updater read an already-emptied FileList and
                // silently added nothing. The picker opened, a file was
                // chosen, and the screen never changed.
                const chosen = Array.from(e.target.files ?? []);
                // Reset, or picking the same file twice in a row fires nothing.
                e.target.value = "";
                if (chosen.length > 0) add(chosen);
              }}
            />
          </label>
        </div>

        {/* --- files waiting to be uploaded ------------------------------- */}
        {picked.length > 0 && (
          <>
            <div style={headerRow}>
              <h6 style={{ margin: 0 }}>
                Ready to upload · {picked.length} {picked.length === 1 ? "file" : "files"}
              </h6>
              <button
                type="button"
                className="btn-link"
                style={{ fontSize: 12 }}
                onClick={() => {
                  setPicked([]);
                  reset();
                }}
                disabled={busy}
              >
                Clear
              </button>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {picked.map((row) => {
                const outcome = outcomes[row.id];
                const failed = row.problem !== null || outcome?.state === "failed";
                const subtitle =
                  row.problem ??
                  (outcome?.state === "failed"
                    ? outcome.error
                    : outcome?.state === "done"
                      ? outcome.duplicate
                        ? "Already uploaded — using the copy on file"
                        : "Uploaded"
                      : outcome?.state === "uploading"
                        ? "Uploading…"
                        : formatSize(row.file.size));

                return (
                  <FileRow
                    key={row.id}
                    title={row.file.name}
                    subtitle={subtitle}
                    tone={failed ? "danger" : "normal"}
                    busy={outcome?.state === "uploading"}
                    onRemove={
                      busy
                        ? undefined
                        : () => setPicked((c) => c.filter((r) => r.id !== row.id))
                    }
                  />
                );
              })}
            </div>

            <button
              className="btn btn-primary btn-block"
              style={{ marginTop: 22, height: 46, fontSize: 16 }}
              disabled={usable.length === 0 || busy}
              onClick={() => void analyse()}
            >
              {busy
                ? "Uploading…"
                : usable.length === 0
                  ? `Add a file under ${MAX_UPLOAD_MB} MB to continue`
                  : `Analyze ${usable.length} document${usable.length === 1 ? "" : "s"} →`}
            </button>
          </>
        )}

        {/* --- what is already stored ------------------------------------- */}
        {listError && (
          <div style={{ marginTop: 26 }}>
            <div style={headerRow}>
              <h6 style={{ margin: 0 }}>Your documents</h6>
              <button
                type="button"
                className="btn-link"
                style={{ fontSize: 12 }}
                onClick={() => void refresh()}
              >
                Try again
              </button>
            </div>
            <div
              style={{
                padding: "11px 14px",
                border: "1px solid var(--color-danger)",
                background: "var(--color-danger-bg)",
                color: "var(--color-danger-text)",
                fontSize: 13,
              }}
            >
              {listError}
            </div>
          </div>
        )}

        {hasDocuments && (
          <>
            <div style={headerRow}>
              <h6 style={{ margin: 0 }}>
                Your documents · {documents!.length}{" "}
                {documents!.length === 1 ? "file" : "files"}
              </h6>
              <span style={{ fontSize: 12, color: "rgba(29,31,32,.5)" }}>
                {working ? "Reading…" : "All read"}
              </span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {documents!.map((doc) => (
                <DocumentRow key={doc.id} document={doc} onOpen={() => setViewing(doc)} />
              ))}
            </div>
          </>
        )}

        {/* Nothing stored and nothing waiting: say so plainly. Filling this
            space with an example would only leave the user unsure which rows
            were theirs. */}
        {isEmpty && (
          <div style={{ ...headerRow, justifyContent: "flex-start" }}>
            <span style={{ fontSize: 13, color: MUTED }}>
              No documents yet. Drop a file above to get started.
            </span>
          </div>
        )}

        <p
          style={{
            textAlign: "center",
            fontSize: 12,
            color: "rgba(29,31,32,.45)",
            marginTop: 22,
          }}
        >
          Documents are processed for reconciliation only and are never used to make a diagnosis.
        </p>
      </div>
    </div>
  );
}

const headerRow: React.CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  margin: "26px 0 10px",
  gap: 12,
  flexWrap: "wrap",
};
