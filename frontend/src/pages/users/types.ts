import type { PermissionValue } from "./PermissionMatrixEditor"

export interface LinkedPlayer { player_id: number; player_name: string; auto: boolean }

export interface AdminUserRow {
  id: number; discord_id: string; discord_username: string | null; role: string
  is_superuser: boolean; privilege_group_id: number | null; privilege_group_name: string | null
  individual_permissions: { pages?: PermissionValue } | PermissionValue | null
  linked_player: LinkedPlayer | null
  permission_summary: { department_count: number; conflict_count: number; effective: Record<string, boolean> }
}

export interface PrivilegeGroup {
  id: number; name: string; description: string | null
  permissions: { pages?: PermissionValue } | PermissionValue; member_count: number
}

export interface UsersResponse {
  admins: AdminUserRow[]; roles: string[]; groups: PrivilegeGroup[]
  can_edit: boolean; can_delete: boolean; is_superuser: boolean
}

export interface PermissionDecision {
  value: boolean
  state: "allow" | "deny" | "unset"
  sources: Array<{ kind: string; value: boolean; [key: string]: unknown }>
}

export interface AuthorizationTrace {
  admin: { id: number; discord_id: string; discord_username: string | null; discord_avatar: string | null; role: string; is_superuser: boolean; policy_mode: string }
  effective: { pages: Record<string, Record<string, PermissionDecision>> }
  sources: { individual: { pages?: PermissionValue } | PermissionValue; group: { id: number; name: string; permissions: unknown } | null; department_site: unknown[]; department_module: unknown[] }
  departments: Array<{ id: number; name: string; held_level_keys: string[]; is_head: boolean; is_deputy_head: boolean; is_auto: boolean; levels: Array<{ id: number; key: string; name: string; kind: string; held: boolean; permissions: unknown; site_permissions: unknown }> }>
  linked_player: { player_id: number; name: string } | null
  login_keys: Array<{ id: number; prefix: string; label: string | null; revoked: boolean; expires_at: string | null; use_count: number }>
  resource_acl: { documents: unknown[]; task_boards: unknown[] }
}

export function unwrapPages(raw: { pages?: PermissionValue } | PermissionValue | null | undefined): PermissionValue {
  if (!raw) return {}
  return (raw as { pages?: PermissionValue }).pages ?? (raw as PermissionValue) ?? {}
}
