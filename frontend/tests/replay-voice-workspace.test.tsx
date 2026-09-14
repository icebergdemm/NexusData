import assert from "node:assert/strict"
import test from "node:test"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter } from "react-router-dom"
import { TooltipProvider } from "../src/components/ui/tooltip"

globalThis.window = { location: { pathname: "/v2" } } as unknown as Window & typeof globalThis
const {
  ReplayVoiceWorkspace,
  playbackPositionFromPointer,
  timelineWindowAfterWheel,
} = await import("../src/pages/replays/ReplayVoiceWorkspace")

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

test("voice workspace renders one timeline without placeholder categories or duplicate player cards", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter><TooltipProvider><ReplayVoiceWorkspace replayKind="lobby" initialData={data} /></TooltipProvider></MemoryRouter>,
  )

  assert.doesNotMatch(html, /Включить голос/)
  assert.match(html, /aria-label="Позиция записи"/)
  assert.match(html, /Канал/)
  assert.match(html, /Участники/)
  assert.doesNotMatch(html, /Голос \+ чат/)
  assert.doesNotMatch(html, /События/)
  assert.doesNotMatch(html, />чат</)
  assert.doesNotMatch(html, />Игроки</)
  assert.match(html, /Alice/)
  assert.match(html, /href="\/players\/7"/)
  assert.match(html, /Unknown/)
  assert.match(html, /Не сопоставлен/)
})

test("timeline pointer and wheel produce bounded replay positions", () => {
  assert.equal(playbackPositionFromPointer(150, { left: 100, width: 200 }, 1_000), 250)
  assert.deepEqual(timelineWindowAfterWheel({
    startMs: 100,
    endMs: 300,
    durationMs: 1_000,
    anchorRatio: 0.5,
    deltaY: 100,
    pan: false,
  }), { startMs: 80, endMs: 320 })
  assert.deepEqual(timelineWindowAfterWheel({
    startMs: 100,
    endMs: 300,
    durationMs: 1_000,
    anchorRatio: 0.5,
    deltaY: 100,
    pan: true,
  }), { startMs: 140, endMs: 340 })
})

test("round voice workspace uses the same component contract", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter><TooltipProvider><ReplayVoiceWorkspace replayKind="round" initialData={data} /></TooltipProvider></MemoryRouter>,
  )

  assert.match(html, /Голосовая лента/)
  assert.match(html, /локальная речь/)
  assert.match(html, /радио/)
})
