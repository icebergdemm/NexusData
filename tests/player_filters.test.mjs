import assert from "node:assert/strict"
import test from "node:test"

import {
  countActivePlayerFilters,
  filterAndSortPlayers,
  getDiscordFilterLabel,
  resetPlayerFilters,
} from "../frontend/src/lib/player-filters.ts"

const players = [
  {
    player_steamid: "76561198000000000",
    player_name: "Бета",
    server_name: "ASPER2",
    join_time: "2026-09-04T09:00:00Z",
    total_play_time: 7200,
    has_discord_link: false,
  },
  {
    player_steamid: "76561198000000001",
    player_name: "Альфа",
    server_name: "ASPER1",
    join_time: "2026-09-04T10:00:00Z",
    total_play_time: 3600,
    has_discord_link: true,
  },
]

test("linked filter removes players without a Discord link", () => {
  const result = filterAndSortPlayers(players, { discordFilter: "linked", labelId: "all", rankId: "all" }, { key: "name", direction: "asc" })

  assert.deepEqual(result.map((player) => player.player_name), ["Альфа"])
})

test("descending playtime sort puts the longest-played player first", () => {
  const result = filterAndSortPlayers(players, { discordFilter: "all", labelId: "all", rankId: "all" }, { key: "play_time", direction: "desc" })

  assert.deepEqual(result.map((player) => player.player_name), ["Бета", "Альфа"])
})

test("missing sort values stay after known values", () => {
  const result = filterAndSortPlayers(
    [
      { player_last_time_name: "Без даты", last_login_time: null, has_discord_link: false },
      { player_last_time_name: "С датой", last_login_time: "2026-09-04T10:00:00Z", has_discord_link: false },
    ],
    { discordFilter: "all", labelId: "all", rankId: "all" },
    { key: "last_login", direction: "desc" },
  )

  assert.deepEqual(result.map((player) => player.player_last_time_name), ["С датой", "Без даты"])
})

test("combined filters keep only players matching Discord, label, and rank", () => {
  const result = filterAndSortPlayers(
    [
      { player_last_time_name: "Подходит", rank_id: 2, label_ids: [7], has_discord_link: true },
      { player_last_time_name: "Другая метка", rank_id: 2, label_ids: [8], has_discord_link: true },
      { player_last_time_name: "Другое звание", rank_id: 3, label_ids: [7], has_discord_link: true },
      { player_last_time_name: "Без Discord", rank_id: 2, label_ids: [7], has_discord_link: false },
    ],
    { discordFilter: "linked", labelId: "7", rankId: "2" },
    { key: "name", direction: "asc" },
  )

  assert.deepEqual(result.map((player) => player.player_last_time_name), ["Подходит"])
})

test("shared filter helpers count, label, and reset filters consistently", () => {
  const filters = { discordFilter: "linked", labelId: "7", rankId: "2" }
  assert.equal(countActivePlayerFilters(filters, { key: "play_time", direction: "asc" }, { key: "none", direction: "desc" }), 4)
  assert.equal(getDiscordFilterLabel("unlinked"), "Нет привязки")
  assert.deepEqual(resetPlayerFilters(), { discordFilter: "all", labelId: "all", rankId: "all" })
})
