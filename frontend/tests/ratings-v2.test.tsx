import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import {
  buildRatingV2Query,
  getRatingProfiles,
  normalizeRatingLeaderboard,
  prepareRatingConfigPayload,
} from "../src/pages/ratings/rating-v2-contract"
import { RatingEventDetails, RatingV2Investigation } from "../src/pages/ratings/rating-v2-views"

const ratingsPageSource = readFileSync(new URL("../src/pages/ratings/index.tsx", import.meta.url), "utf8")

test("rating v2 query carries mode, role, pagination, and omits an empty player id", () => {
  assert.equal(buildRatingV2Query({ mode: "pvp", role: "all", page: 2, pageSize: 25, steamId: "" }), "mode=pvp&role=all&page=2&page_size=25")
  assert.equal(buildRatingV2Query({ mode: "mission", role: "captain", page: 1, pageSize: 50, steamId: "7656119" }), "mode=mission&role=captain&page=1&page_size=50&steam_id=7656119")
})

test("ratings page uses one paginated role table with row and drawer navigation", () => {
  assert.match(ratingsPageSource, /<Select value=\{role\}/)
  assert.match(ratingsPageSource, /pageSize: PAGE_SIZE/)
  assert.match(ratingsPageSource, /onClick=\{\(\) => selectPlayer\(entry\.steamId, entry\.role\)\}/)
  assert.match(ratingsPageSource, /<Sheet open=\{Boolean\(selectedPlayer\)\}/)
  assert.match(ratingsPageSource, /search-suggestions\?q=/)
  assert.match(ratingsPageSource, /PlayerHoverLink/)
  assert.match(ratingsPageSource, /Обновить Discord/)
  assert.doesNotMatch(ratingsPageSource, /leaderboard\.extra\.map/)
  assert.match(ratingsPageSource, /setDrawerRole\("all"\)/)
  assert.match(ratingsPageSource, /aria-label="Роль в превью"/)
  assert.doesNotMatch(ratingsPageSource, /Enter — открыть игрока/)
  assert.doesNotMatch(ratingsPageSource, /onKeyDown=\{\(event\) => event\.key === "Enter" && selectPlayer\(searchInput\)\}/)
  assert.doesNotMatch(ratingsPageSource, /<Tabs value=\{role\}/)
})

test("rating v2 leaderboard keeps usable rows when optional transparency fields are absent", () => {
  const result = normalizeRatingLeaderboard({
    leaderboard: [{ steam_id: "7656119", player_name: "Ada", rating: 42 }],
    roles: ["captain"],
  })

  assert.deepEqual(result, {
    leaderboard: [{ rank: 1, steamId: "7656119", playerId: null, playerName: "Ada", role: null, rating: 42, points: null, activity: null, playtimeHours: null, qualifiedRounds: null, confidence: null, eligibility: null, dataQuality: null, eventCount: null, lastActivity: null, extra: [] }],
    roles: ["captain"],
    selectedRole: "captain",
    pagination: { total: 1, totalPages: 1, page: 1, pageSize: 100, hasNext: false },
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

test("rating v2 normalizes unified role rows and server pagination", () => {
  const result = normalizeRatingLeaderboard({
    selected_role: "all",
    leaderboard: [{ rank: 26, steam_id: "7656119", player_name: "Ada", role: "security", current_rating: 42 }],
    total: 51,
    total_pages: 3,
    page: 2,
    page_size: 25,
    has_next: true,
  })

  assert.equal(result.leaderboard[0].role, "security")
  assert.deepEqual(result.pagination, { total: 51, totalPages: 3, page: 2, pageSize: 25, hasNext: true })
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

test("rating investigation renders human-readable player details and clickable journal rows", () => {
  const html = renderToStaticMarkup(<RatingV2Investigation
    player={{ steam_id: "7656119", player_name: "Ada", mode: "pvp", role: "security", rating: 42, play_time_hours: 12.5, qualified_rounds: 3 }}
    explanation={{ current_rating: 42, cumulative_points: 37, contributions: { "combat.player_kill": 5 }, activity_factor: 0.9, formula_version: "rating-v2" }}
    events={{ events: [{ event_id: "evt-1", event_type: "combat.player_kill", points_delta: 5, raw_value: 1, mapping_status: "mapped", occurred_at: "2026-09-07T00:00:00Z" }] }}
    history={{ history: [{ captured_at: "2026-09-07T00:00:00Z", rank: 1, rating: 42 }] }}
    onEventSelect={() => undefined}
  />)

  assert.match(html, /Профиль игрока/)
  assert.match(html, />42<\/div>/)
  assert.match(html, /Почему такой рейтинг/)
  assert.match(html, /Журнал рейтинга/)
  assert.match(html, /Убийство игрока/)
  assert.match(html, /Технические данные/)
  assert.doesNotMatch(html, /"contributions"\s*:/)
})

test("rating investigation renders explicit empty states for missing v2 fields", () => {
  const html = renderToStaticMarkup(<RatingV2Investigation player={undefined} explanation={undefined} events={{}} />)
  assert.match(html, /Нет данных о профиле игрока/)
  assert.match(html, /Нет данных о расчёте/)
  assert.match(html, /Журнал пока пуст/)
  assert.match(html, /История появится/)
})

test("rating investigation renders all-role player preview", () => {
  const html = renderToStaticMarkup(<RatingV2Investigation
    player={{ steam_id: "7656119", mode: "mission", role: "all", ratings: [
      { role: "captain", current_rating: 42, cumulative_points: 12 },
      { role: "mechanic", current_rating: 31, cumulative_points: 9 },
    ] }}
    explanation={{ role: "all", roles: [
      { role: "captain", current_rating: 42, contributions: { "captain.navigate": 8 } },
      { role: "mechanic", current_rating: 31, contributions: { "engineering.repair": 4 } },
    ] }}
    events={{ events: [] }}
  />)
  assert.match(html, /Капитан/)
  assert.match(html, /Механик/)
  assert.match(html, /Показаны все роли/)
})

test("rating investigation shows delivery summary instead of an empty delivery table", () => {
  const html = renderToStaticMarkup(<RatingV2Investigation
    player={{ steam_id: "7656119", player_name: "Ada" }}
    explanation={{}}
    events={{}}
    delivery={{ entries: [], summary: { accepted: 12, duplicate: 2, rejected: 1, failed: 0 } }}
  />)

  assert.match(html, /Доставка событий/)
  assert.match(html, /Принято.*12/)
  assert.match(html, /Дубли.*2/)
  assert.doesNotMatch(html, /Нет данных о доставке/)
})

test("rating event drawer explains an event and keeps raw fields technical", () => {
  const html = renderToStaticMarkup(<RatingEventDetails
    event={{ event_id: "evt-1", event_type: "combat.player_kill", mode: "pvp", role: "security", raw_value: 1, points_delta: 5, mapping_status: "mapped", occurred_at: "2026-09-07T00:00:00Z", metadata: { victim: "bot" } }}
  />)

  assert.match(html, /Убийство игрока/)
  assert.match(html, /PvP/)
  assert.match(html, /Технические данные/)
  assert.match(html, /evt-1/)
})
