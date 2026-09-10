import assert from "node:assert/strict"
import test from "node:test"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { VoiceProfilePlayerCard } from "../src/pages/voice-profiles"

const profile = {
  player_id: 7,
  steam_id: "76561198000000001",
  display_name: "Alice",
  clips: [
    {
      id: 1,
      source_replay_id: 1062,
      start_ms: 100,
      end_ms: 900,
      packet_count: 12,
      radio_packet_count: 0,
      storage_size: 1234,
      slot: 0,
      band: "early",
      audio_url: "/api/v1/admin/voice-profiles/clips/1/audio",
    },
    {
      id: 2,
      source_replay_id: 1063,
      start_ms: 200,
      end_ms: 1_200,
      packet_count: 14,
      radio_packet_count: 4,
      storage_size: 2345,
      slot: 24,
      band: "latest",
      audio_url: "/api/v1/admin/voice-profiles/clips/2/audio",
    },
  ],
}

test("voice profile player card is collapsed by default", () => {
  const html = renderToStaticMarkup(<VoiceProfilePlayerCard profile={profile} />)

  assert.match(html, /Alice/)
  assert.match(html, /Показать/)
  assert.doesNotMatch(html, /<audio/)
})

test("expanded voice profile player card exposes playback and clip metadata", () => {
  const html = renderToStaticMarkup(
    <VoiceProfilePlayerCard profile={profile} initiallyExpanded />,
  )

  assert.match(html, /Alice/)
  assert.match(html, /early/)
  assert.match(html, /latest/)
  assert.match(html, /Replay #1063/)
  assert.match(html, /radio/)
  assert.match(html, /<audio[^>]+controls[^>]+src="\/api\/v1\/admin\/voice-profiles\/clips\/2\/audio"/)
})
