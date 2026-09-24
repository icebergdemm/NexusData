import { useQuery } from "@tanstack/react-query"
import { api, setCsrfToken } from "@/lib/api"

export const PERMISSION_PAGES = [
  "admin", "players", "players_special", "moderation", "dispatch", "replays",
  "moderation_templates",
  "sessions", "audit", "privileges", "kits", "punishments", "sub_projects",
  "ratings", "discord_bot", "tickets", "analytics", "documents", "departments",
  "tasks", "world_state", "monster_spawn", "sounds", "respawn_dolls", "sub_checker", "achievements",
  "disguise",
] as const

export type PermissionPage = (typeof PERMISSION_PAGES)[number]

// Each page now registers its own action set (permissions_registry.py) —
// still always includes "view"/"edit" so every existing canView/canEdit
// call site keeps working, but a page may carry extra named actions too
// (e.g. moderation: "kick"/"ban"/"message").
export type PermissionMap = Record<string, Record<string, boolean>>

export interface MyDepartmentRef {
  id: number
  name: string
  icon: string | null
  color: string | null
  // Drives visibility of "Тестовый полигон ASPER" — Polygon access runs
  // entirely through this department module, never through a site-wide
  // permission page.
  test_server_view: boolean
  test_server_edit: boolean
}

export interface LinkedPlayerRef {
  player_id: number
  player_steamid: string
  forced_display_name: string | null
}

export interface CurrentAdmin {
  id: number
  discord_username: string | null
  discord_avatar: string | null
  is_superuser: boolean
  permissions: PermissionMap
  my_departments: MyDepartmentRef[]
  linked_player: LinkedPlayerRef | null
  csrf_token: string | null
}

export interface PermissionAction {
  key: string
  label: string
}

export interface PermissionPageDef {
  label: string
  actions: PermissionAction[]
}

export type PermissionRegistry = Record<string, PermissionPageDef>

export function useCurrentAdmin() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const me = await api.get<CurrentAdmin>("/api/v1/me")
      setCsrfToken(me.csrf_token)
      return me
    },
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: false,
  })
}

/** The full page/action registry (labels + which actions each page has) —
 * drives the group and individual permission editors instead of a
 * hardcoded view/edit/delete grid. */
export function usePermissionRegistry() {
  return useQuery({
    queryKey: ["permissions-registry"],
    queryFn: () => api.get<{ pages: PermissionRegistry }>("/api/v1/admin/permissions/registry"),
    staleTime: 30 * 60 * 1000,
  })
}

export function canView(admin: CurrentAdmin | undefined, page: PermissionPage): boolean {
  if (!admin) return false
  return admin.is_superuser || admin.permissions[page]?.view === true
}

export function canEdit(admin: CurrentAdmin | undefined, page: PermissionPage): boolean {
  if (!admin) return false
  return admin.is_superuser || admin.permissions[page]?.edit === true
}

/** Generic check for a custom (non-view/edit) action, e.g. canDo(admin, "moderation", "kick"). */
export function canDo(admin: CurrentAdmin | undefined, page: string, action: string): boolean {
  if (!admin) return false
  return admin.is_superuser || admin.permissions[page]?.[action] === true
}
