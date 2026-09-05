import assert from "node:assert/strict"
import test from "node:test"

import {
  countActivePlayerFilters,
  filterAndSortPlayers,
  getDiscordFilterLabel,
  getPlayerFilterDefaults,
  resetPlayerFilters,
} from "../frontend/src/lib/player-filters.ts"
import { parsePlayerFilterState } from "../frontend/src/lib/player-filter-storage.ts"
import { formatDossierRow } from "../frontend/src/pages/ai-center/format.ts"

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

test("dossier rows show human text with source metadata separately", () => {
  assert.deepEqual(
    formatDossierRow({ text: "Есть активное наказание", source: "punishment" }),
    { text: "Есть активное наказание", metadata: "Источник: наказание" },
  )
  assert.deepEqual(
    formatDossierRow({ text: "Проверить лог", reason: "Повторяющееся событие" }),
    { text: "Проверить лог", metadata: "Почему: Повторяющееся событие" },
  )
})

test("linked filter removes players without a Discord link", () => {
  const result = filterAndSortPlayers(players, { ...resetPlayerFilters(), discordFilter: "linked" }, { key: "name", direction: "asc" })

  assert.deepEqual(result.map((player) => player.player_name), ["Альфа"])
})

test("descending playtime sort puts the longest-played player first", () => {
  const result = filterAndSortPlayers(players, resetPlayerFilters(), { key: "play_time", direction: "desc" })

  assert.deepEqual(result.map((player) => player.player_name), ["Бета", "Альфа"])
})

test("missing sort values stay after known values", () => {
  const result = filterAndSortPlayers(
    [
      { player_last_time_name: "Без даты", last_login_time: null, has_discord_link: false },
      { player_last_time_name: "С датой", last_login_time: "2026-09-04T10:00:00Z", has_discord_link: false },
    ],
    resetPlayerFilters(),
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
    { ...resetPlayerFilters(), discordFilter: "linked", labelMode: "any", labelIds: ["7"], rankId: "2" },
    { key: "none", direction: "desc" },
  )

  assert.deepEqual(result.map((player) => player.player_last_time_name), ["Подходит"])
})

test("shared filter helpers count, label, and reset filters consistently", () => {
  const filters = { ...resetPlayerFilters(), discordFilter: "linked", labelMode: "any", labelIds: ["7"], rankId: "2" }
  assert.equal(countActivePlayerFilters(filters, { key: "play_time", direction: "asc" }, { key: "none", direction: "desc" }), 4)
  assert.equal(getDiscordFilterLabel("unlinked"), "Без Discord")
  assert.deepEqual(resetPlayerFilters(), getPlayerFilterDefaults())
})

test("facets filter by multiple labels and moderation flags", () => {
  const result = filterAndSortPlayers(
    [
      { player_last_time_name: "Подходит", label_ids: [7], has_notes: true, has_active_punishments: true, has_open_anomalies: false, has_previous_names: false, has_discord_link: true },
      { player_last_time_name: "Другая метка", label_ids: [8], has_notes: false, has_active_punishments: true, has_open_anomalies: true, has_previous_names: false, has_discord_link: true },
      { player_last_time_name: "Без признаков", label_ids: [], has_notes: true, has_active_punishments: true, has_open_anomalies: false, has_previous_names: false, has_discord_link: true },
    ],
    { ...resetPlayerFilters(), labelIds: ["7", "8"], labelMode: "any", notesMode: "with", sanctionsMode: "with", anomaliesMode: "without" },
    { key: "none", direction: "desc" },
  )

  assert.deepEqual(result.map((player) => player.player_last_time_name), ["Подходит"])
})

test("persisted filters reject invalid values and migrate the old single label", () => {
  const parsed = parsePlayerFilterState(JSON.stringify({ discordFilter: "linked", labelId: "7", rankId: "2", labelsMode: "broken" }))
  assert.deepEqual(parsed, { ...getPlayerFilterDefaults(), discordFilter: "linked", labelMode: "any", labelIds: ["7"], rankId: "2" })
})
