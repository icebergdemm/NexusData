import { getPlayerFilterDefaults, type ActivityFilter, type FilterMode, type PlayerFilterState, type PlayerSort, type PlayerSortKey, type SortDirection } from "./player-filters"

const STORAGE_KEY = "nexusdata.player-filters.v2"
const SORT_STORAGE_KEY = "nexusdata.player-sorts.v1"
const FILTER_MODES: readonly FilterMode[] = ["all", "with", "without"]
const ACTIVITY_MODES: readonly ActivityFilter[] = ["all", "new", "inactive_7", "inactive_30", "inactive_90"]

function oneOf<T extends string>(value: unknown, values: readonly T[], fallback: T): T {
  return typeof value === "string" && values.includes(value as T) ? value as T : fallback
}

export function parsePlayerFilterState(raw: string | null): PlayerFilterState {
  const defaults = getPlayerFilterDefaults()
  if (!raw) return defaults
  try {
    const value = JSON.parse(raw) as Record<string, unknown>
    const oldLabelId = typeof value.labelId === "string" && value.labelId !== "all" ? [value.labelId] : []
    const rawLabelIds = Array.isArray(value.labelIds) ? value.labelIds : oldLabelId
    const labelIds = rawLabelIds.filter((id): id is string => typeof id === "string" && /^\d+$/.test(id))
    const labelMode = oneOf(value.labelMode ?? value.labelsMode, ["all", "with", "without", "any"] as const, defaults.labelMode)
    const normalizedLabelMode = labelMode === "any" || (labelMode === "all" && labelIds.length > 0) ? "any" : labelMode
    return {
      discordFilter: oneOf(value.discordFilter, ["all", "linked", "unlinked"] as const, defaults.discordFilter),
      labelMode: normalizedLabelMode === "any" && labelIds.length === 0 ? "all" : normalizedLabelMode,
      labelIds: normalizedLabelMode === "any" ? labelIds : [],
      rankId: typeof value.rankId === "string" ? value.rankId : defaults.rankId,
      notesMode: oneOf(value.notesMode, FILTER_MODES, defaults.notesMode),
      sanctionsMode: oneOf(value.sanctionsMode, FILTER_MODES, defaults.sanctionsMode),
      anomaliesMode: oneOf(value.anomaliesMode, FILTER_MODES, defaults.anomaliesMode),
      nameHistoryMode: oneOf(value.nameHistoryMode, FILTER_MODES, defaults.nameHistoryMode),
      activityMode: oneOf(value.activityMode, ACTIVITY_MODES, defaults.activityMode),
    }
  } catch {
    return defaults
  }
}

export function loadPlayerFilterState(): PlayerFilterState {
  if (typeof window === "undefined") return getPlayerFilterDefaults()
  try {
    return parsePlayerFilterState(window.localStorage.getItem(STORAGE_KEY))
  } catch {
    return getPlayerFilterDefaults()
  }
}

export function savePlayerFilterState(state: PlayerFilterState): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // Private browsing and disabled storage must not break the player list.
  }
}

export function clearPlayerFilterState(): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Storage is optional; the in-memory reset still works.
  }
}

export function loadPlayerSort(view: string, fallback: PlayerSort, allowedKeys: readonly PlayerSortKey[]): PlayerSort {
  if (typeof window === "undefined") return { ...fallback }
  try {
    const raw = window.localStorage.getItem(SORT_STORAGE_KEY)
    const saved = raw ? JSON.parse(raw) as Record<string, { key?: unknown; direction?: unknown }> : {}
    const value = saved[view]
    const key = typeof value?.key === "string" && allowedKeys.includes(value.key as PlayerSortKey) ? value.key as PlayerSortKey : fallback.key
    const direction: SortDirection = value?.direction === "asc" || value?.direction === "desc" ? value.direction : fallback.direction
    return { key, direction }
  } catch {
    return { ...fallback }
  }
}

export function savePlayerSort(view: string, sort: PlayerSort): void {
  if (typeof window === "undefined") return
  try {
    const raw = window.localStorage.getItem(SORT_STORAGE_KEY)
    const saved = raw ? JSON.parse(raw) as Record<string, PlayerSort> : {}
    saved[view] = sort
    window.localStorage.setItem(SORT_STORAGE_KEY, JSON.stringify(saved))
  } catch {
    // Sorting remains usable when browser storage is unavailable.
  }
}
