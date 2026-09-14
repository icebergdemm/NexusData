import assert from "node:assert/strict"
import test from "node:test"
import type { AIDossierResponse, PlayerDetailResponse } from "../src/pages/players/detail-types"
import {
  buildPlayerExport,
  PLAYER_EXPORT_GROUPS,
  serializePlayerExport,
  type PlayerTicketExportResponse,
} from "../src/pages/players/player-export"

const player = {
  player: {
    player_id: 1195,
    player_last_time_name: "Гаммас",
    player_steamid: "76561198000000001",
    last_login_time: null,
    play_time: 3600,
    play_time_hours: 1,
    rank_id: 1,
    kit_level: 2,
    last_ip: "203.0.113.7",
    discord_id: "42",
    discord_profile_id: "42",
    discord_id_confirmed: true,
    is_secret: false,
    linked_admin_id: null,
    linked_admin_name: null,
    forced_display_name: null,
    steam_avatar_url: null,
  },
  stats: null,
  kills: [],
  role_actions: [],
  ips: [],
  names: [],
  role_stats: [],
  sessions: [],
  notes: [],
  label_pairs: [],
  traps: [],
  label_categories: [],
  label_folders: [],
  punishments: [],
  ranks: {},
  kits: {},
  kval_kits: {},
  custom_items: {},
  custom_job_items: {},
  custom_job_item_variations: {},
  custom_stats: {},
  custom_talents: {},
  available_kval_kits: [],
  reward_reference: { jobs: [], skill_labels: {}, talents: {} },
  is_online: false,
  server_privileges: [],
  has_view: true,
  has_edit: false,
  can_manage_rewards: false,
  can_set_forced_name: false,
  can_manage_notes: false,
  can_manage_labels: false,
  can_manage_secret_labels: false,
  can_send_message: false,
  has_delete: false,
  has_special: false,
  can_issue_punishment: false,
  can_revoke_punishment: false,
  punishment_types: [],
  punishment_type_labels: {},
} satisfies PlayerDetailResponse

const dossier = {
  status: "succeeded",
  result: null,
  stale: false,
  summary: "summary",
  model: "qwen",
  requested_at: null,
  started_at: null,
  generated_at: "2026-09-01T00:00:00",
  finished_at: null,
  error: null,
  requested_by_admin_id: 1,
  run: {
    source: "manual",
    status: "succeeded",
    latency_ms: 10,
    prompt_tokens: 1,
    completion_tokens: 2,
    requested_by_admin_id: 1,
    raw_response: "secret model payload",
  },
} satisfies AIDossierResponse

const ticketArchive = {
  player_id: 1195,
  ticket_count: 1,
  tickets: [{
    ticket: {
      id: 460,
      ticket_type: "appeal",
      title: "Обжалование",
      status: "closed",
      discord_channel_id: null,
      creator_username: "reporter",
      creator_display_name: "Reporter",
      player_id: 1195,
      closed_by: null,
      created_at: "2026-09-01T00:00:00",
      closed_at: null,
      deleted_at: null,
      deleted_by: null,
      last_message_at: null,
      awaiting_admin: false,
    },
    messages: [],
    participants: [],
    mention_profiles: {},
    archive: { state: "complete", attachment_count: 0, total_bytes: 0, failed_count: 0, legacy_count: 0 },
  }],
} satisfies PlayerTicketExportResponse

test("player export includes all groups by default and strips AI raw response", () => {
  const sourceSnapshot = structuredClone(player)
  const data = buildPlayerExport({ player, dossier, ticketArchive, exportedAt: "2026-09-14T00:00:00.000Z" })

  assert.deepEqual(Object.keys(data).sort(), [
    "activity", "ai_assessment", "dossier_type", "exported_at", "history", "moderation", "profile", "rewards", "schema_version", "subject", "tickets",
  ].sort())
  assert.equal(data.subject.player_id, 1195)
  assert.equal(data.ai_assessment?.run?.raw_response, undefined)
  assert.deepEqual(player, sourceSnapshot)
})

test("excluded groups are absent rather than null", () => {
  const included = new Set(PLAYER_EXPORT_GROUPS.filter((group) => group !== "tickets" && group !== "rewards"))
  const data = buildPlayerExport({ player, dossier, ticketArchive, exportedAt: "2026-09-14T00:00:00.000Z" }, included)

  assert.equal("tickets" in data, false)
  assert.equal("rewards" in data, false)
  assert.equal(data.profile?.player_id, 1195)
  assert.equal(data.subject.profile_url, "/players/1195")
})

test("serialized clipboard payload is the same export object", () => {
  const data = buildPlayerExport({ player, dossier, ticketArchive, exportedAt: "2026-09-14T00:00:00.000Z" })

  assert.deepEqual(JSON.parse(serializePlayerExport(data)), data)
})
