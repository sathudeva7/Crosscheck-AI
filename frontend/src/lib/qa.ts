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
 * Conversations live on the server. The client sends a `session_id` and nothing
 * else about the past — it used to send the earlier turns themselves, which
 * made the browser the authority on what had been said. Replayed history is
 * context the model reasons from, so a client able to write it could put a
 * sentence in the assistant's mouth and have it treated as established.
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

/**
 * One published page behind an attributed general-medicine sentence.
 *
 * Deliberately not a `Citation`. A citation is a fact read off the patient's own
 * document and carries the confidence that reading earned; this is drug-reference
 * publishing, which says nothing about them and has no such score. They render
 * under separate headings for the same reason they are separate types here.
 */
export interface WebSource {
  url: string;
  title: string | null;
}

/**
 * What an answer cost to produce.
 *
 * One question is three to eight model calls — a lookup decision per gathering
 * round, then composing, then verifying — and `calls` is how many it took.
 *
 * `cost_usd` is nullable for the same reason `confidence` is: null means not
 * known, never free. It comes back null when the model in use has no price on
 * file server-side, and the screen has to be able to say so rather than
 * printing $0.00 next to work that was billed.
 */
export interface Usage {
  model: string | null;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  /** Spend per graph node — 'agent', 'compose', 'verify'. Diagnostic. */
  by_node: Record<string, { calls: number; input_tokens: number; output_tokens: number; cost_usd: number | null }>;
}

export interface Answer {
  answer: string;
  /** Which conversation it landed in — the id of a new one, if none was named. */
  session_id: string;
  /** The weakest supporting fact, 0–1. Null when the answer rests on none. */
  confidence: number | null;
  band: Band;
  /** Why the score is what it is, in words. */
  basis: string | null;
  citations: Citation[];
  /** Published pages behind any attributed general-medicine sentence. */
  web: WebSource[];
  grounded: boolean;
  /** Claims the verifier could not trace to evidence. */
  unsupported: string[] | null;
  /** What was consulted, in order — the trace view. */
  tool_calls: string[];
  /** Tokens and cost. Null if metering found nothing to report. */
  usage: Usage | null;
  /**
   * The question was refused before anything was looked up — it read as an
   * attempt to change how the agent works rather than as a question.
   *
   * Sent as its own flag rather than left to be inferred, because a refusal and
   * a good answer that found nothing look identical from the other fields: both
   * arrive unscored, with no citations and no tools consulted. They must not
   * render the same, so the screen is told which it has.
   */
  blocked: boolean;
  disclaimer: string;
}

/** One conversation, as it appears in the list. */
export interface Session {
  id: string;
  title: string;
  created_at: string;
  /** Last message time. What the list is ordered by. */
  updated_at: string;
}

/**
 * One stored message.
 *
 * The apparatus fields are filled on an answer and defaulted on a question, so
 * `role` is the only branch a caller needs — nothing has to be null-checked
 * before it can be rendered.
 */
export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  confidence: number | null;
  band: Band;
  basis: string | null;
  citations: Citation[];
  web: WebSource[];
  grounded: boolean;
  unsupported: string[] | null;
  tool_calls: string[];
  /** Null on a question, and on any answer stored before metering existed. */
  usage: Usage | null;
  /** The reader left before this answer finished. It is deliberately partial. */
  truncated: boolean;
  /** This turn was refused by the input guard. See `Answer.blocked`. */
  blocked: boolean;
  /** Sent by the server rather than written here — it is safety wording. */
  disclaimer: string;
}

export interface Transcript {
  session: Session;
  messages: Message[];
}

export function listSessions(token: string | null): Promise<Session[]> {
  return apiFetch<Session[]>("/qa/sessions", { token });
}

export function getTranscript(token: string | null, sessionId: string): Promise<Transcript> {
  return apiFetch<Transcript>(`/qa/sessions/${sessionId}`, { token });
}

export function renameSession(
  token: string | null,
  sessionId: string,
  title: string,
): Promise<Session> {
  return apiFetch<Session>(`/qa/sessions/${sessionId}`, {
    token,
    method: "PATCH",
    json: { title },
  });
}

export function deleteSession(token: string | null, sessionId: string): Promise<void> {
  return apiFetch<void>(`/qa/sessions/${sessionId}`, { token, method: "DELETE" });
}

export async function ask(
  token: string | null,
  question: string,
  sessionId: string | null,
): Promise<Answer> {
  return apiFetch<Answer>("/qa/ask", {
    token,
    method: "POST",
    json: { question, session_id: sessionId },
  });
}

/** What the stream reports while the answer is still being worked out. */
export interface StreamWatcher {
  /** A lookup began. Names are tool names — pass through `toolLabel`. */
  onStep?(tools: string[]): void;
  /** A fragment of the answer. Append it. */
  onToken?(text: string): void;
  /**
   * Which conversation this landed in. Arrives before any answer text, so a
   * question that started a new conversation can put its id in the address bar
   * straight away — a reader who navigates away mid-answer still has somewhere
   * to come back to.
   */
  onSession?(session: { id: string; title: string }): void;
  /**
   * Discard everything shown so far and start the answer again.
   *
   * The server no longer emits this: it composes once and labels the result
   * rather than rewriting it. Kept because the cost is three lines and the
   * failure it prevents is ugly — a server still running the old retry loop
   * would otherwise concatenate two answers on screen.
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
  sessionId: string | null,
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
      body: JSON.stringify({ question, session_id: sessionId }),
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
          case "session":
            watcher.onSession?.({ id: event.id, title: event.title });
            break;
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
