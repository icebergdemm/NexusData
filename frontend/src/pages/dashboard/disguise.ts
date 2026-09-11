export type DisguiseMode = "random" | "fixed"
export type DisguiseServerStatus = "pending" | "ready" | "failed"

export interface DisguiseServer {
  server_name?: string
  status: DisguiseServerStatus
}

export interface DisguiseProfile {
  id: number
  alias: string
  public_steam_id: string | null
  enabled: boolean
  sort_order: number
}

export interface DisguiseState {
  enabled: boolean
  mode: DisguiseMode
  fixed_alias: string | null
  selected_profile_id: number | null
  profiles: DisguiseProfile[]
  version: number
  servers: DisguiseServer[]
}

export function canEditDisguise(
  admin: { is_superuser: boolean } | undefined,
  linkedPlayer: { player_steamid?: string } | null | undefined,
): boolean {
  return admin?.is_superuser === true && Boolean(linkedPlayer?.player_steamid)
}

export function makeDisguiseUpdate(
  enabled: boolean,
  mode: DisguiseMode,
  fixedAlias: string,
  selectedProfileId: number | null = null,
  profiles: DisguiseProfile[] = [],
) {
  return {
    enabled,
    mode,
    fixed_alias: mode === "fixed" ? fixedAlias.trim() : null,
    selected_profile_id: selectedProfileId,
    profiles,
  }
}

export function disguiseStatusLabel(servers: DisguiseServer[]): string {
  if (servers.some((server) => server.status === "failed")) return "Ошибка синхронизации"
  if (servers.length > 0 && servers.every((server) => server.status === "ready")) return "Готово"
  return "Синхронизация ожидается"
}
