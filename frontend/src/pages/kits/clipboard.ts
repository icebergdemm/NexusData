/** Общий буфер обмена для предметов кита.
 *
 *  Держим СВОЙ буфер, а не только системный: копирование и вставка нужны
 *  между карточками китов на одной странице, а чтение системного буфера
 *  (navigator.clipboard.readText) требует разрешения и в части браузеров
 *  недоступно вовсе — тогда «Вставить» просто не работало бы. Поэтому пишем
 *  в оба: системный буфер даёт перенос между вкладками и в чат, свой —
 *  гарантированную работу пункта меню и Ctrl+V.
 */
import { useSyncExternalStore } from "react"
import type { KitItemEntry } from "./kit-items"

const PAYLOAD_KIND = "nd-kit-items"

interface ClipboardPayload {
  kind: typeof PAYLOAD_KIND
  items: KitItemEntry[]
}

let buffer: KitItemEntry[] = []
const listeners = new Set<() => void>()

function emit() {
  for (const l of listeners) l()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function copyItemsToClipboard(items: KitItemEntry[]) {
  buffer = items.map((i) => ({ ...i }))
  emit()
  const payload: ClipboardPayload = { kind: PAYLOAD_KIND, items: buffer }
  void navigator.clipboard?.writeText(JSON.stringify(payload)).catch(() => {
    // Системный буфер недоступен (нет разрешения / небезопасный контекст) —
    // свой уже заполнен, вставка внутри панели продолжит работать.
  })
}

function parsePayload(text: string): KitItemEntry[] | null {
  try {
    const parsed = JSON.parse(text) as Partial<ClipboardPayload>
    if (parsed?.kind !== PAYLOAD_KIND || !Array.isArray(parsed.items)) return null
    return parsed.items
      .filter((i) => i && typeof i.identifier === "string")
      .map((i) => ({ identifier: i.identifier, quantity: Number(i.quantity) > 0 ? Number(i.quantity) : 1 }))
  } catch {
    return null
  }
}

/** Предметы для вставки: сначала системный буфер (мог быть скопирован в
 *  другой вкладке), при неудаче — свой. */
export async function readItemsFromClipboard(): Promise<KitItemEntry[]> {
  try {
    const text = await navigator.clipboard?.readText()
    const fromSystem = text ? parsePayload(text) : null
    if (fromSystem?.length) return fromSystem
  } catch {
    // читать системный буфер нельзя — молча используем свой
  }
  return buffer.map((i) => ({ ...i }))
}

/** Подписка для UI: пункт «Вставить» неактивен, пока копировать нечего. */
export function useClipboardHasItems(): boolean {
  return useSyncExternalStore(subscribe, () => buffer.length > 0, () => false)
}
