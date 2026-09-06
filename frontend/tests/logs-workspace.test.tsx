import assert from "node:assert/strict"
import test from "node:test"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter } from "react-router-dom"
import { LogsWorkspace, LogsWorkspaceHeader } from "../src/pages/logs/logs-workspace"

function render(communications: boolean, filtersOpen = false) {
  return renderToStaticMarkup(
    <MemoryRouter basename="/v2" initialEntries={["/v2/logs?view=communications&comm_source=voice&comm_page=2"]}>
      <LogsWorkspace>
        <LogsWorkspaceHeader communications={communications} filtersOpen={filtersOpen}
          onToggleFilters={() => {}} filterCount={1} />
        <section>Results</section>
      </LogsWorkspace>
    </MemoryRouter>,
  )
}

for (const communications of [true, false]) {
  test(`logs navigation exposes exactly one high-contrast active link (${communications ? "communications" : "events"})`, () => {
    const html = render(communications)
    const anchors = html.match(/<a\b[^>]*>[\s\S]*?<\/a>/g) ?? []
    const active = anchors.filter((anchor) => anchor.includes('aria-current="page"'))
    assert.equal(active.length, 1)
    assert.ok(active[0].includes(communications ? "Общение игроков" : "Журнал событий"))
    assert.ok(active[0].includes('data-variant="default"'))
    assert.ok(active[0].includes("underline-offset-4"))
    assert.ok(anchors.find((anchor) => !anchor.includes('aria-current="page"'))?.includes('data-variant="outline"'))
    assert.match(html, /<nav aria-label="Разделы логов"/)
  })
}

test("switching views preserves investigation query parameters and the V2 basename", () => {
  const html = render(true)
  const hrefs = [...html.matchAll(/href="([^"]+)"/g)].map((match) => match[1].replaceAll("&amp;", "&"))
  assert.equal(hrefs.length, 2)
  const urls = hrefs.map((href) => new URL(href, "https://nexus.invalid"))
  assert.ok(urls.every((url) => url.pathname === "/v2/logs"))
  assert.equal(urls[0].searchParams.get("view"), null)
  assert.equal(urls[1].searchParams.get("view"), "communications")
  assert.ok(urls.every((url) => url.searchParams.get("comm_source") === "voice"))
  assert.ok(urls.every((url) => url.searchParams.get("comm_page") === "2"))
})

test("both views share viewport-constrained layout and an accessible filter toggle", () => {
  for (const communications of [true, false]) {
    const html = render(communications, true)
    assert.match(html, /data-logs-workspace/)
    assert.match(html, /h-\[calc\(100dvh-3\.5rem\)\]/)
    assert.match(html, /overflow-hidden/)
    assert.match(html, /aria-expanded="true" aria-controls="logs-filters-v2"/)
    assert.ok(html.includes("Фильтры (1)"))
  }
})
