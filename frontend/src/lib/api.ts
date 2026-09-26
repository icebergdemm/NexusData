import { readBrowserFingerprintCookie } from "./browser-fingerprint"

/**
 * Typed fetch client for the NexusData API.
 *
 * Auth rides on `admin_token`; browser-bound sessions also send the current
 * FingerprintJS ID from their first-party cookie.
 */

export class ApiError extends Error {
  status: number
  details?: unknown
  constructor(status: number, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.details = details
  }
}

export function isMaintenanceError(error: unknown): boolean {
  return error instanceof ApiError
    && error.status === 503
    && Boolean((error.details as { maintenance?: boolean } | undefined)?.maintenance)
}

let csrfToken: string | null = null

export function setCsrfToken(token: string | null) {
  csrfToken = token
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase()
  const headers = new Headers(init?.headers)
  if (!headers.has("Content-Type") && init?.body && typeof init.body === "string") {
    headers.set("Content-Type", "application/json")
  }
  if (method !== "GET" && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken)
  }
  const browserFingerprint = await readBrowserFingerprintCookie()
  if (browserFingerprint) headers.set("X-ND-Browser-Fingerprint", browserFingerprint)

  const res = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "include",
  })

  if (res.status === 401) {
    // The dev-only design preview runs on a mock session; a background poll
    // answered 401 by a locally running backend must not throw the reviewer
    // out to /login.
    if (!(import.meta.env.DEV && /\/design-preview\/?$/.test(window.location.pathname))) {
      const next = encodeURIComponent(window.location.pathname + window.location.search)
      window.location.href = `/login?next=${next}`
    }
    // Navigation is async; throw so callers' promise chains stop here.
    throw new ApiError(401, "Not authenticated")
  }

  const contentType = res.headers.get("content-type") || ""
  const body = contentType.includes("application/json") ? await res.json().catch(() => null) : null

  if (res.status === 503 && body && (body as { maintenance?: boolean }).maintenance) {
    // Backend maintenance_gate_middleware (see main.py) — API disabled,
    // whole-site maintenance mode, or the database is down. Notify the
    // global MaintenanceBanner (mounted in main.tsx) without changing the
    // thrown-error contract callers already rely on.
    window.dispatchEvent(new CustomEvent("nd-maintenance", { detail: body }))
  }

  if (!res.ok) {
    const responseMessage = body && (body.error || body.detail)
    const message = typeof responseMessage === "string" && responseMessage.trim()
      ? responseMessage
      : res.statusText.trim() || `Ошибка HTTP ${res.status}`
    throw new ApiError(res.status, message, body)
  }

  return body as T
}

export const api = {
  get: <T>(path: string, init?: RequestInit) => request<T>(path, init),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "POST", body: data !== undefined ? JSON.stringify(data) : undefined }),
  put: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "PUT", body: data !== undefined ? JSON.stringify(data) : undefined }),
  patch: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "PATCH", body: data !== undefined ? JSON.stringify(data) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  /** multipart/form-data POST (file uploads) — do not JSON-encode the body. */
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
}
