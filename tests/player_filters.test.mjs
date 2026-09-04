import assert from "node:assert/strict"
import test from "node:test"

import { filterAndSortPlayers } from "../frontend/src/lib/player-filters.ts"

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
  const result = filterAndSortPlayers(players, "linked", { key: "name", direction: "asc" })

  assert.deepEqual(result.map((player) => player.player_name), ["Альфа"])
})

test("descending playtime sort puts the longest-played player first", () => {
  const result = filterAndSortPlayers(players, "all", { key: "play_time", direction: "desc" })

  assert.deepEqual(result.map((player) => player.player_name), ["Бета", "Альфа"])
})

test("missing sort values stay after known values", () => {
  const result = filterAndSortPlayers(
    [
      { player_last_time_name: "Без даты", last_login_time: null, has_discord_link: false },
      { player_last_time_name: "С датой", last_login_time: "2026-09-04T10:00:00Z", has_discord_link: false },
    ],
    "all",
    { key: "last_login", direction: "desc" },
  )

  assert.deepEqual(result.map((player) => player.player_last_time_name), ["С датой", "Без даты"])
})
