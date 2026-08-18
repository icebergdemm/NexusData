/** Визуальный редактор состава кита — та же сетка карточек с иконками, что и
 *  на складе Диспетчерской (pages/dispatch/Warehouse.tsx), плюс копирование и
 *  вставка предметов: правой кнопкой мыши или Ctrl+C / Ctrl+X / Ctrl+V.
 *  Работает поверх того же JSON-значения, что и текстовый режим, поэтому
 *  между «Визуально» и «JSON» можно переключаться в любой момент. */
import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"
import {
  Plus, Minus, Trash2, Search, PackageSearch, Copy, ClipboardPaste, Scissors, CopyPlus,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { AppContextMenu, type ContextMenuAction } from "@/components/layout/AppContextMenu"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { useItemCatalog, itemDisplayName, type ItemCatalog } from "@/lib/items"
import { copyItemsToClipboard, readItemsFromClipboard, useClipboardHasItems } from "./clipboard"
import {
  parseKitItems, serializeKitItems, totalItemCount, type KitItemEntry, type KitItemsModel,
} from "./kit-items"

/** Выбор предмета из каталога Barotrauma — тот же приём, что и в диалоге
 *  склада: фильтрация списка на клиенте, каталог всего в несколько сотен
 *  записей и грузится один раз за сессию (lib/items.ts). */
function AddItemDialog({ open, onOpenChange, catalog, onAdd }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  catalog: ItemCatalog | undefined
  onAdd: (identifier: string, quantity: number) => void
}) {
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<string | null>(null)
  const [quantity, setQuantity] = useState("1")

  const options = useMemo(() => {
    if (!catalog) return []
    const q = search.trim().toLowerCase()
    return Object.entries(catalog)
      .filter(([id, entry]) => !q || entry.name.toLowerCase().includes(q) || id.toLowerCase().includes(q))
      .sort((a, b) => a[1].name.localeCompare(b[1].name, "ru"))
      .slice(0, 150)
  }, [catalog, search])

  const reset = () => { setSelected(null); setSearch(""); setQuantity("1") }
  const confirm = () => {
    if (!selected) return
    onAdd(selected, Math.max(1, Number(quantity) || 1))
    onOpenChange(false)
    reset()
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset() }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Добавить предмет в кит</DialogTitle>
          <DialogDescription>
            Предмет из каталога Barotrauma. Нет в списке — введите идентификатор вручную и нажмите Enter.
          </DialogDescription>
        </DialogHeader>

        {!selected ? (
          <>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && search.trim()) setSelected(search.trim()) }}
                placeholder="Поиск по названию или ID…"
                className="pl-8"
                autoFocus
              />
            </div>
            <div className="max-h-72 overflow-y-auto rounded-md border">
              {options.map(([id, entry]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setSelected(id)}
                  className="flex w-full items-center gap-2 border-b px-2.5 py-1.5 text-left text-sm last:border-b-0 hover:bg-accent"
                >
                  <ItemIcon identifier={id} size={24} />
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{id}</span>
                </button>
              ))}
              {!options.length && (
                <div className="py-6 text-center text-xs text-muted-foreground">
                  Ничего не найдено — Enter добавит «{search.trim()}» как идентификатор
                </div>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center gap-3 rounded-md border p-3">
              <ItemIcon identifier={selected} size={36} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{itemDisplayName(catalog, selected)}</div>
                <div className="truncate font-mono text-[10px] text-muted-foreground">{selected}</div>
              </div>
              <Button type="button" variant="ghost" size="sm" onClick={() => setSelected(null)}>Изменить</Button>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Количество</label>
              <Input
                type="number" min={1} value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") confirm() }}
              />
            </div>
          </>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button type="button" disabled={!selected} onClick={confirm}>Добавить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function KitItemsEditor({ value, onChange }: {
  /** JSON состава кита — тот же самый, что редактируется в режиме «JSON». */
  value: string
  onChange: (json: string) => void
}) {
  const { data: catalog } = useItemCatalog()
  const [addOpen, setAddOpen] = useState(false)
  const [selection, setSelection] = useState<string[]>([])
  const clipboardHasItems = useClipboardHasItems()

  const model = useMemo(() => parseKitItems(value), [value])

  const apply = useCallback((next: KitItemsModel) => onChange(serializeKitItems(next)), [onChange])

  const items = useMemo(() => model?.items ?? [], [model])
  const removals = model?.removals ?? []

  // Выделение чистим от предметов, которых уже нет (удалили, отменили правку).
  useEffect(() => {
    setSelection((sel) => {
      const next = sel.filter((id) => items.some((i) => i.identifier === id))
      return next.length === sel.length ? sel : next
    })
  }, [items])

  const mutate = useCallback((fn: (m: KitItemsModel) => void) => {
    if (!model) return
    const next: KitItemsModel = {
      items: model.items.map((i) => ({ ...i })),
      removals: [...model.removals],
      extras: { ...model.extras },
      shape: model.shape,
    }
    fn(next)
    next.items = next.items.filter((i) => i.quantity > 0)
    apply(next)
  }, [model, apply])

  const addItems = useCallback((incoming: KitItemEntry[]) => {
    if (!incoming.length) return
    mutate((m) => {
      for (const inc of incoming) {
        const found = m.items.find((i) => i.identifier === inc.identifier)
        if (found) found.quantity += inc.quantity
        else m.items.push({ ...inc })
      }
    })
  }, [mutate])

  const removeItems = useCallback((identifiers: string[]) => {
    const drop = new Set(identifiers)
    mutate((m) => { m.items = m.items.filter((i) => !drop.has(i.identifier)) })
    setSelection((sel) => sel.filter((id) => !drop.has(id)))
  }, [mutate])

  const adjust = useCallback((identifier: string, delta: number) => {
    mutate((m) => {
      const found = m.items.find((i) => i.identifier === identifier)
      if (found) found.quantity += delta
    })
  }, [mutate])

  /** Что копируем при действии над карточкой: всё выделение, если карточка
   *  входит в него, иначе — только саму карточку (привычное поведение
   *  файловых менеджеров). */
  const targetsFor = useCallback((identifier: string): KitItemEntry[] => {
    const ids = selection.includes(identifier) ? selection : [identifier]
    return items.filter((i) => ids.includes(i.identifier)).map((i) => ({ ...i }))
  }, [items, selection])

  const copy = useCallback((entries: KitItemEntry[]) => {
    if (!entries.length) return
    copyItemsToClipboard(entries)
    toast.success(entries.length === 1
      ? `Скопирован предмет: ${itemDisplayName(catalog, entries[0].identifier)}`
      : `Скопировано предметов: ${entries.length}`)
  }, [catalog])

  const cut = useCallback((entries: KitItemEntry[]) => {
    if (!entries.length) return
    copyItemsToClipboard(entries)
    removeItems(entries.map((e) => e.identifier))
    toast.success(`Вырезано предметов: ${entries.length}`)
  }, [removeItems])

  const paste = useCallback(async () => {
    const incoming = await readItemsFromClipboard()
    if (!incoming.length) {
      toast.error("Буфер пуст — сначала скопируйте предметы")
      return
    }
    addItems(incoming)
    toast.success(`Вставлено предметов: ${incoming.length}`)
  }, [addItems])

  const toggleSelection = (identifier: string, additive: boolean) => {
    setSelection((sel) => {
      if (additive) return sel.includes(identifier) ? sel.filter((i) => i !== identifier) : [...sel, identifier]
      return sel.length === 1 && sel[0] === identifier ? [] : [identifier]
    })
  }

  // Горячие клавиши работают, пока фокус внутри сетки, — иначе Ctrl+C на
  // странице с несколькими китами не знал бы, из какого копировать.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!(e.ctrlKey || e.metaKey)) {
      if (e.key === "Delete" && selection.length) {
        e.preventDefault()
        removeItems(selection)
      }
      return
    }
    const key = e.key.toLowerCase()
    if (key === "c" && selection.length) {
      e.preventDefault()
      copy(items.filter((i) => selection.includes(i.identifier)))
    } else if (key === "x" && selection.length) {
      e.preventDefault()
      cut(items.filter((i) => selection.includes(i.identifier)))
    } else if (key === "v") {
      e.preventDefault()
      void paste()
    } else if (key === "a" && items.length) {
      e.preventDefault()
      setSelection(items.map((i) => i.identifier))
    }
  }

  if (!model) {
    return (
      <div className="rounded-md border border-warning/50 p-3 text-xs text-muted-foreground">
        Этот состав нельзя показать визуально (некорректный JSON или непривычная структура) —
        отредактируйте его в режиме «JSON», и визуальный режим снова заработает.
      </div>
    )
  }

  const gridActions: ContextMenuAction[] = [
    { label: "Добавить предмет…", icon: Plus, onSelect: () => setAddOpen(true) },
    { label: "Вставить (Ctrl+V)", icon: ClipboardPaste, disabled: !clipboardHasItems, onSelect: () => void paste() },
    { label: "Копировать весь кит", icon: CopyPlus, separatorBefore: true, disabled: !items.length, onSelect: () => copy(items) },
    { label: "Очистить кит", icon: Trash2, destructive: true, separatorBefore: true, disabled: !items.length, onSelect: () => removeItems(items.map((i) => i.identifier)) },
  ]

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => setAddOpen(true)}>
          <Plus className="size-3.5" /> Добавить предмет
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={!clipboardHasItems} onClick={() => void paste()}>
          <ClipboardPaste className="size-3.5" /> Вставить
        </Button>
        {!!items.length && (
          <Button type="button" size="sm" variant="ghost" onClick={() => copy(items)}>
            <CopyPlus className="size-3.5" /> Копировать всё
          </Button>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {items.length} позиций · {totalItemCount(model)} шт.
          {selection.length ? ` · выделено: ${selection.length}` : ""}
        </span>
      </div>

      <AppContextMenu actions={gridActions}>
        <div
          tabIndex={0}
          onKeyDown={onKeyDown}
          onClick={(e) => { if (e.target === e.currentTarget) setSelection([]) }}
          className="grid max-h-72 grid-cols-2 content-start gap-2 overflow-y-auto rounded-md border p-2 outline-none focus-visible:ring-2 focus-visible:ring-ring/50 sm:grid-cols-3"
        >
          {items.map((it) => {
            const name = itemDisplayName(catalog, it.identifier)
            const isSelected = selection.includes(it.identifier)
            const actions: ContextMenuAction[] = [
              { label: "Копировать (Ctrl+C)", icon: Copy, onSelect: () => copy(targetsFor(it.identifier)) },
              { label: "Вырезать (Ctrl+X)", icon: Scissors, onSelect: () => cut(targetsFor(it.identifier)) },
              { label: "Вставить (Ctrl+V)", icon: ClipboardPaste, disabled: !clipboardHasItems, onSelect: () => void paste() },
              { label: "＋1 к количеству", icon: Plus, separatorBefore: true, onSelect: () => adjust(it.identifier, 1) },
              { label: "−1 к количеству", icon: Minus, disabled: it.quantity <= 1, onSelect: () => adjust(it.identifier, -1) },
              { label: "Удалить (Del)", icon: Trash2, destructive: true, separatorBefore: true, onSelect: () => removeItems(targetsFor(it.identifier).map((t) => t.identifier)) },
            ]
            return (
              <AppContextMenu key={it.identifier} actions={actions}>
                <div
                  role="button"
                  tabIndex={-1}
                  onClick={(e) => toggleSelection(it.identifier, e.ctrlKey || e.metaKey || e.shiftKey)}
                  // Правый клик по невыделенной карточке сначала выделяет её —
                  // иначе меню действовало бы не на ту карточку, по которой кликнули.
                  onContextMenu={() => { if (!selection.includes(it.identifier)) setSelection([it.identifier]) }}
                  className={`flex cursor-pointer items-center gap-2 rounded-md border p-2 text-left transition-colors ${
                    isSelected ? "border-primary bg-primary/10" : "hover:bg-accent"
                  }`}
                  title={`${name}\nИдентификатор: ${it.identifier}`}
                >
                  <ItemIcon identifier={it.identifier} size={32} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium">{name}</div>
                    <div className="truncate font-mono text-[9px] text-muted-foreground">{it.identifier}</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-0.5">
                    <Button
                      type="button" size="icon-xs" variant="ghost"
                      disabled={it.quantity <= 1}
                      onClick={(e) => { e.stopPropagation(); adjust(it.identifier, -1) }}
                    >
                      <Minus className="size-3" />
                    </Button>
                    <span className="w-6 text-center text-xs font-semibold">{it.quantity}</span>
                    <Button
                      type="button" size="icon-xs" variant="ghost"
                      onClick={(e) => { e.stopPropagation(); adjust(it.identifier, 1) }}
                    >
                      <Plus className="size-3" />
                    </Button>
                    <Button
                      type="button" size="icon-xs" variant="ghost" className="text-destructive"
                      onClick={(e) => { e.stopPropagation(); removeItems([it.identifier]) }}
                    >
                      <Trash2 className="size-3" />
                    </Button>
                  </div>
                </div>
              </AppContextMenu>
            )
          })}
          {!items.length && (
            <div className="col-span-full flex flex-col items-center gap-1 py-8 text-center text-xs text-muted-foreground">
              <PackageSearch className="size-5 opacity-40" />
              Предметов нет. Добавьте их кнопкой выше или вставьте скопированные (правая кнопка мыши / Ctrl+V).
            </div>
          )}
        </div>
      </AppContextMenu>

      {!!removals.length && (
        <div className="rounded-md border border-destructive/40 p-2">
          <div className="mb-1 text-[11px] font-medium text-muted-foreground">
            Удаляются из базового набора (ar_delete)
          </div>
          <div className="flex flex-wrap gap-1">
            {removals.map((id) => (
              <Badge key={id} variant="outline" className="gap-1 border-destructive/40 text-destructive">
                {itemDisplayName(catalog, id)}
                <button
                  type="button"
                  className="hover:text-foreground"
                  onClick={() => mutate((m) => { m.removals = m.removals.filter((r) => r !== id) })}
                >
                  ✕
                </button>
              </Badge>
            ))}
          </div>
        </div>
      )}

      {!!Object.keys(model.extras).length && (
        <p className="text-[11px] text-muted-foreground">
          Кроме предметов, в этом составе заданы: <code>{Object.keys(model.extras).join(", ")}</code> —
          они сохраняются без изменений, редактировать их можно в режиме «JSON».
        </p>
      )}

      <AddItemDialog open={addOpen} onOpenChange={setAddOpen} catalog={catalog} onAdd={(id, qty) => addItems([{ identifier: id, quantity: qty }])} />
    </div>
  )
}
