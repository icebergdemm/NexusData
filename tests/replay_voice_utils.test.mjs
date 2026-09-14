import assert from "node:assert/strict"
import test from "node:test"

import {
  buildVoiceLanes,
  filterVoiceLanes,
  replayDisplayMode,
  voiceViewFromSearch,
} from "../frontend/src/pages/replays/replay-voice-utils.ts"

const speakers = [
  { speaker_id: 2, names: ["Zed"], player_id: null, steam_id: null, resolution_status: "unresolved" },
  { speaker_id: 1, names: ["Alice"], player_id: 7, steam_id: "76561198000000001", resolution_status: "mapped" },
]

const segments = [
  { speaker_id: 2, start_ms: 400, end_ms: 900, radio: false },
  { speaker_id: 1, start_ms: 100, end_ms: 300, radio: false },
  { speaker_id: 1, start_ms: 301, end_ms: 500, radio: false },
  { speaker_id: 1, start_ms: 700, end_ms: 800, radio: true },
]

test("buildVoiceLanes keeps players alphabetic and merges adjacent activity", () => {
  const lanes = buildVoiceLanes(speakers, segments, 1_000)

  assert.deepEqual(lanes.map((lane) => lane.name), ["Alice", "Zed"])
  assert.deepEqual(lanes[0].segments, [
    { speakerId: 1, startMs: 100, endMs: 500, radio: false },
    { speakerId: 1, startMs: 700, endMs: 800, radio: true },
  ])
  assert.equal(lanes[0].totalMs, 500)
})

test("filterVoiceLanes can show only radio activity and selected speakers", () => {
  const lanes = buildVoiceLanes(speakers, segments, 1_000)

  const filtered = filterVoiceLanes(lanes, {
    category: "voice",
    channel: "radio",
    activeOnly: true,
    speakerIds: new Set([1]),
  })

  assert.deepEqual(filtered.map((lane) => lane.name), ["Alice"])
  assert.deepEqual(filtered[0].segments, [
    { speakerId: 1, startMs: 700, endMs: 800, radio: true },
  ])
})

test("voice view query defaults to full mode and accepts only voice mode", () => {
  assert.equal(voiceViewFromSearch(""), "full")
  assert.equal(voiceViewFromSearch("?view=voice"), "voice")
  assert.equal(voiceViewFromSearch("?view=garbage"), "full")
})

test("lobby always uses voice mode while round defaults to the full viewer", () => {
  assert.equal(replayDisplayMode("lobby", ""), "voice")
  assert.equal(replayDisplayMode("lobby_dump", "?view=full"), "voice")
  assert.equal(replayDisplayMode("round", ""), "full")
  assert.equal(replayDisplayMode("round", "?view=voice"), "voice")
})
