import { api } from "@/lib/api"

export interface WarehouseItemRow {
  id: number
  identifier: string
  quantity: number
  updated_at: string | null
  updated_by: string | null
}

const base = "/api/v1/admin/warehouse"

export const warehouseApi = {
  list: () => api.get<{ items: WarehouseItemRow[]; can_edit: boolean }>(base),
  upsert: (identifier: string, quantity: number) =>
    api.post<WarehouseItemRow>(`${base}/items`, { identifier, quantity }),
  adjust: (identifier: string, delta: number) =>
    api.patch<WarehouseItemRow>(`${base}/items/${encodeURIComponent(identifier)}`, { delta }),
  setQuantity: (identifier: string, quantity: number) =>
    api.patch<WarehouseItemRow>(`${base}/items/${encodeURIComponent(identifier)}`, { quantity }),
  remove: (identifier: string) => api.delete(`${base}/items/${encodeURIComponent(identifier)}`),
}
