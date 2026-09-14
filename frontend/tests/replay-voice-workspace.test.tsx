import assert from "node:assert/strict"
import test from "node:test"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter } from "react-router-dom"
import { TooltipProvider } from "../src/components/ui/tooltip"

globalThis.window = { location: { pathname: "/v2" } } as unknown as Window & typeof globalThis
const { ReplayVoiceWorkspace } = await import("../src/pages/replays/ReplayVoiceWorkspace")

const data = {
  durationMs: 1_000,
  voiceSpeakers: [
    { speaker_id: 1, names: ["Alice"], player_id: 7, steam_id: "76561198000000001", resolution_status: "mapped" },
    { speaker_id: 2, names: ["Unknown"], player_id: null, steam_id: null, resolution_status: "unresolved" },
  ],
  voiceSegments: [
    { speaker_id: 1, start_ms: 100, end_ms: 400, radio: false },
    { speaker_id: 2, start_ms: 500, end_ms: 700, radio: true },
  ],
  timelineEvents: [],
}

test("voice workspace renders one shared filter bar without a voice enable toggle", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter><TooltipProvider><ReplayVoiceWorkspace replayKind="lobby" initialData={data} /></TooltipProvider></MemoryRouter>,
  )

  assert.doesNotMatch(html, /Включить голос/)
  assert.match(html, /Только голос/)
  assert.match(html, /Голос \+ чат/)
  assert.match(html, /События/)
  assert.match(html, /Alice/)
  assert.match(html, /href="\/players\/7"/)
  assert.match(html, /Unknown/)
  assert.match(html, /Профиль не найден/)
})

test("round voice workspace uses the same component contract", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter><TooltipProvider><ReplayVoiceWorkspace replayKind="round" initialData={data} /></TooltipProvider></MemoryRouter>,
  )

  assert.match(html, /Голосовая лента/)
  assert.match(html, /локальная речь/)
  assert.match(html, /радио/)
})
