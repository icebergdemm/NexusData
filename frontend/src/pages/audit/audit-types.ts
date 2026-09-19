export interface AuditActor {
  type: string | null
  discord_id: string | null
  discord_name: string | null
  nexus_username: string | null
}

export interface AuditStructuredDetails {
  id?: number | null
  summary: string
  action: string
  target: { type: string; id: number | string; label?: string | null } | null
  changes: Array<{ field: string; from: unknown; to: unknown }>
  fields: Array<{ key: string; value: unknown }>
  reason: string | null
  actor: AuditActor
  context: {
    source: string
    auth_type: string | null
    session_id: number | null
    request_id: string | null
    source_ip?: string | null
    user_agent?: string | null
  }
  created_at: string | null
  outcome: string | null
  severity: string | null
  raw: Record<string, unknown> | null
}

export interface AuditLog {
  id: number
  admin_id: number | null
  admin_name: string | null
  admin_display_name: string | null
  actor?: AuditActor
  action: string
  details: Record<string, unknown> | null
  structured_details?: AuditStructuredDetails
  created_at: string | null
  auth_type?: string | null
  session_id?: number | null
  request_id?: string | null
  source_ip?: string | null
  user_agent?: string | null
  outcome?: string | null
  reason?: string | null
  severity?: string | null
}

export interface AuditSession {
  scope_key?: string
  session_id: number | null
  admin_id: number | null
  actor: AuditActor
  admin_name: string | null
  admin_display_name: string | null
  auth_type: string | null
  created_at: string | null
  expires_at: string | null
  last_seen_at: string | null
  first_event_at: string | null
  last_event_at: string | null
  event_count: number
  last_event: AuditStructuredDetails | null
  status: "active" | "revoked" | "expired" | "actor"
  is_current: boolean
}

export interface AuditResponse {
  logs: AuditLog[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface AuditSessionsResponse {
  sessions: AuditSession[]
  total: number
  page: number
  page_size: number
  total_pages: number
}
