import assert from "node:assert/strict"
import test from "node:test"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import {
  buildRatingV2Query,
  getRatingProfiles,
  normalizeRatingLeaderboard,
  prepareRatingConfigPayload,
} from "../src/pages/ratings/rating-v2-contract"
import { RatingV2Investigation } from "../src/pages/ratings/rating-v2-views"

test("rating v2 query always carries mode and role, and omits an empty player id", () => {
  assert.equal(buildRatingV2Query({ mode: "pvp", role: "security", steamId: "" }), "mode=pvp&role=security")
  assert.equal(buildRatingV2Query({ mode: "mission", role: "captain", steamId: "7656119" }), "mode=mission&role=captain&steam_id=7656119")
})

test("rating v2 leaderboard keeps usable rows when optional transparency fields are absent", () => {
  const result = normalizeRatingLeaderboard({
    leaderboard: [{ steam_id: "7656119", player_name: "Ada", rating: 42 }],
    roles: ["captain"],
  })

  assert.deepEqual(result, {
    leaderboard: [{ rank: 1, steamId: "7656119", playerName: "Ada", rating: 42, points: null, activity: null, playtimeHours: null, qualifiedRounds: null, confidence: null, eligibility: null, eventCount: null, lastActivity: null, extra: [] }],
    roles: ["captain"],
    selectedRole: "captain",
    extra: [],
  })
})

test("rating v2 leaderboard exposes unknown scalar fields as transparent metadata", () => {
  const result = normalizeRatingLeaderboard({
    selected_role: "security",
    leaderboard: [{ rank: 4, steam_id: "7656119", player_name: "Ada", rating: 42, win_rate: 0.75, matches: 12 }],
    generated_at: "2026-09-07T00:00:00Z",
  })

  assert.deepEqual(result.leaderboard[0].extra, [["win_rate", 0.75], ["matches", 12]])
  assert.deepEqual(result.extra, [["generated_at", "2026-09-07T00:00:00Z"]])
  assert.equal(result.selectedRole, "security")
})

test("rating v2 leaderboard recognizes projection-state field names", () => {
  const result = normalizeRatingLeaderboard({
    leaderboard: [{ steam_id: "7656119", current_rating: 42, cumulative_points: 15, last_event_at: "2026-09-07T00:00:00Z" }],
  })
  assert.equal(result.leaderboard[0].rating, 42)
  assert.equal(result.leaderboard[0].points, 15)
  assert.equal(result.leaderboard[0].lastActivity, "2026-09-07T00:00:00Z")
})

test("rating config profiles accept array and keyed response shapes without inventing presets", () => {
  assert.deepEqual(getRatingProfiles({ profiles: ["balanced", { id: "hardcore", label: "Hardcore" }] }), [
    { id: "balanced", label: "balanced" }, { id: "hardcore", label: "Hardcore" },
  ])
  assert.deepEqual(getRatingProfiles({ profiles: { casual: { label: "Casual" }, pvp: {} } }), [
    { id: "casual", label: "Casual" }, { id: "pvp", label: "pvp" },
  ])
  assert.deepEqual(getRatingProfiles({ config: { profiles: { ranked: { label: "Ranked" } } } }), [
    { id: "ranked", label: "Ranked" },
  ])
})

test("rating profile selection preserves unknown config settings", () => {
  assert.deepEqual(prepareRatingConfigPayload({ profiles: { balanced: {} }, decay: 0.15, opaque_backend_setting: { keep: true } }, "balanced"), {
    profiles: { balanced: {} }, decay: 0.15, opaque_backend_setting: { keep: true }, active_profile: "balanced",
  })
  assert.deepEqual(prepareRatingConfigPayload({ config: { profiles: { pvp: {} }, decay: 0.2 }, server_revision: 3 }, "pvp"), {
    config: { profiles: { pvp: {} }, decay: 0.2, active_profile: "pvp" }, server_revision: 3,
  })
})

test("rating investigation shows player, explanation, and event log from v2 responses", () => {
  const html = renderToStaticMarkup(<RatingV2Investigation
    player={{ steam_id: "7656119", player_name: "Ada", rating: 42, decay_factor: 0.9 }}
    explanation={{ total: 42, formula: "base * decay" }}
    events={{ events: [{ event_type: "pvp_kill", points_delta: 5, created_at: "2026-09-07T00:00:00Z" }] }}
    history={{ history: [{ captured_at: "2026-09-07T00:00:00Z", rank: 1, rating: 42 }] }}
  />)

  assert.match(html, /Данные игрока/)
  assert.match(html, /Объяснение расчёта/)
  assert.match(html, /Журнал рейтинга/)
  assert.match(html, /decay_factor/)
  assert.match(html, /pvp_kill/)
})

test("rating investigation renders explicit empty states for missing v2 fields", () => {
  const html = renderToStaticMarkup(<RatingV2Investigation player={undefined} explanation={undefined} events={{}} />)
  assert.equal((html.match(/Нет данных/g) ?? []).length, 4)
  assert.match(html, /История появится/)
})
