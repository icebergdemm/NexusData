import { api } from "@/lib/api"

export type DepartmentModule = "documentation" | "roster" | "rating" | "orders" | "test_server" | "chat" | "news"

export const MODULE_KEYS: DepartmentModule[] = [
  "documentation", "roster", "rating", "orders", "test_server", "chat", "news",
]

/** "roster" (Участники) and "news" (Новости) are always available for every
 * department and can't be disabled — only the remaining modules are
 * optional, superuser-toggleable tabs (item 12 / item 6). */
export const OPTIONAL_MODULE_KEYS: DepartmentModule[] = MODULE_KEYS.filter((m) => m !== "roster" && m !== "news")

export const MODULE_LABELS: Record<DepartmentModule, string> = {
  documentation: "Документация",
  roster: "Участники",
  rating: "Рейтинг",
  orders: "Приказы",
  test_server: "Тестовый сервер",
  chat: "Чат",
  news: "Новости",
}

export interface DepartmentAccessSummary {
  view: boolean
  edit: boolean
}

export interface DepartmentMemberRow {
  id: number
  admin_id: number
  admin_name: string | null
  admin_avatar: string | null
  // Admin-assigned in-game nickname of this member's linked profile, when
  // set — takes precedence over admin_name wherever a member's name shows.
  forced_display_name: string | null
  created_by_worker: boolean
  held_level_keys: string[]
  is_head: boolean
  is_deputy_head: boolean
  is_auto: boolean
  added_at: string | null
  // False when this employee holds (or would hold) a non-base access level
  // but has no linked in-game (Steam) profile — surfaced by the roster page
  // as a yellow-highlighted row with a "Указать SteamID" action.
  has_linked_profile: boolean
}

export interface DepartmentRow {
  id: number
  name: string
  slug: string
  description: string | null
  icon: string | null
  color: string | null
  base_discord_role_id: string | null
  base_discord_role_name: string | null
  enabled_modules: DepartmentModule[]
  pterodactyl_server_uuid: string | null
  orders_channel_id: string | null
  news_channel_id: string | null
  rating_channel_id: string | null
  // Superuser-only: validates manually-entered document-publish channel IDs
  // belong to this department (routes/documents.py's verify-channel).
  discord_category_id: string | null
  // Superuser-only link: is_head/is_deputy_head members auto-receive this
  // server privilege group (task_sync_auto_server_privileges).
  head_server_privilege_group_id: number | null
  member_count: number
  head: DepartmentMemberRow | null
  is_member: boolean
  is_head: boolean
  is_deputy_head: boolean
  my_access: Record<DepartmentModule, DepartmentAccessSummary>
  created_at: string | null
}

export interface DepartmentAccessLevel {
  id: number
  key: string
  name: string
  discord_role_id: string | null
  kind: "base" | "qualification" | "head" | "deputy_head"
  description: string | null
  sort_order: number
  can_bind_server_privilege: boolean
  permissions: { pages?: Record<string, Record<string, boolean>> }
  site_permissions: { pages?: Record<string, Record<string, boolean>> }
  kit_binding: { job_id: string; kit_level: number } | null
  // Superuser-only link: a member holding this level auto-receives this
  // server privilege group (task_sync_auto_server_privileges).
  server_privilege_group_id: number | null
}

export interface DepartmentDetail extends DepartmentRow {
  access_levels: DepartmentAccessLevel[]
}

export interface RatingLeaderboardRow {
  admin_id: number
  admin_name: string | null
  forced_display_name: string | null
  total: number
}

export interface RatingEntry {
  id: number
  admin_id: number
  admin_name: string | null
  forced_display_name: string | null
  delta: number
  reason: string | null
  category: string | null
  created_by: string | null
  created_at: string | null
}

export interface DepartmentReprimand {
  id: number
  admin_id: number
  reason: string
  issued_by: string | null
  created_at: string | null
}

export interface TestServerStatus {
  state: string | null
  cpu_percent: number | null
  memory_bytes: number | null
  memory_limit_bytes: number | null
  disk_bytes: number | null
}

export interface ChatChannel {
  id: number
  discord_channel_id: string
  label: string | null
}

export interface ChatMessage {
  id: number
  author_name: string | null
  author_avatar_url: string | null
  content: string | null
  sent_by_admin: string | null
  created_at: string | null
}

export interface DeptDocumentTag {
  id: number
  name: string
  color: string
}

