import { api } from "@/lib/api"

/** Per-round state of one stock line on one server. */
export interface WarehouseRoundState {
  issued: number
  /** null — предмет без лимита на раунд. */
  remaining: number | null
  available: number
}

export interface WarehouseItemRow {
  id: number
  identifier: string
  /** Русское название из каталога предметов — тот же справочник, что и у
   * useItemCatalog (см. lib/items.ts). Возвращается сервером напрямую, но
   * страница склада уже резолвит имя через useItemCatalog на клиенте, так
   * что это поле пока не задействовано в интерфейсе. */
  name: string
  quantity: number
  /** Предмет выдаётся всегда; ограничен только лимитом на раунд. */
  is_unlimited: boolean
  per_round_limit: number | null
  rounds?: Record<string, WarehouseRoundState>
  updated_at: string | null
  updated_by: string | null
}

export interface WarehouseItemInput {
  identifier: string
  quantity: number
  is_unlimited?: boolean
  per_round_limit?: number | null
}

const base = "/api/v1/admin/warehouse"

export const warehouseApi = {
  list: () => api.get<{ items: WarehouseItemRow[]; servers: string[]; can_edit: boolean }>(base),
  /** Одним запросом можно завести сразу несколько позиций. */
  addMany: (items: WarehouseItemInput[]) =>
    api.post<{ items: WarehouseItemRow[] }>(`${base}/items`, { items }),
  adjust: (identifier: string, delta: number) =>
    api.patch<WarehouseItemRow>(`${base}/items/${encodeURIComponent(identifier)}`, { delta }),
  setQuantity: (identifier: string, quantity: number) =>
    api.patch<WarehouseItemRow>(`${base}/items/${encodeURIComponent(identifier)}`, { quantity }),
  setLimits: (identifier: string, is_unlimited: boolean, per_round_limit: number | null) =>
    api.patch<WarehouseItemRow>(`${base}/items/${encodeURIComponent(identifier)}`, {
      is_unlimited,
      per_round_limit,
    }),
  remove: (identifier: string) => api.delete(`${base}/items/${encodeURIComponent(identifier)}`),
}
