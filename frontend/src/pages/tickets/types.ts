export type TicketStatus = "open" | "waiting" | "closed" | (string & {})

export type TicketArchiveState = "pending" | "complete" | "partial" | "failed" | "legacy" | "incomplete" | (string & {})

export interface TicketAdminSummary {
  id: number
  discord_id: string | null
  display_name: string | null
  role: string | null
  avatar_url: string | null
}

export interface TicketParticipantPlayer {
  player_id: number
  player_last_time_name: string
  forced_display_name: string | null
  player_steamid: string
  avatar_url: string | null
  last_login_time: string | null
  online: boolean
  profile_url: string | null
}

export interface TicketParticipant {
  id?: number | string
  discord_id: string | null
  username: string | null
  display_name: string | null
  avatar_url: string | null
  role: "creator" | "participant" | "staff" | "assigned_admin" | (string & {})
  role_label?: string | null
  message_count?: number
  last_message_at?: string | null
  is_assigned?: boolean
  admin?: TicketAdminSummary | null
  player?: TicketParticipantPlayer | null
  player_id?: number | null
  profile_url?: string | null
  status?: "online" | "offline" | "in_game" | "unknown" | (string & {}) | null
  status_label?: string | null
  joined_at?: string | null
}

export interface TicketArchiveSummary {
  state: TicketArchiveState
  attachment_count: number
  stored_count?: number
  total_bytes: number
  failed_count: number
  legacy_count: number
}

export interface TicketPermissions {
  can_reply: boolean
  can_close: boolean
  can_assign: boolean
  can_delete: boolean
}

export interface TicketRow {
  id: number
  ticket_type: string
  title: string
  status: TicketStatus
  discord_channel_id: string | null
  creator_discord_id?: string | null
  creator_username: string | null
  creator_display_name: string | null
  creator_avatar_url?: string | null
  player_id: number | null
  assigned_admin?: TicketAdminSummary | null
  closed_by: string | null
  created_at: string | null
  closed_at: string | null
  deleted_at: string | null
  deleted_by: string | null
  last_message_at: string | null
  awaiting_admin: boolean
  last_message_preview?: string | null
  attachment_count?: number
  participant_count?: number
  unread_count?: number
  archive_state?: TicketArchiveState | null
}

export interface TicketTypeMeta {
  name: string
  emoji: string
  color: string
}

export interface TicketsListResponse {
  tickets: TicketRow[]
  status_filter: string
  ticket_config: { types: Record<string, TicketTypeMeta> }
  permissions?: Partial<TicketPermissions>
  can_edit: boolean
  can_assign: boolean
  can_delete: boolean
}

export interface TicketAttachment {
  id?: number | string | null
  filename: string
  content_type: string | null
  file_size?: number | null
  url: string | null
  download_url?: string | null
  archive_status?: string | null
  archive_error?: string | null
  archived?: boolean
}

export interface TicketEmbed {
  title?: string | null
  description?: string | null
  url?: string | null
  color?: string | null
  author_name?: string | null
  footer_text?: string | null
  image_url?: string | null
  thumbnail_url?: string | null
}

export interface TicketReferenceSource {
  guild_id?: string | null
  channel_id?: string | null
  message_id?: string | null
  channel_name?: string | null
  jump_url?: string | null
}

export interface TicketReferenceAuthor {
  discord_id?: string | null
  username?: string | null
  display_name?: string | null
  avatar_url?: string | null
}

export interface TicketReferenceSnapshot {
  kind: "reply" | "forward" | (string & {})
  source?: TicketReferenceSource | string | null
  author?: TicketReferenceAuthor | null
  content?: string | null
  created_at?: string | null
  attachments?: TicketAttachment[]
  embeds?: TicketEmbed[]
}

export interface TicketMessageRow {
  id: number
  discord_message_id?: string | null
  author_discord_id: string
  author_username: string | null
  author_display_name: string | null
  author_avatar_url: string | null
  content: string | null
  attachments: TicketAttachment[]
  reference_snapshot?: TicketReferenceSnapshot | null
  embeds?: TicketEmbed[]
  is_bot: boolean
  created_at: string | null
  edited_at?: string | null
  delivery_status?: "received" | "queued" | "sent" | "failed" | (string & {}) | null
  delivery_error?: string | null
}

export interface TicketMessagePollResponse {
  messages: TicketMessageRow[]
  delivery_updates: Array<Pick<TicketMessageRow, "id" | "delivery_status" | "delivery_error">>
  ticket_update: Pick<TicketRow, "status" | "archive_state" | "closed_by" | "closed_at" | "last_message_at" | "awaiting_admin">
  has_more: boolean
}

export interface TicketMentionProfile {
  discord_id: string
  username: string | null
  display_name: string
  avatar_url: string | null
  player_id: number | null
  profile_url: string | null
  is_staff: boolean
}

export interface TicketPlayerSummary {
  player_id: number
  player_last_time_name: string
  forced_display_name: string | null
  player_steamid: string
  avatar_url?: string | null
  online?: boolean
  steam_avatar_url?: string | null
  is_online?: boolean
}

export interface TicketDetailResponse {
  ticket: TicketRow
  player: TicketPlayerSummary | null
  messages: TicketMessageRow[]
  participants?: TicketParticipant[]
  mention_profiles?: Record<string, TicketMentionProfile>
  archive?: TicketArchiveSummary | null
  permissions?: Partial<TicketPermissions>
  ticket_config: { types: Record<string, TicketTypeMeta> }
  can_edit: boolean
  can_assign: boolean
  can_delete: boolean
}

export interface TicketMember {
  discord_id: string
  username: string | null
  display_name: string | null
  avatar_url: string | null
  joined_at: string | null
}

export interface TicketMembersResponse {
  total: number
  members: TicketMember[]
}
