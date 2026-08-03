/**
 * Asking questions about the record.
 *
 * Mirrors `AskResponse` in app/schemas/qa.py. Two fields on it are unusual
 * enough to state plainly, because the screen has to respect both:
 *
 *   `confidence` is nullable, and null is not a failure. An answer that
 *   correctly reports finding nothing rests on no extracted fact, so it has no
 *   score. Rendering that as 0% would paint a red bar next to a good answer;
 *   rendering it as 100% would claim certainty about an absence.
 *
 *   `grounded` false means the verifier could not trace every claim back to a
 *   tool result. The answer is still shown — it is usually mostly right — but
 *   it must not look the same as one that checked out.
 *
 * History is client-owned until the qa_session tables exist. It is replayed so
 * follow-ups resolve, never treated as evidence: a fabricated turn cannot put a
 * claim into a new answer, because every fact still has to come back from a
 * tool on the current turn.
 */

import { ApiError, BASE_URL, NetworkError, apiFetch } from "./api";

export type Band = "high" | "medium" | "low" | "none";

/** One extracted fact an answer stands on. */
export interface Citation {
  tool: string;
  event_id: string | null;
  document_id: string | null;
  page_no: number | null;
  date: string | null;
  /** How the page was read: digital, scanned, handwritten. */
  modality: string | null;
  confidence: number | null;
  text: string | null;
}

export interface Answer {
  answer: string;
  /** The weakest supporting fact, 0–1. Null when the answer rests on none. */
  confidence: number | null;
  band: Band;
  /** Why the score is what it is, in words. */
  basis: string | null;
  citations: Citation[];
  grounded: boolean;
  /** Claims the verifier could not trace to evidence. */
  unsupported: string[] | null;
  /** What was consulted, in order — the trace view. */
  tool_calls: string[];
  disclaimer: string;
}

export interface Turn {
  role: "user" | "assistant";
  content: string;
}

/** The API caps this at 20; trimming here keeps the request from being rejected. */
export const MAX_HISTORY = 20;

export async function ask(
  token: string | null,
  question: string,
  history: Turn[],
): Promise<Answer> {
  return apiFetch<Answer>("/qa/ask", {
    token,
    method: "POST",
    json: { question, history: history.slice(-MAX_HISTORY) },
  });
}

/** What the stream reports while the answer is still being worked out. */
export interface StreamWatcher {
  /** A lookup began. Names are tool names — pass through `toolLabel`. */
  onStep?(tools: string[]): void;
  /** A fragment of the answer. Append it. */
  onToken?(text: string): void;
  /**
   * The verifier rejected that draft and the agent went back for more.
   * Everything shown so far must be discarded, or the reader ends up with two
   * answers run together.
   */
  onRestart?(): void;
}

/**
 * The same answer as `ask`, delivered as it is produced.
 *
 * Written with `fetch` rather than `EventSource` for one hard reason: the
 * session token belongs in an Authorization header, and `EventSource` cannot
 * set headers or use POST.
 *
 * Resolves with the finished answer, so a caller that passes no watcher
 * behaves exactly like `ask` — just slower to return and with the same result.
 */
export async function askStreaming(
  token: string | null,
  question: string,
  history: Turn[],
  watcher: StreamWatcher = {},
): Promise<Answer> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/qa/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ question, history: history.slice(-MAX_HISTORY) }),
    });
  } catch {
    throw new NetworkError();
  }

  if (!response.ok || !response.body) {
    // Failures before the stream opens are ordinary HTTP errors — 401, 422.
    // Once it is open the status is already 200, so anything that goes wrong
    // after that arrives as an `error` event instead.
    let detail = response.statusText || `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* not JSON; the status line will do */
    }
    throw new ApiError(response.status, detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer: Answer | null = null;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Events are separated by a blank line, and a chunk can split one in
      // half — so only whole events are taken and the remainder is kept.
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        const line = part.split("\n").find((row) => row.startsWith("data: "));
        if (!line) continue;

        const event = JSON.parse(line.slice(6));
        switch (event.type) {
          case "step":
            watcher.onStep?.(event.tools ?? []);
            break;
          case "token":
            watcher.onToken?.(event.text ?? "");
            break;
          case "restart":
            watcher.onRestart?.();
            break;
          case "error":
            throw new Error(event.detail ?? "Could not answer that just now.");
          case "done":
            answer = event as Answer;
            break;
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }

  if (!answer) {
    // The connection closed before the final event — a dropped stream rather
    // than a refusal. Saying so beats leaving a half-written answer on screen.
    throw new Error("The answer was cut off. Please ask again.");
  }
  return answer;
}

// --- presentation ------------------------------------------------------------

/**
 * How a confidence band reads.
 *
 * 'none' is deliberately grey and says "not scored" rather than showing a
 * number — see the note at the top of this file.
 */
export const BAND: Record<Band, { label: string; bg: string; text: string }> = {
  high: { label: "High confidence", bg: "#e9f0eb", text: "#3c5f48" },
  medium: { label: "Medium confidence", bg: "var(--color-warning-bg)", text: "var(--color-warning-text)" },
  low: { label: "Low confidence", bg: "var(--color-danger-bg)", text: "var(--color-danger-text)" },
  none: { label: "Not scored", bg: "var(--color-neutral-100)", text: "var(--color-neutral-800)" },
};

/** What each tool actually looked at, for the trace line under an answer. */
export const TOOL_LABELS: Record<string, string> = {
  list_documents: "your documents",
  get_timeline: "your timeline",
  find_medications: "your medicines",
  get_lab_series: "your lab results",
  get_allergies: "your allergies",
  get_findings: "the safety checks",
  check_drug: "the interaction database",
  read_document: "a document",
  search_text: "the text of your documents",
  get_uncertain_items: "what needs review",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/_/g, " ");
}

/** The questions offered before the first one is asked. */
export const STARTERS = [
  "What medicines am I taking right now?",
  "Is it safe for me to take ibuprofen?",
  "What advice did the doctor give me?",
  "How have my lab results changed?",
];
