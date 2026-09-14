import type { AIDossierResponse, PlayerDetailResponse } from "./detail-types"
import type {
  TicketArchiveSummary,
  TicketMentionProfile,
  TicketMessageRow,
  TicketParticipant,
  TicketRow,
} from "../tickets/types"

export const PLAYER_EXPORT_GROUPS = [
  "profile",
  "activity",
  "history",
  "moderation",
  "tickets",
  "rewards",
  "ai_assessment",
] as const

export type PlayerExportGroup = typeof PLAYER_EXPORT_GROUPS[number]

export interface PlayerTicketExportRow {
  ticket: TicketRow
  messages: TicketMessageRow[]
  participants: TicketParticipant[]
  mention_profiles: Record<string, TicketMentionProfile>
  archive: TicketArchiveSummary
}

export interface PlayerTicketExportResponse {
  player_id: number
  ticket_count: number
  tickets: PlayerTicketExportRow[]
}

export type JsonValue = string | number | boolean | null | JsonObject | JsonValue[]
export interface JsonObject { [key: string]: JsonValue }

export interface PlayerPersonalFileExport {
  dossier_type: "player_personal_file"
  schema_version: 1
  exported_at: string
  subject: JsonObject
  profile?: JsonObject
  activity?: JsonObject
  history?: JsonObject
  moderation?: JsonObject
  tickets?: JsonValue[]
  rewards?: JsonObject
  ai_assessment?: JsonObject | null
}

export interface PlayerExportInput {
  player: PlayerDetailResponse
  dossier: AIDossierResponse | null
  ticketArchive: PlayerTicketExportResponse | null
  exportedAt: string
}

function cloneJson<T extends JsonValue>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function safeDossier(dossier: AIDossierResponse | null): JsonObject | null {
  if (!dossier) return null
  const { run, ...withoutRun } = dossier
  const safeRun = run
    ? Object.fromEntries(Object.entries(run).filter(([key]) => key !== "raw_response"))
    : run ?? null
  return cloneJson({ ...withoutRun, run: safeRun } as unknown as JsonObject)
}

export function buildPlayerExport(
  input: PlayerExportInput,
  includedGroups: ReadonlySet<PlayerExportGroup> = new Set(PLAYER_EXPORT_GROUPS),
): PlayerPersonalFileExport {
  const { player: detail, dossier, ticketArchive, exportedAt } = input
  const result: PlayerPersonalFileExport = {
    dossier_type: "player_personal_file",
    schema_version: 1,
    exported_at: exportedAt,
    subject: {
      player_id: detail.player.player_id,
      name: detail.player.player_last_time_name,
      profile_url: `/players/${detail.player.player_id}`,
    },
  }

  if (includedGroups.has("profile")) {
    result.profile = cloneJson(detail.player as unknown as JsonObject)
  }
  if (includedGroups.has("activity")) {
    result.activity = cloneJson({
      stats: detail.stats,
      kills: detail.kills,
      role_actions: detail.role_actions,
      role_stats: detail.role_stats,
    } as unknown as JsonObject)
  }
  if (includedGroups.has("history")) {
    result.history = cloneJson({
      names: detail.names,
      ips: detail.ips,
      sessions: detail.sessions,
    } as unknown as JsonObject)
  }
  if (includedGroups.has("moderation")) {
    result.moderation = cloneJson({
      punishments: detail.punishments,
      notes: detail.notes,
      labels: detail.label_pairs,
      traps: detail.traps,
    } as unknown as JsonObject)
  }
  if (includedGroups.has("tickets")) {
    result.tickets = cloneJson((ticketArchive?.tickets ?? []) as unknown as JsonValue[])
  }
  if (includedGroups.has("rewards")) {
    result.rewards = cloneJson({
      kval_kits: detail.kval_kits,
      custom_items: detail.custom_items,
      custom_job_items: detail.custom_job_items,
      custom_job_item_variations: detail.custom_job_item_variations,
      custom_stats: detail.custom_stats,
      custom_talents: detail.custom_talents,
    } as unknown as JsonObject)
  }
  if (includedGroups.has("ai_assessment")) {
    result.ai_assessment = safeDossier(dossier)
  }

  return result
}

export function serializePlayerExport(data: PlayerPersonalFileExport): string {
  return JSON.stringify(data, null, 2)
}
