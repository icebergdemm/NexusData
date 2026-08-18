/** Модель состава кита для визуального редактора.
 *
 *  В БД `items` кита — это JSON-объект `{item_id: количество}` со
 *  спец-ключами `ar_delete` / `talents` / `stats` (см. серверный разбор в
 *  services_kit_resolution.py::_flatten_items — он итерирует именно объект).
 *  Визуально редактируются только сами предметы и `ar_delete`; всё
 *  остальное (таланты, статы, вложенные контейнеры вида
 *  `{item: {nested: {...}}}`) переносится из исходного JSON без изменений,
 *  чтобы редактор ничего молча не терял.
 */

export interface KitItemEntry {
  identifier: string
  quantity: number
}

export interface KitItemsModel {
  items: KitItemEntry[]
  /** ar_delete — предметы, которые квал-кит убирает из базового набора. */
  removals: string[]
  /** talents / stats / нередактируемые визуально значения — как есть. */
  extras: Record<string, unknown>
  /** Форма, в которой состав лежал в БД. Сохраняем в ней же: часть китов
   *  хранится объектом `{item_id: количество}`, часть — легаси-массивом
   *  идентификаторов, и менять форму на ровном месте нельзя — её читает и
   *  игровой сервер, и резолвер китов (services_kit_resolution.py). */
  shape: "object" | "array"
}

const SPECIAL_KEYS = new Set(["ar_delete", "talents", "stats"])

function addItem(items: KitItemEntry[], identifier: string, quantity: number) {
  const found = items.find((i) => i.identifier === identifier)
  if (found) found.quantity += quantity
  else items.push({ identifier, quantity })
}

/** Разбирает JSON состава кита. Возвращает null, если текст — не JSON или не
 *  та форма, которую редактор умеет показать (тогда остаётся режим JSON). */
export function parseKitItems(json: string): KitItemsModel | null {
  const text = json.trim()
  if (!text) return { items: [], removals: [], extras: {}, shape: "object" }

  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return null
  }

  const model: KitItemsModel = { items: [], removals: [], extras: {}, shape: "object" }

  // Легаси-форма: массив идентификаторов, по одному предмету на элемент.
  // Повторы схлопываются в количество и при сохранении разворачиваются
  // обратно в массив — форма кита остаётся прежней.
  if (Array.isArray(raw)) {
    model.shape = "array"
    for (const v of raw) {
      if (typeof v !== "string") return null
      addItem(model.items, v, 1)
    }
    return model
  }

  if (!raw || typeof raw !== "object") return null

  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (key === "ar_delete") {
      if (Array.isArray(value)) model.removals.push(...value.filter((v): v is string => typeof v === "string"))
      else if (value && typeof value === "object") model.removals.push(...Object.keys(value))
      else model.extras[key] = value
      continue
    }
    if (SPECIAL_KEYS.has(key)) {
      model.extras[key] = value
      continue
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      addItem(model.items, key, value)
      continue
    }
    // Вложенные контейнеры и всё нечисловое — визуально не редактируем,
    // но и не выбрасываем.
    model.extras[key] = value
  }
  return model
}

/** Собирает JSON обратно в исходной форме кита. Порядок ключей: предметы →
 *  ar_delete → остальное, чтобы диффы между сохранениями оставались
 *  читаемыми. */
export function serializeKitItems(model: KitItemsModel): string {
  if (model.shape === "array") {
    // В массиве количество выражается повторами — как оно и лежало раньше.
    const list: string[] = []
    for (const it of model.items) {
      for (let i = 0; i < Math.max(0, Math.trunc(it.quantity)); i++) list.push(it.identifier)
    }
    return JSON.stringify(list, null, 2)
  }
  const out: Record<string, unknown> = {}
  for (const it of model.items) {
    if (it.quantity > 0) out[it.identifier] = it.quantity
  }
  if (model.removals.length) out.ar_delete = [...new Set(model.removals)]
  for (const [k, v] of Object.entries(model.extras)) {
    if (k === "ar_delete" && model.removals.length) continue
    out[k] = v
  }
  return JSON.stringify(out, null, 2)
}

export function totalItemCount(model: KitItemsModel): number {
  return model.items.reduce((sum, i) => sum + Math.max(0, i.quantity), 0)
}
