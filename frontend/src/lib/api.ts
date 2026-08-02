/**
 * The one place that talks to FastAPI.
 *
 * Everything goes through `apiFetch`, so the base URL, the bearer header and
 * the shape of an error are decided once. Callers get a typed result or an
 * `ApiError` carrying the status — which matters because a 404 from
 * `/patients/me` is not a failure, it is the answer.
 */

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/+$/,
  "",
);

export class ApiError extends Error {
  // Assigned in the body rather than as a constructor parameter property: the
  // project builds with `erasableSyntaxOnly`, which rules that shorthand out.
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Raised when the API cannot be reached at all — server down, CORS, offline. */
export class NetworkError extends Error {
  constructor() {
    super("Cannot reach CrossCheck. Check that the API is running.");
    this.name = "NetworkError";
  }
}

/**
 * FastAPI answers with `{detail: "..."}` for HTTPException and with
 * `{detail: [{loc, msg, ...}]}` for validation failures. Flatten both, and fall
 * back to the status line if the body is not JSON at all.
 */
async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => item?.msg).filter(Boolean).join("; ") || response.statusText;
    }
  } catch {
    /* not JSON — fall through */
  }
  return response.statusText || `Request failed (${response.status})`;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  /** Clerk session token. Omitted only for endpoints that do not need one. */
  token?: string | null;
  /** Serialised as JSON, with the header set to match. */
  json?: unknown;
  /** A file upload. Sent as-is with no Content-Type of our own — see below. */
  form?: FormData;
  /**
   * Called with the HTTP status before the body is parsed.
   *
   * Only one endpoint needs this: `POST /documents` answers 201 for a new
   * document and 200 when the same bytes are already stored, and the body is
   * identical either way — the status is the only thing that tells them apart.
   */
  onStatus?: (status: number) => void;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { token, json, form, headers, onStatus, ...rest } = options;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...rest,
      body: form ?? (json === undefined ? undefined : JSON.stringify(json)),
      headers: {
        // Only for JSON. A multipart body must carry a boundary marker in its
        // Content-Type, and only the browser knows what that boundary is —
        // setting the header ourselves would send one without it, and the
        // server would fail to parse a request that is otherwise correct.
        ...(json === undefined ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
    });
  } catch {
    // fetch only rejects when the request never completed, so this is always a
    // transport problem rather than an HTTP error.
    throw new NetworkError();
  }

  onStatus?.(response.status);

  if (!response.ok) throw new ApiError(response.status, await readError(response));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
