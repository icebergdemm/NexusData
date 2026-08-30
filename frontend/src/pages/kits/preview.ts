/** Mirrors the item-preview logic from the original templates/admin/kits.html
 * Jinja/JS rendering, so base and qualification kits show the same chip
 * breakdown instead of a raw JSON blob. */

export function baseKitItemsList(items: unknown): string[] {
  if (items && typeof items === "object" && !Array.isArray(items)) {
    // Обычная форма — {item_id: количество}: показываем идентификатор (с xN
    // при количестве больше одного), а не голое значение. Раньше здесь
    // печатались именно ЗНАЧЕНИЯ, то есть на карточке кита вместо предметов
    // был ряд чисел «1, 1, 2». Легаси-форма {ключ: "item_id"} со строковыми
    // значениями по-прежнему показывается значением.
    return Object.entries(items as Record<string, unknown>).map(([k, v]) =>
      typeof v === "number" ? (v !== 1 ? `${k} x${v}` : k) : String(v),
    )
  }
  if (Array.isArray(items)) return items.map((v) => String(v))
  return []
}

export interface QualItemChip { key: string; label: string; isDelete: boolean }

export function qualKitChips(items: unknown): { chips: QualItemChip[]; normCount: number; delCount: number } {
  if (!items || typeof items !== "object" || Array.isArray(items)) return { chips: [], normCount: 0, delCount: 0 }
  const obj = items as Record<string, unknown>
  const chips: QualItemChip[] = []
  let delCount = 0

  for (const [k, v] of Object.entries(obj)) {
    if (k === "ar_delete") {
      if (v && typeof v === "object") {
        const entries = Array.isArray(v) ? v.map((x) => [x, 1] as const) : Object.entries(v as Record<string, unknown>)
        for (const [dk, dv] of entries) {
          delCount++
          chips.push({ key: `del-${dk}`, label: `${dk}${dv !== 1 ? ` x${dv}` : ""}`, isDelete: true })
        }
      }
    } else {
      const isNested = v && typeof v === "object"
      const label = isNested ? `${k} [nested]` : (v !== 1 ? `${k} x${v}` : k)
      chips.push({ key: k, label, isDelete: false })
    }
  }
  const normCount = Object.keys(obj).length - ("ar_delete" in obj ? 1 : 0)
  return { chips, normCount, delCount }
}
