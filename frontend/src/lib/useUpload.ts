/**
 * Uploading a set of files.
 *
 * `POST /documents` takes one file, so a five-file drop is five requests. They
 * go a few at a time rather than all at once: a browser will happily open six
 * parallel connections and leave a slow one starved behind the others, and the
 * server is doing real work per file.
 *
 * Failures are per file. One rejected upload must not discard the four that
 * worked, so each row carries its own outcome and the caller decides what to do
 * with a partial success.
 */

import { useCallback, useState } from "react";
import { useAuth } from "./useAuth";
import { uploadDocument } from "./documents";
import type { DocumentRead } from "./documents";
import { describeError } from "./errors";
import type { PickedFile } from "./files";

/** Enough to keep the connection busy, few enough to stay fair. */
const CONCURRENCY = 3;

export type UploadOutcome =
  | { state: "pending" }
  | { state: "uploading" }
  | { state: "done"; document: DocumentRead; duplicate: boolean }
  | { state: "failed"; error: string };

export interface UploadState {
  /** Outcome per picked file, keyed by `PickedFile.id`. */
  outcomes: Record<string, UploadOutcome>;
  busy: boolean;
  /** Documents created or matched, in the order their uploads finished. */
  documents: DocumentRead[];
}

export function useUpload() {
  const { getToken } = useAuth();
  const [outcomes, setOutcomes] = useState<Record<string, UploadOutcome>>({});
  const [busy, setBusy] = useState(false);

  const set = useCallback((id: string, outcome: UploadOutcome) => {
    setOutcomes((current) => ({ ...current, [id]: outcome }));
  }, []);

  const reset = useCallback(() => setOutcomes({}), []);

  /**
   * Uploads every file and resolves with the documents that made it.
   *
   * Resolves rather than rejects on partial failure: the caller needs the
   * successes as much as it needs to know something went wrong.
   */
  const start = useCallback(
    async (files: PickedFile[]): Promise<DocumentRead[]> => {
      const token = await getToken();
      const queue = [...files];
      const created: DocumentRead[] = [];

      setBusy(true);
      setOutcomes(Object.fromEntries(files.map((f) => [f.id, { state: "pending" } as const])));

      // Each worker pulls from the shared queue, so a slow file holds up only
      // itself rather than a whole fixed slice of the work.
      async function worker() {
        for (;;) {
          const next = queue.shift();
          if (!next) return;

          set(next.id, { state: "uploading" });
          try {
            const { document, duplicate } = await uploadDocument(token, next.file);
            created.push(document);
            set(next.id, { state: "done", document, duplicate });
          } catch (problem) {
            set(next.id, { state: "failed", error: describeError(problem) });
          }
        }
      }

      await Promise.all(
        Array.from({ length: Math.min(CONCURRENCY, files.length) }, () => worker()),
      );

      setBusy(false);
      return created;
    },
    [getToken, set],
  );

  return { outcomes, busy, start, reset };
}
