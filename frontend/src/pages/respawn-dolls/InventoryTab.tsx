/** Вкладка инвентаря: сетка слотов снаряжения как в игре плюс список
 *  «прочего», которое кукла получит в сумку/карманы.
 *
 *  Предмет выбирается из каталога с иконками (тот же static/item_catalog.json,
 *  что и на других страницах), а не вводом идентификатора руками —
 *  опечатка в identifier тихо теряет предмет при спавне.
 *
 *  Вложенность (патроны в магазине, магазин в сумке) поддержана рекурсивно:
 *  плагин принимает contained любой глубины, а без вложенности половину
 *  ивентовых наборов не собрать.
 */
import { useMemo, useState } from "react"
import { Plus, Trash2, ChevronRight, ChevronDown, Search } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { useItemCatalog, itemDisplayName, type ItemCatalog } from "@/lib/items"
import { EQUIP_SLOTS, QUALITY_LABELS, type InventoryItem } from "./types"

const MAX_RESULTS = 80

function ItemPicker({ catalog, onPick, children }: {
  catalog: ItemCatalog | undefined
  onPick: (identifier: string) => void
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  const results = useMemo(() => {
    const entries = Object.entries(catalog ?? {})
    const needle = query.trim().toLowerCase()
    const matched = needle
      ? entries.filter(([id, e]) => id.includes(needle) || e.name?.toLowerCase().includes(needle))
      : entries
    // Совпадение по началу названия — выше: при поиске «пис» пистолет должен
    // быть первым, а не «ремкомплект для пистолета».
    return matched
      .sort(([, a], [, b]) => {
        const an = (a.name || "").toLowerCase()
        const bn = (b.name || "").toLowerCase()
        if (needle) {
          const ap = an.startsWith(needle) ? 0 : 1
          const bp = bn.startsWith(needle) ? 0 : 1
          if (ap !== bp) return ap - bp
        }
        return an.localeCompare(bn)
      })
      .slice(0, MAX_RESULTS)
  }, [catalog, query])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent className="w-80 p-0" align="start">
        <div className="flex items-center gap-2 border-b px-2 py-1.5">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск предмета"
            className="w-full bg-transparent text-sm outline-none"
          />
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {results.map(([id, entry]) => (
            <button
              key={id}
              type="button"
              onClick={() => { onPick(id); setOpen(false); setQuery("") }}
              className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-accent"
            >
              <ItemIcon identifier={id} size={24} />
              <span className="min-w-0 flex-1 truncate">{entry.name || id}</span>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{id}</span>
            </button>
          ))}
          {!results.length && (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">Ничего не найдено</p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** Настройки одного предмета + его содержимое. Рекурсивна: contained
 *  редактируется тем же компонентом. */
function ItemRow({ item, catalog, onChange, onRemove, depth = 0 }: {
  item: InventoryItem
  catalog: ItemCatalog | undefined
  onChange: (next: InventoryItem) => void
  onRemove: () => void
  depth?: number
}) {
  const [expanded, setExpanded] = useState(false)
  const contained = item.contained ?? []
  const set = (patch: Partial<InventoryItem>) => onChange({ ...item, ...patch })

  return (
    <div className="rounded-md border">
      <div className="flex items-center gap-2 p-1.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="shrink-0 text-muted-foreground hover:text-foreground"
          title={expanded ? "Свернуть" : "Настроить"}
        >
          {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <ItemIcon identifier={item.identifier} size={28} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm">{itemDisplayName(catalog, item.identifier)}</div>
          <div className="truncate font-mono text-[10px] text-muted-foreground">
            {item.identifier}
            {(item.amount ?? 1) > 1 && ` ×${item.amount}`}
            {contained.length > 0 && ` · вложено: ${contained.length}`}
          </div>
        </div>
        <Button variant="ghost" size="icon-sm" title="Убрать" onClick={onRemove}>
          <Trash2 className="size-4 text-destructive" />
        </Button>
      </div>

      {expanded && (
        <div className="space-y-2 border-t bg-muted/30 p-2">
          <div className="grid grid-cols-3 gap-2">
            <div>
              <Label className="mb-1 block text-[11px]">Количество</Label>
              <Input
                type="number" min={1} max={100}
                value={item.amount ?? 1}
                onChange={(e) => set({ amount: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })}
                className="h-8"
              />
            </div>
            <div>
              <Label className="mb-1 block text-[11px]">Прочность, %</Label>
              <Input
                type="number" min={0} max={100}
                value={item.condition ?? 100}
                onChange={(e) => set({ condition: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })}
                className="h-8"
              />
            </div>
            <div>
              <Label className="mb-1 block text-[11px]">Качество</Label>
              <Select
                value={String(item.quality ?? 0)}
                onValueChange={(v) => set({ quality: Number(v) })}
              >
                <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {QUALITY_LABELS.map((q, i) => <SelectItem key={q} value={String(i)}>{q}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>

          {depth < 2 && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <Label className="text-[11px]">Внутри предмета</Label>
                <ItemPicker
                  catalog={catalog}
                  onPick={(id) => set({ contained: [...contained, { identifier: id, amount: 1 }] })}
                >
                  <Button variant="outline" size="sm" className="h-7">
                    <Plus className="mr-1 size-3" /> Вложить
                  </Button>
                </ItemPicker>
              </div>
              <div className="space-y-1">
                {contained.map((child, i) => (
                  <ItemRow
                    key={`${child.identifier}-${i}`}
                    item={child}
                    catalog={catalog}
                    depth={depth + 1}
                    onChange={(next) => set({
                      contained: contained.map((c, j) => (j === i ? next : c)),
                    })}
                    onRemove={() => set({
                      contained: contained.filter((_, j) => j !== i),
                    })}
                  />
                ))}
                {!contained.length && (
                  <p className="text-[11px] text-muted-foreground">
                    Пусто — сюда кладут патроны в магазин, батарейку в фонарь и т.п.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function InventoryTab({ items, onChange }: {
  items: InventoryItem[]
  onChange: (next: InventoryItem[]) => void
}) {
  const { data: catalog } = useItemCatalog()

  const replaceAt = (index: number, next: InventoryItem | null) => {
    onChange(next === null ? items.filter((_, i) => i !== index) : items.map((it, i) => (i === index ? next : it)))
  }

  // Предмет привязан к слоту полем slot; всё остальное кукла получает
  // «россыпью» — плагин разложит по карманам и сумке.
  const slotIndex = (slot: string) => items.findIndex((it) => it.slot === slot)
  const loose = items.map((it, i) => ({ it, i })).filter(({ it }) => !it.slot)

  return (
    <div className="space-y-4">
      <div>
        <Label className="mb-2 block text-xs">Снаряжение по слотам</Label>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
          {EQUIP_SLOTS.map(({ key, label }) => {
            const index = slotIndex(key)
            const item = index >= 0 ? items[index] : null
            return (
              <div key={key} className="flex flex-col items-center gap-1">
                <ItemPicker
                  catalog={catalog}
                  onPick={(id) => {
                    const next: InventoryItem = { identifier: id, amount: 1, slot: key }
                    onChange(index >= 0 ? items.map((it, i) => (i === index ? next : it)) : [...items, next])
                  }}
                >
                  <button
                    type="button"
                    title={item ? itemDisplayName(catalog, item.identifier) : `Выбрать предмет: ${label}`}
                    className={`flex aspect-square w-full items-center justify-center rounded-md border-2 border-dashed transition ${
                      item ? "border-solid border-primary/40 bg-primary/5" : "hover:border-primary/50 hover:bg-accent"
                    }`}
                  >
                    {item
                      ? <ItemIcon identifier={item.identifier} size={40} />
                      : <Plus className="size-5 text-muted-foreground" />}
                  </button>
                </ItemPicker>
                <span className="text-center text-[10px] leading-tight text-muted-foreground">{label}</span>
                {item && (
                  <button
                    type="button"
                    className="text-[10px] text-destructive underline"
                    onClick={() => replaceAt(index, null)}
                  >
                    убрать
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Настройки уже надетых вещей — количество/прочность/вложения. */}
      {items.some((it) => it.slot) && (
        <div className="space-y-1">
          <Label className="text-xs">Настройки надетого</Label>
          {items.map((it, i) => it.slot ? (
            <ItemRow
              key={`${it.slot}-${i}`}
              item={it}
              catalog={catalog}
              onChange={(next) => replaceAt(i, next)}
              onRemove={() => replaceAt(i, null)}
            />
          ) : null)}
        </div>
      )}

      <div>
        <div className="mb-2 flex items-center justify-between">
          <Label className="text-xs">
            В сумку и карманы
            {loose.length > 0 && <Badge variant="secondary" className="ml-2">{loose.length}</Badge>}
          </Label>
          <ItemPicker catalog={catalog} onPick={(id) => onChange([...items, { identifier: id, amount: 1 }])}>
            <Button variant="outline" size="sm">
              <Plus className="mr-1 size-4" /> Добавить предмет
            </Button>
          </ItemPicker>
        </div>
        <div className="space-y-1">
          {loose.map(({ it, i }) => (
            <ItemRow
              key={`${it.identifier}-${i}`}
              item={it}
              catalog={catalog}
              onChange={(next) => replaceAt(i, next)}
              onRemove={() => replaceAt(i, null)}
            />
          ))}
          {!loose.length && (
            <p className="rounded-md border border-dashed py-6 text-center text-sm text-muted-foreground">
              Пусто — кукла получит только то, что надето
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
