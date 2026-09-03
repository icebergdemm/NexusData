export interface TicketRow {
  id: number
  ticket_type: string
  title: string
  status: string
  discord_channel_id: string | null
  creator_username: string | null
  creator_display_name: string | null
  player_id: number | null
  closed_by: string | null
  created_at: string | null
  closed_at: string | null
  deleted_at: string | null
  deleted_by: string | null
  last_message_at: string | null
  awaiting_admin: boolean
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
  can_edit: boolean
  can_assign: boolean
  can_delete: boolean
}

export interface TicketMessageRow {
  id: number
  author_discord_id: string
  author_username: string | null
  author_display_name: string | null
  author_avatar_url: string | null
  content: string | null
  attachments: { url: string; filename: string; content_type: string }[]
  is_bot: boolean
  created_at: string | null
}

export interface TicketDetailResponse {
  ticket: TicketRow
  player: { player_id: number; player_last_time_name: string; forced_display_name: string | null; player_steamid: string } | null
  messages: TicketMessageRow[]
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
