import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Plus, Minus, Trash2, Search, PackageSearch, Settings2, Infinity as InfinityIcon, Check } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { useItemCatalog, itemDisplayName, type ItemCatalog } from "@/lib/items"
import { warehouseApi, type WarehouseItemInput, type WarehouseItemRow } from "./warehouseApi"

/** Сколько ждать после последнего клика/ввода, прежде чем сохранить. Набрать
 * «+15» щелчками раньше означало пятнадцать запросов подряд, каждый из которых
 * блокировал кнопку до ответа сервера. */
const QUANTITY_COMMIT_DELAY_MS = 700

/** Item picker — multi-select, because stocking a warehouse means adding
 * twenty things at once, not opening this dialog twenty times. Any identifier
 * may also be typed by hand: the bundled catalog covers vanilla Barotrauma,
 * and a server running mods legitimately needs items that are not in it. */
function AddItemDialog({ open, onOpenChange, existingIdentifiers, catalog }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  existingIdentifiers: Set<string>
  catalog: ItemCatalog | undefined
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [customId, setCustomId] = useState("")

  const selectedIds = Object.keys(selected)
  const trimmedSearch = search.trim()

  const options = useMemo(() => {
    if (!catalog) return []
    const q = trimmedSearch.toLowerCase()
    return Object.entries(catalog)
      .filter(([id, entry]) => !existingIdentifiers.has(id)
        && (!q || entry.name.toLowerCase().includes(q) || id.toLowerCase().includes(q)))
      .sort((a, b) => a[1].name.localeCompare(b[1].name, "ru"))
  }, [catalog, trimmedSearch, existingIdentifiers])

  // Only the visible slice is rendered, but the count below reports the whole
  // match set, so a search that hits 300 entries no longer looks like 150.
  const visible = options.slice(0, 200)
  const hiddenCount = options.length - visible.length

  const reset = () => { setSelected({}); setSearch(""); setCustomId("") }

  const toggle = (id: string) => setSelected((current) => {
    if (id in current) {
      const next = { ...current }
      delete next[id]
      return next
    }
    return { ...current, [id]: "1" }
  })

  const addCustom = () => {
    const id = customId.trim()
    if (!id) return
    if (existingIdentifiers.has(id)) {
      toast.error("Этот предмет уже есть на складе")
      return
    }
    setSelected((current) => ({ ...current, [id]: "1" }))
    setCustomId("")
  }

  const addMutation = useMutation({
    mutationFn: () => {
      const items: WarehouseItemInput[] = selectedIds.map((identifier) => ({
        identifier,
        quantity: Math.max(0, Math.trunc(Number(selected[identifier]) || 0)),
      }))
      return warehouseApi.addMany(items)
    },
    onSuccess: () => {
      toast.success(selectedIds.length > 1
        ? `Добавлено на склад: ${selectedIds.length}`
        : "Добавлено на склад")
      queryClient.invalidateQueries({ queryKey: ["warehouse"] })
      onOpenChange(false)
      reset()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Добавить предметы на склад</DialogTitle>
          <DialogDescription>
            Отметьте нужные предметы — можно сразу несколько. Количество задаётся для каждого,
            настройки лимита — потом, на карточке предмета.
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по названию или ID…" className="pl-8" autoFocus />
        </div>

        <div className="max-h-56 overflow-y-auto rounded-md border">
          {visible.map(([id, entry]) => (
            <button
              key={id}
              type="button"
              onClick={() => toggle(id)}
              className="flex w-full items-center gap-2 border-b px-2.5 py-1.5 text-left text-sm last:border-b-0 hover:bg-accent"
            >
              <Checkbox checked={id in selected} className="pointer-events-none" tabIndex={-1} />
              <ItemIcon identifier={id} size={24} />
              <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{id}</span>
            </button>
          ))}
          {!visible.length && (
            <div className="py-6 text-center text-xs text-muted-foreground">
              В каталоге ничего не найдено — добавьте идентификатор вручную ниже
            </div>
          )}
          {hiddenCount > 0 && (
            <div className="border-t py-2 text-center text-[10px] text-muted-foreground">
              Показаны первые {visible.length} из {options.length} — уточните поиск
            </div>
          )}
        </div>

        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Label className="mb-1.5 block text-xs text-muted-foreground">
              Предмета нет в каталоге? Укажите идентификатор вручную
            </Label>
            <Input
              value={customId}
              onChange={(e) => setCustomId(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustom() } }}
              placeholder="например, mod_customitem"
              className="font-mono text-xs"
            />
          </div>
          <Button type="button" variant="outline" onClick={addCustom} disabled={!customId.trim()}>
            <Plus className="size-4" />
          </Button>
        </div>

        {selectedIds.length > 0 && (
          <div className="max-h-44 space-y-1.5 overflow-y-auto rounded-md border p-2">
            {selectedIds.map((id) => (
              <div key={id} className="flex items-center gap-2">
                <ItemIcon identifier={id} size={22} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs">{itemDisplayName(catalog, id)}</div>
                  <div className="truncate font-mono text-[9px] text-muted-foreground">{id}</div>
                </div>
                <Input
                  type="number"
                  min={0}
                  value={selected[id]}
                  onChange={(e) => setSelected((current) => ({ ...current, [id]: e.target.value }))}
                  className="h-7 w-20 text-xs"
                  aria-label={`Количество: ${itemDisplayName(catalog, id)}`}
                />
                <Button type="button" variant="ghost" size="icon-xs" onClick={() => toggle(id)}>
                  <Trash2 className="size-3.5 text-destructive" />
                </Button>
              </div>
            ))}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!selectedIds.length || addMutation.isPending} onClick={() => addMutation.mutate()}>
            {addMutation.isPending ? "Добавление…" : `Добавить${selectedIds.length ? ` (${selectedIds.length})` : ""}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Availability rules of one line: an item can be produced on demand, but then
 * a per-round ceiling is mandatory — that ceiling is exactly what the AI
 * dispatcher is shown as the available amount. */
function LimitsDialog({ item, open, onOpenChange, catalog }: {
  item: WarehouseItemRow | null
  open: boolean
  onOpenChange: (open: boolean) => void
  catalog: ItemCatalog | undefined
}) {
  const queryClient = useQueryClient()
  const [unlimited, setUnlimited] = useState(false)
  const [limited, setLimited] = useState(false)
  const [limit, setLimit] = useState("")

  useEffect(() => {
    if (!item) return
    setUnlimited(item.is_unlimited)
    setLimited(item.per_round_limit !== null)
    setLimit(item.per_round_limit !== null ? String(item.per_round_limit) : "")
  }, [item])

  const parsedLimit = Math.trunc(Number(limit))
  const limitRequired = unlimited
  const limitActive = unlimited || limited
  const limitInvalid = limitActive && (!Number.isFinite(parsedLimit) || parsedLimit < 1)

  const saveMutation = useMutation({
    mutationFn: () => warehouseApi.setLimits(
      item!.identifier,
      unlimited,
      limitActive ? parsedLimit : null,
    ),
    onSuccess: () => {
      toast.success("Настройки предмета сохранены")
      queryClient.invalidateQueries({ queryKey: ["warehouse"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!item) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{itemDisplayName(catalog, item.identifier)}</DialogTitle>
          <DialogDescription className="font-mono text-[11px]">{item.identifier}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="flex items-start justify-between gap-3 rounded-md border p-3">
            <div className="min-w-0">
              <Label htmlFor="wh-unlimited" className="text-sm">Бесконечный запас</Label>
              <p className="mt-1 text-xs text-muted-foreground">
                Остаток на складе не расходуется. Ограничивает только лимит на раунд — поэтому он обязателен.
              </p>
            </div>
            <Switch
              id="wh-unlimited"
              checked={unlimited}
              onCheckedChange={(value) => {
                setUnlimited(value)
                if (value) setLimited(true)
              }}
            />
          </div>

          <div className="space-y-2 rounded-md border p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <Label htmlFor="wh-limited" className="text-sm">Лимит выдачи за раунд</Label>
                <p className="mt-1 text-xs text-muted-foreground">
                  Считается отдельно для каждого сервера и обнуляется с началом нового раунда.
                  ИИ-диспетчер видит именно остаток лимита как доступное количество.
                </p>
              </div>
              <Switch
                id="wh-limited"
                checked={limitActive}
                disabled={limitRequired}
                onCheckedChange={setLimited}
              />
            </div>
            {limitActive && (
              <Input
                type="number"
                min={1}
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
                placeholder="Например, 20"
                aria-label="Лимит на раунд"
              />
            )}
            {limitInvalid && (
              <p className="text-xs text-destructive">Укажите целое число больше нуля</p>
            )}
          </div>

          {item.rounds && (
            <div className="space-y-1 rounded-md border p-3 text-xs">
              <div className="mb-1 font-medium">Выдано в текущем раунде</div>
              {Object.entries(item.rounds).map(([server, state]) => (
                <div key={server} className="flex items-center justify-between text-muted-foreground">
                  <span className="font-mono text-[11px]">{server}</span>
                  <span>
                    {state.issued}
                    {state.remaining !== null ? ` · осталось ${state.remaining}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={limitInvalid || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            {saveMutation.isPending ? "Сохранение…" : "Сохранить"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** One stock card. The amount is edited locally and written once the operator
 * stops changing it, so holding "+" is a single request instead of one per
 * click — and the number never jumps back while it is being edited. */
function WarehouseCard({ item, canEdit, catalog, onCommit, onRemove, onOpenLimits }: {
  item: WarehouseItemRow
  canEdit: boolean
  catalog: ItemCatalog | undefined
  onCommit: (identifier: string, quantity: number) => Promise<unknown>
  onRemove: (identifier: string) => void
  onOpenLimits: (item: WarehouseItemRow) => void
}) {
  const name = itemDisplayName(catalog, item.identifier)
  const [draft, setDraft] = useState(String(item.quantity))
  const dirtyRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [saving, setSaving] = useState(false)

  // A background refetch must not overwrite a number being edited right now.
  useEffect(() => {
    if (dirtyRef.current) return
    setDraft(String(item.quantity))
  }, [item.quantity])

  const commit = (value: number) => {
    setSaving(true)
    onCommit(item.identifier, value)
      .then(() => { dirtyRef.current = false })
      .finally(() => setSaving(false))
  }

  const schedule = (raw: string) => {
    dirtyRef.current = true
    setDraft(raw)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      const parsed = Math.max(0, Math.trunc(Number(raw) || 0))
      setDraft(String(parsed))
      commit(parsed)
    }, QUANTITY_COMMIT_DELAY_MS)
  }

  const step = (delta: number) => {
    const current = Math.max(0, Math.trunc(Number(draft) || 0))
    schedule(String(Math.max(0, current + delta)))
  }

  const flushNow = () => {
    if (!dirtyRef.current) return
    if (timerRef.current) clearTimeout(timerRef.current)
    const parsed = Math.max(0, Math.trunc(Number(draft) || 0))
    setDraft(String(parsed))
    commit(parsed)
  }

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  const rounds = item.rounds ?? {}
  const pending = dirtyRef.current || saving

  return (
    <Card className="overflow-hidden py-0">
      <CardContent className="flex flex-col items-center gap-2 p-3">
        <ItemIcon identifier={item.identifier} size={44} title={`${name}\nИдентификатор: ${item.identifier}`} />
        <div className="w-full text-center">
          <div className="truncate text-xs font-medium" title={name}>{name}</div>
          <div className="truncate font-mono text-[9px] text-muted-foreground">{item.identifier}</div>
        </div>

        <div className="flex flex-wrap items-center justify-center gap-1">
          {item.is_unlimited && (
            <Badge variant="outline" className="gap-1 px-1.5 py-0 text-[9px]">
              <InfinityIcon className="size-2.5" />бесконечный
            </Badge>
          )}
          {item.per_round_limit !== null && (
            <Badge variant="secondary" className="px-1.5 py-0 text-[9px]">
              лимит {item.per_round_limit}/раунд
            </Badge>
          )}
        </div>

        {canEdit ? (
          <div className="flex items-center gap-1">
            <Button
              size="icon-xs" variant="outline" onClick={() => step(-1)}
              disabled={item.is_unlimited || Number(draft) <= 0}
              aria-label="Уменьшить"
            >
              <Minus className="size-3" />
            </Button>
            <Input
              value={item.is_unlimited ? "∞" : draft}
              onChange={(e) => schedule(e.target.value)}
              onBlur={flushNow}
              disabled={item.is_unlimited}
              inputMode="numeric"
              className="h-7 w-16 px-1 text-center text-sm font-semibold"
              aria-label={`Количество: ${name}`}
            />
            <Button
              size="icon-xs" variant="outline" onClick={() => step(1)}
              disabled={item.is_unlimited}
              aria-label="Увеличить"
            >
              <Plus className="size-3" />
            </Button>
          </div>
        ) : (
          <div className="text-sm font-semibold">{item.is_unlimited ? "∞" : item.quantity}</div>
        )}

        {canEdit && (
          <div className="flex h-4 items-center text-[9px] text-muted-foreground">
            {saving ? "Сохранение…" : pending ? "Изменено" : (
              <span className="inline-flex items-center gap-0.5 opacity-0 transition-opacity">
                <Check className="size-2.5" />
              </span>
            )}
          </div>
        )}

        {Object.entries(rounds).some(([, state]) => state.remaining !== null || state.issued > 0) && (
          <div className="w-full space-y-0.5 text-[9px] text-muted-foreground">
            {Object.entries(rounds).map(([server, state]) => (
              <div key={server} className="flex justify-between">
                <span className="font-mono">{server}</span>
                <span>
                  выдано {state.issued}
                  {state.remaining !== null ? ` · доступно ${state.available}` : ""}
                </span>
              </div>
            ))}
          </div>
        )}

        {canEdit && (
          <div className="flex items-center gap-1">
            <Button
              size="icon-xs" variant="ghost"
              onClick={() => onOpenLimits(item)}
              title="Настройки предмета"
            >
              <Settings2 className="size-3.5" />
            </Button>
            <Button
              size="icon-xs" variant="ghost" className="text-destructive"
              onClick={() => onRemove(item.identifier)}
              title="Убрать со склада"
            >
              <Trash2 className="size-3.5" />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** Virtual dispatch resource warehouse — see routes/warehouse.py. Item
 * quantities the Диспетчерская can manage now; sending some of this stock
 * out with the supply shuttle is future work (see the "Отправить челнок
 * снабжения" stub action in dispatch/index.tsx) that will read from here. */
export function Warehouse() {
  const queryClient = useQueryClient()
  const { data: catalog } = useItemCatalog()
  const [addOpen, setAddOpen] = useState(false)
  const [limitsFor, setLimitsFor] = useState<WarehouseItemRow | null>(null)
  const [search, setSearch] = useState("")

  const { data } = useQuery({
    queryKey: ["warehouse"],
    queryFn: () => warehouseApi.list(),
    refetchInterval: 15000,
  })

  const setQuantity = (identifier: string, quantity: number) =>
    warehouseApi.setQuantity(identifier, quantity)
      .then((row) => {
        // Refresh sibling state (per-round counters) without fighting the
        // card that is still being edited.
        queryClient.invalidateQueries({ queryKey: ["warehouse"] })
        return row
      })
      .catch((e: Error) => {
        toast.error(e.message)
        queryClient.invalidateQueries({ queryKey: ["warehouse"] })
        throw e
      })

  const removeMutation = useMutation({
    mutationFn: (identifier: string) => warehouseApi.remove(identifier),
    onSuccess: () => { toast.success("Убрано со склада"); queryClient.invalidateQueries({ queryKey: ["warehouse"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const items = data?.items ?? []
  const filtered = items.filter((it) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return itemDisplayName(catalog, it.identifier).toLowerCase().includes(q) || it.identifier.toLowerCase().includes(q)
  })
  const existingIdentifiers = useMemo(() => new Set(items.map((it) => it.identifier)), [items])
  const current = limitsFor
    ? items.find((it) => it.identifier === limitsFor.identifier) ?? limitsFor
    : null

  return (
    <div className="flex h-[calc(100vh-13rem)] flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по складу…" className="pl-8" />
        </div>
        {data?.can_edit && (
          <Button onClick={() => setAddOpen(true)}><Plus className="mr-1 size-4" /> Добавить предмет</Button>
        )}
      </div>

      {!filtered.length ? (
        <Card className="flex-1"><CardContent className="flex h-full flex-col items-center justify-center gap-2 py-12 text-center text-sm text-muted-foreground">
          <PackageSearch className="size-6 opacity-40" />
          {items.length ? "Ничего не найдено" : "Склад пуст"}
        </CardContent></Card>
      ) : (
        <div className="grid auto-rows-min flex-1 grid-cols-2 content-start gap-2 overflow-y-auto pr-0.5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {filtered.map((it) => (
            <WarehouseCard
              key={it.identifier}
              item={it}
              canEdit={Boolean(data?.can_edit)}
              catalog={catalog}
              onCommit={setQuantity}
              onRemove={(identifier) => removeMutation.mutate(identifier)}
              onOpenLimits={setLimitsFor}
            />
          ))}
        </div>
      )}

      <AddItemDialog open={addOpen} onOpenChange={setAddOpen} existingIdentifiers={existingIdentifiers} catalog={catalog} />
      <LimitsDialog
        item={current}
        open={Boolean(limitsFor)}
        onOpenChange={(open) => { if (!open) setLimitsFor(null) }}
        catalog={catalog}
      />
    </div>
  )
}
