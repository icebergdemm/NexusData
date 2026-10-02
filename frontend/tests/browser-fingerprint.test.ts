import assert from "node:assert/strict"
import test from "node:test"

import { api } from "../src/lib/api"

test("admin API forwards the browser fingerprint from the first-party cookie", async () => {
  const originalFetch = globalThis.fetch
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document")
  let requestHeaders: Headers | undefined

  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { cookie: "theme=dark; nd_browser_fingerprint=browser-id%2F1" },
  })
  globalThis.fetch = (async (_input, init) => {
    requestHeaders = new Headers(init?.headers)
    return new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  }) as typeof fetch

  try {
    await api.get("/api/v1/me")
    assert.equal(requestHeaders?.get("X-ND-Browser-Fingerprint"), "browser-id/1")
  } finally {
    globalThis.fetch = originalFetch
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument)
    else delete (globalThis as { document?: Document }).document
  }
})
