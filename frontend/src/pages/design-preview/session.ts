import type { QueryClient } from "@tanstack/react-query"
import type { CurrentAdmin } from "@/lib/auth"

export const DESIGN_PREVIEW_PATH = "design-preview"

/** Matches /v2/design-preview and /admin/design-preview, with or without a trailing slash. */
export function isDesignPreviewPath(pathname: string): boolean {
  return /\/design-preview\/?$/.test(pathname)
}

/** A superuser, so the sidebar shows every section the real shell can have. */
const PREVIEW_ADMIN: CurrentAdmin = {
  id: 0,
  discord_username: "Design preview",
  discord_avatar: null,
  is_superuser: true,
  permissions: {},
  my_departments: [],
  // Linked and named, so the first-login onboarding wizard stays closed.
  linked_player: { player_id: 0, player_steamid: "0", forced_display_name: "Preview" },
  csrf_token: null,
}

/**
 * Development only (called behind `import.meta.env.DEV`): seeds a mock session
 * when the dev server opens the design preview, so the V2 shell renders with
 * no backend and no login. `staleTime: Infinity` keeps React Query from ever
 * asking the missing backend for the real session.
 */
export function installDesignPreviewSession(queryClient: QueryClient) {
  if (!isDesignPreviewPath(window.location.pathname)) return
  queryClient.setQueryDefaults(["me"], { staleTime: Infinity, retry: false })
  queryClient.setQueryData(["me"], PREVIEW_ADMIN)
}