export interface DeptDocumentFolder {
  id: number
  name: string
  parent_id: number | null
  // Documents inside this folder publish to Discord here by default.
  discord_channel_id: string | null
  // When true, this folder's OWN DepartmentFolderPermission grants also
  // apply to documents in its subfolders (live inheritance).
  auto_apply_permissions: boolean
}

export interface DeptFolderPermission {
  id: number
  grantee_type: "access_level" | "member"
  access_level_key: string | null
  member_admin_id: number | null
  member_name: string | null
  level: "view" | "edit"
}

export interface CreateDepartmentBody {
  name: string
  description?: string
  icon?: string
  color?: string
  base_discord_role_id?: string
  base_discord_role_name?: string
  enabled_modules?: DepartmentModule[]
}

export const departmentsApi = {
  list: () => api.get<{ departments: DepartmentRow[]; can_create: boolean }>("/api/v1/admin/departments"),
  get: (id: number) => api.get<DepartmentDetail>(`/api/v1/admin/departments/${id}`),
  create: (body: CreateDepartmentBody) => api.post<{ status: string; department: DepartmentRow }>("/api/v1/admin/departments", body),
  update: (id: number, body: Partial<CreateDepartmentBody> & { pterodactyl_server_uuid?: string; orders_channel_id?: string; news_channel_id?: string; rating_channel_id?: string; discord_category_id?: string; head_server_privilege_group_id?: number | null }) =>
    api.patch<{ status: string; department: DepartmentRow }>(`/api/v1/admin/departments/${id}`, body),
  archive: (id: number) => api.delete<{ status: string }>(`/api/v1/admin/departments/${id}`),

  accessLevels: {
    create: (departmentId: number, body: { name: string; kind?: "qualification" | "head" | "deputy_head"; discord_role_id?: string; description?: string; permissions?: unknown }) =>
      api.post<{ status: string; access_level: DepartmentAccessLevel }>(`/api/v1/admin/departments/${departmentId}/access-levels`, body),
    update: (departmentId: number, levelId: number, body: { name?: string; discord_role_id?: string; kind?: "qualification" | "head" | "deputy_head" | "base"; description?: string; permissions?: unknown; site_permissions?: unknown; kit_binding?: { job_id: string; kit_level: number } | null; server_privilege_group_id?: number | null; can_bind_server_privilege?: boolean }) =>
      api.patch<{ status: string; access_level: DepartmentAccessLevel }>(`/api/v1/admin/departments/${departmentId}/access-levels/${levelId}`, body),
    delete: (departmentId: number, levelId: number) =>
      api.delete<{ status: string }>(`/api/v1/admin/departments/${departmentId}/access-levels/${levelId}`),
    reorder: (departmentId: number, ordered_ids: number[]) =>
      api.post<{ status: string }>(`/api/v1/admin/departments/${departmentId}/access-levels/reorder`, { ordered_ids }),
  },

  documentFolders: {
    list: (departmentId: number) => api.get<{ folders: DeptDocumentFolder[] }>(`/api/v1/admin/departments/${departmentId}/document-folders`),
    create: (departmentId: number, body: { name: string; parent_id?: number | null; discord_channel_id?: string; auto_apply_permissions?: boolean }) =>
      api.post<{ status: string; folder: DeptDocumentFolder }>(`/api/v1/admin/departments/${departmentId}/document-folders`, body),
    rename: (departmentId: number, folderId: number, body: { name?: string; parent_id?: number | null; discord_channel_id?: string; auto_apply_permissions?: boolean }) =>
      api.patch<{ status: string; folder: DeptDocumentFolder }>(`/api/v1/admin/departments/${departmentId}/document-folders/${folderId}`, body),
    delete: (departmentId: number, folderId: number) =>
      api.delete<{ status: string }>(`/api/v1/admin/departments/${departmentId}/document-folders/${folderId}`),
    permissions: {
      list: (departmentId: number, folderId: number) =>
        api.get<{ permissions: DeptFolderPermission[] }>(`/api/v1/admin/departments/${departmentId}/document-folders/${folderId}/permissions`),
      grant: (departmentId: number, folderId: number, body: { grantee_type: "access_level" | "member"; access_level_key?: string; member_admin_id?: number; level: "view" | "edit" }) =>
        api.post<{ status: string; permission: DeptFolderPermission }>(`/api/v1/admin/departments/${departmentId}/document-folders/${folderId}/permissions`, body),
      revoke: (departmentId: number, folderId: number, permissionId: number) =>
        api.delete<{ status: string }>(`/api/v1/admin/departments/${departmentId}/document-folders/${folderId}/permissions/${permissionId}`),
    },
  },

  documentTags: {
    list: (departmentId: number) => api.get<{ tags: DeptDocumentTag[] }>(`/api/v1/admin/departments/${departmentId}/document-tags`),
    create: (departmentId: number, body: { name: string; color?: string }) =>
      api.post<{ status: string; tag: DeptDocumentTag }>(`/api/v1/admin/departments/${departmentId}/document-tags`, body),
    delete: (departmentId: number, tagId: number) =>
      api.delete<{ status: string }>(`/api/v1/admin/departments/${departmentId}/document-tags/${tagId}`),
  },

  members: {
    list: (departmentId: number) => api.get<{ members: DepartmentMemberRow[] }>(`/api/v1/admin/departments/${departmentId}/members`),
    add: (departmentId: number, body: { discord_id?: string; admin_id?: number; held_level_keys?: string[]; is_head?: boolean; is_deputy_head?: boolean; steamid?: string }) =>
      api.post<{ status: string; member: DepartmentMemberRow; auto_link: "linked" | "not_found" | "skipped" }>(`/api/v1/admin/departments/${departmentId}/members`, body),
    update: (departmentId: number, memberId: number, body: { held_level_keys?: string[]; is_head?: boolean; is_deputy_head?: boolean; steamid?: string }) =>
      api.patch<{ status: string; member: DepartmentMemberRow }>(`/api/v1/admin/departments/${departmentId}/members/${memberId}`, body),
    remove: (departmentId: number, memberId: number) =>
      api.delete<{ status: string }>(`/api/v1/admin/departments/${departmentId}/members/${memberId}`),
  },

  chat: {
    channels: (departmentId: number) => api.get<{ channels: ChatChannel[] }>(`/api/v1/admin/departments/${departmentId}/chat/channels`),
    addChannel: (departmentId: number, body: { discord_channel_id: string; label?: string }) =>
      api.post<{ status: string; channel: ChatChannel }>(`/api/v1/admin/departments/${departmentId}/chat/channels`, body),
    removeChannel: (departmentId: number, channelId: number) =>
      api.delete<{ status: string }>(`/api/v1/admin/departments/${departmentId}/chat/channels/${channelId}`),
    messages: (departmentId: number, channelId: number) =>
      api.get<{ messages: ChatMessage[] }>(`/api/v1/admin/departments/${departmentId}/chat/channels/${channelId}/messages`),
    send: (departmentId: number, channelId: number, content: string) =>
      api.post<{ status: string; message: ChatMessage }>(`/api/v1/admin/departments/${departmentId}/chat/channels/${channelId}/messages`, { content }),
  },

  testServer: {
    status: (departmentId: number) => api.get<TestServerStatus>(`/api/v1/admin/departments/${departmentId}/test-server/status`),
    power: (departmentId: number, action: "start" | "stop" | "restart" | "kill") =>
      api.post<{ status: string }>(`/api/v1/admin/departments/${departmentId}/test-server/power`, { action }),
  },

  rating: {
    leaderboard: (departmentId: number) => api.get<{ leaderboard: RatingLeaderboardRow[] }>(`/api/v1/admin/departments/${departmentId}/rating`),
    entries: (departmentId: number, adminId?: number) => {
      const qs = adminId ? `?admin_id=${adminId}` : ""
      return api.get<{ entries: RatingEntry[] }>(`/api/v1/admin/departments/${departmentId}/rating/entries${qs}`)
    },
    addEntry: (departmentId: number, body: { admin_id: number; delta: number; reason?: string; category?: string }) =>
      api.post<{ status: string; entry: RatingEntry }>(`/api/v1/admin/departments/${departmentId}/rating/entries`, body),
    deleteEntry: (departmentId: number, entryId: number) =>
      api.delete<{ status: string }>(`/api/v1/admin/departments/${departmentId}/rating/entries/${entryId}`),
  },

  reprimands: {
    list: (departmentId: number, adminId?: number) => {
      const qs = adminId ? `?admin_id=${adminId}` : ""
      return api.get<{ reprimands: DepartmentReprimand[] }>(`/api/v1/admin/departments/${departmentId}/reprimands${qs}`)
    },
    create: (departmentId: number, body: { admin_id: number; reason: string }) =>
      api.post<{ status: string; reprimand: DepartmentReprimand }>(`/api/v1/admin/departments/${departmentId}/reprimands`, body),
    delete: (departmentId: number, reprimandId: number) =>
      api.delete<{ status: string }>(`/api/v1/admin/departments/${departmentId}/reprimands/${reprimandId}`),
  },
}
