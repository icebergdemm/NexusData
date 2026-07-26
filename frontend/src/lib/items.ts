/**
 * Site-wide Barotrauma item catalog — identifier -> {name, has_icon}.
 * Backed by routes/world_state.py's GET /api/v1/admin/items/catalog, which
 * just serves static/item_catalog.json (generated offline by
 * tools/extract_barotrauma_items.py). Icons themselves are plain files at
 * /static/item_icons/{identifier}.png (itemIconUrl below) — no dedicated
 * icon endpoint, they're served by the app's existing static file mount.
 *
 * Deliberately NOT scoped to any one page — first consumer is "Обзор мира"'s
 * character inventory popup, but any future page that shows an item
 * identifier (logs, replays, moderation) can reuse this.
 */
import { useQuery } from "@tanstack/react-query"
import { api } from "./api"

export interface ItemCatalogEntry {
  name: string
  has_icon: boolean
}

export type ItemCatalog = Record<string, ItemCatalogEntry>

export const itemsApi = {
  getCatalog: () => api.get<ItemCatalog>("/api/v1/admin/items/catalog"),
}

/** Static reference data — fetched once per session, never refetched. */
export function useItemCatalog() {
  return useQuery({
    queryKey: ["item-catalog"],
    queryFn: () => itemsApi.getCatalog(),
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

export function itemIconUrl(identifier: string): string {
  return `/static/item_icons/${encodeURIComponent(identifier)}.png`
}

/** Best-effort display name for an item identifier — falls back to the raw
 * identifier (prettified) when the catalog hasn't loaded yet or doesn't
 * know this identifier (e.g. a modded item added after the last extraction
 * run — see tools/extract_barotrauma_items.py). */
export function itemDisplayName(catalog: ItemCatalog | undefined, identifier: string): string {
  const known = catalog?.[identifier]?.name
  if (known) return known
  return identifier.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}
