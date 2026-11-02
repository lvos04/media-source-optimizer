// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/api/client.ts — internally versioned copy, see PROVENANCE.md
/**
 * Typed client for the MSO core API (`/api/v1/*`, proxied by server.mjs).
 * - Session = HttpOnly cookie (sent automatically, same origin).
 * - Mutations send `X-CSRF-Token` (value from `GET /api/v1/auth/me` or `POST /api/v1/auth/login`).
 * - Every failure becomes an `ApiError` with a `kind` so the UI can render the right state
 *   (unavailable endpoint ≠ backend offline ≠ validation error ≠ forbidden).
 * - Error body shape: `{ detail, code, correlation_id }`.
 */

export const CORE_BASE = "/api/v1";

export type ApiErrorKind =
  | "unauthorized" // 401 – not logged in / session expired
  | "forbidden" // 403 – role too low, CSRF failure or blocked by read-only mode
  | "unavailable" // 404/405/501 on a route the backend does not (yet) implement
  | "not_found" // 404 for an existing route but missing resource
  | "validation" // 400/409/422 – user-correctable
  | "offline" // network error / 502 / 503 / 504 – backend not reachable
  | "server"; // other 5xx

export class ApiError extends Error {
  constructor(
    public readonly kind: ApiErrorKind,
    public readonly status: number,
    message: string,
    public readonly path: string,
    public readonly correlationId: string | null = null,
    public readonly detail: unknown = null,
    /** Machine-readable error code from the body (`code`), e.g. `connector_not_configured`. */
    public readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken: string | null = null;
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}
export function getCsrfToken(): string | null {
  return csrfToken;
}

type Listener = (error: ApiError) => void;
const unauthorizedListeners = new Set<Listener>();
/** Session layer subscribes so an expired session anywhere sends the user to /login. */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation errors
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const loc = "loc" in item && Array.isArray(item.loc) ? item.loc.filter((p: unknown) => p !== "body").join(".") : "";
          return loc ? `${loc}: ${String(item.msg)}` : String(item.msg);
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    const text = d.message_nl ?? d.message ?? d.detail;
    if (typeof text === "string") return text;
    return JSON.stringify(detail);
  }
  return fallback;
}

function kindFor(status: number, detail: unknown): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return detail === "Not Found" ? "unavailable" : "not_found";
  if (status === 405 || status === 501) return "unavailable";
  if (status === 400 || status === 409 || status === 422) return "validation";
  if (status === 502 || status === 503 || status === 504) return "offline";
  return "server";
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export function buildPath(path: string, query?: RequestOptions["query"]): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== null && v !== undefined && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `${path}${path.includes("?") ? "&" : "?"}${s}` : path;
}

export async function coreRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const url = `${CORE_BASE}${buildPath(path, options.query)}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "same-origin",
      cache: "no-store",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError("offline", 0, "network", path);
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const record = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
    const detail = record && "detail" in record ? record.detail : null;
    const code = record && typeof record.code === "string" ? record.code : null;
    const correlationId = (record && typeof record.correlation_id === "string" ? record.correlation_id : null)
      ?? response.headers.get("x-correlation-id") ?? response.headers.get("x-request-id");
    // the proxy answers 502 {code:"upstream_unreachable"} when the core is down
    const kind = code === "upstream_unreachable" ? "offline" : kindFor(response.status, detail);
    const error = new ApiError(kind, response.status, messageFrom(detail, response.statusText || String(response.status)), path, correlationId, detail, code);
    if (kind === "unauthorized") unauthorizedListeners.forEach((l) => l(error));
    throw error;
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? await response.json() : await response.text()) as T;
}

export const api = {
  get: <T>(path: string, query?: RequestOptions["query"], signal?: AbortSignal) => coreRequest<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown) => coreRequest<T>(path, { method: "POST", body: body ?? {} }),
  put: <T>(path: string, body?: unknown) => coreRequest<T>(path, { method: "PUT", body: body ?? {} }),
  patch: <T>(path: string, body?: unknown) => coreRequest<T>(path, { method: "PATCH", body: body ?? {} }),
  del: <T>(path: string, body?: unknown) => coreRequest<T>(path, { method: "DELETE", body }),
};

/** Human-readable message for any thrown value (ApiError keeps the backend `detail`). */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
