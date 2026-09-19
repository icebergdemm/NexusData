import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Plus, Minus, Trash2, Search, PackageSearch, Settings2, Infinity as InfinityIcon, Check, Image as ImageIcon, Dices } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"
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

type CatalogScope = "new" | "all" | "existing"
type CatalogSort = "name" | "identifier"

/** Everything the dialog needs to know about one item being added. The limit
 * settings live here too: configuring twenty fresh lines one card at a time,
 * after the dialog had already closed, was the slow part. */
interface ItemDraft {
  quantity: string
  unlimited: boolean
  limited: boolean
  random: boolean
  limit: string
  limitMin: string
}

function emptyDraft(quantity = "1"): ItemDraft {
  return { quantity, unlimited: false, limited: false, random: false, limit: "", limitMin: "0" }
}

/** Same rules the server enforces (see services_warehouse.validate_limit_settings),
 * checked here so the operator is not told about a typo only after sending
 * twenty items. */
function draftError(draft: ItemDraft): string | null {
  const quantity = Math.trunc(Number(draft.quantity))
  if (!Number.isFinite(quantity) || quantity < 0) return "Количество — целое число не меньше нуля"
  const limitActive = draft.unlimited || draft.limited
  if (!limitActive) return null
  const limit = Math.trunc(Number(draft.limit))
  if (!Number.isFinite(limit) || limit < 1) return "Лимит на раунд — целое число больше нуля"
  if (draft.random) {
    const min = Math.trunc(Number(draft.limitMin))
    if (!Number.isFinite(min) || min < 0 || min > limit) return "Нижняя граница — от нуля до верхней"
  }
  return null
}

function draftToInput(identifier: string, draft: ItemDraft): WarehouseItemInput {
  const limitActive = draft.unlimited || draft.limited
  return {
    identifier,
    quantity: Math.max(0, Math.trunc(Number(draft.quantity) || 0)),
    is_unlimited: draft.unlimited,
    per_round_limit: limitActive ? Math.trunc(Number(draft.limit)) : null,
    per_round_limit_min: limitActive && draft.random ? Math.trunc(Number(draft.limitMin)) : null,
  }
}

/** Per-round availability controls, shared by the per-item block and the
 * "apply to everything selected" block above it. */
function LimitFields({ draft, onChange, idPrefix }: {
  draft: ItemDraft
  onChange: (patch: Partial<ItemDraft>) => void
  idPrefix: string
}) {
  const limitActive = draft.unlimited || draft.limited
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={`${idPrefix}-unlimited`} className="text-[11px] font-normal">
          Бесконечный запас
        </Label>
        <Switch
          id={`${idPrefix}-unlimited`}
          checked={draft.unlimited}
          onCheckedChange={(value) => onChange(
            // Без лимита бесконечный предмет не имеет смысла — сервер такое
            // сочетание всё равно отклонит.
            value ? { unlimited: true, limited: true } : { unlimited: false },
          )}
        />
      </div>
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={`${idPrefix}-limited`} className="text-[11px] font-normal">
          Лимит выдачи за раунд
        </Label>
        <Switch
          id={`${idPrefix}-limited`}
          checked={limitActive}
          disabled={draft.unlimited}
          onCheckedChange={(value) => onChange({ limited: value })}
        />
      </div>
      {limitActive && (
        <>
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor={`${idPrefix}-random`} className="text-[11px] font-normal">
              Случайный, от и до
            </Label>
            <Switch
              id={`${idPrefix}-random`}
              checked={draft.random}
              onCheckedChange={(value) => onChange({ random: value })}
            />
          </div>
          <div className="flex items-center gap-1.5">
            {draft.random && (
              <Input
                type="number" min={0} value={draft.limitMin}
                onChange={(e) => onChange({ limitMin: e.target.value })}
                className="h-7 text-xs" placeholder="от" aria-label="Нижняя граница лимита"
              />
            )}
            <Input
              type="number" min={1} value={draft.limit}
              onChange={(e) => onChange({ limit: e.target.value })}
              className="h-7 text-xs"
              placeholder={draft.random ? "до" : "лимит"}
              aria-label={draft.random ? "Верхняя граница лимита" : "Лимит на раунд"}
            />
          </div>
        </>
      )}
    </div>
  )
}

/** Item picker — a full-height two-pane workspace rather than a narrow modal:
 * stocking a warehouse means scanning a 900-entry catalog and taking twenty
 * things out of it, which a six-row list made painful.
 *
 * The bundled catalog only knows an item's name and whether it has an icon
 * (static/item_catalog.json), so there is no category data to filter by —
 * every filter here is backed by something that is actually in the data.
 * Any identifier may also be typed by hand: a server running mods needs items
 * the vanilla catalog has never heard of. */
function AddItemDialog({ open, onOpenChange, existingIdentifiers, catalog }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  existingIdentifiers: Set<string>
  catalog: ItemCatalog | undefined
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [scope, setScope] = useState<CatalogScope>("new")
  const [sort, setSort] = useState<CatalogSort>("name")
  const [iconsOnly, setIconsOnly] = useState(false)
  const [selected, setSelected] = useState<Record<string, ItemDraft>>({})
  const [expanded, setExpanded] = useState<string | null>(null)
  const [customId, setCustomId] = useState("")
  const [bulk, setBulk] = useState<ItemDraft>(emptyDraft(""))

  const selectedIds = Object.keys(selected)
  const trimmedSearch = search.trim()
  const errors = selectedIds
    .map((id) => [id, draftError(selected[id])] as const)
    .filter(([, error]) => error !== null)

  const options = useMemo(() => {
    if (!catalog) return []
    const q = trimmedSearch.toLowerCase()
    const rows = Object.entries(catalog).filter(([id, entry]) => {
      const known = existingIdentifiers.has(id)
      if (scope === "new" && known) return false
      if (scope === "existing" && !known) return false
      if (iconsOnly && !entry.has_icon) return false
      if (!q) return true
      return entry.name.toLowerCase().includes(q) || id.toLowerCase().includes(q)
    })
    rows.sort(([idA, a], [idB, b]) => (
      sort === "name" ? a.name.localeCompare(b.name, "ru") : idA.localeCompare(idB)
    ))
    return rows
  }, [catalog, trimmedSearch, existingIdentifiers, scope, sort, iconsOnly])

  // Only a slice is rendered, but the count reports the whole match set so a
  // search that hits 300 entries never silently looks like 200.
  const visible = options.slice(0, 300)
  const hiddenCount = options.length - visible.length

  const reset = () => {
    setSelected({}); setSearch(""); setCustomId(""); setBulk(emptyDraft(""))
    setScope("new"); setSort("name"); setIconsOnly(false); setExpanded(null)
  }

  /** New picks inherit whatever is configured in the "for everything" block,
   * so a batch of twenty identical lines is set up once. */
  const draftFromBulk = (): ItemDraft => ({
    ...bulk,
    quantity: bulk.quantity.trim() || "1",
  })

  const patchDraft = (id: string, patch: Partial<ItemDraft>) => setSelected((current) => (
    current[id] ? { ...current, [id]: { ...current[id], ...patch } } : current
  ))

  const toggle = (id: string) => setSelected((current) => {
    if (id in current) {
      const next = { ...current }
      delete next[id]
      return next
    }
    return { ...current, [id]: draftFromBulk() }
  })

  const addCustom = () => {
    const id = customId.trim()
    if (!id) return
    if (existingIdentifiers.has(id)) {
      toast.error("Этот предмет уже есть на складе")
      return
    }
    setSelected((current) => ({ ...current, [id]: draftFromBulk() }))
    setCustomId("")
  }

  const applyBulk = () => {
    if (!selectedIds.length) return
    setSelected((current) => Object.fromEntries(
      Object.entries(current).map(([id, draft]) => [id, {
        ...bulk,
        quantity: bulk.quantity.trim() || draft.quantity,
      }]),
    ))
  }

  const addMutation = useMutation({
    mutationFn: () => warehouseApi.addMany(
      selectedIds.map((identifier) => draftToInput(identifier, selected[identifier])),
    ),
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
      <DialogContent className="flex h-[85vh] max-h-[85vh] flex-col gap-3 sm:max-w-5xl">
        <DialogHeader className="shrink-0">
          <DialogTitle>Добавить предметы на склад</DialogTitle>
          <DialogDescription>
            Отметьте нужные предметы — можно сразу несколько. Количество и лимиты задаются справа,
            для каждого предмета или сразу для всех.
          </DialogDescription>
        </DialogHeader>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <div className="relative min-w-52 flex-1">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск по названию или ID…"
              className="pl-8"
              autoFocus
            />
          </div>
          <Select value={scope} onValueChange={(value) => setScope(value as CatalogScope)}>
            <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="new">Которых нет на складе</SelectItem>
              <SelectItem value="all">Все предметы</SelectItem>
              <SelectItem value="existing">Уже на складе</SelectItem>
            </SelectContent>
          </Select>
          <Select value={sort} onValueChange={(value) => setSort(value as CatalogSort)}>
            <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="name">Сортировка: название</SelectItem>
              <SelectItem value="identifier">Сортировка: ID</SelectItem>
            </SelectContent>
          </Select>
          <Button
            type="button"
            variant={iconsOnly ? "default" : "outline"}
            size="sm"
            onClick={() => setIconsOnly((value) => !value)}
            title="Скрыть служебные записи без иконки"
          >
            <ImageIcon className="mr-1 size-3.5" />С иконкой
          </Button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <div className="flex min-h-0 flex-col rounded-md border">
            <div className="flex shrink-0 items-center justify-between border-b px-2.5 py-1.5 text-[11px] text-muted-foreground">
              <span>Найдено: {options.length}</span>
              {hiddenCount > 0 && <span>показаны первые {visible.length} — уточните поиск</span>}
            </div>
            <div className="grid min-h-0 flex-1 auto-rows-min grid-cols-2 content-start gap-1 overflow-y-auto p-1.5 sm:grid-cols-3 xl:grid-cols-4">
              {visible.map(([id, entry]) => {
                const known = existingIdentifiers.has(id)
                const checked = id in selected
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => { if (!known) toggle(id) }}
                    disabled={known}
                    title={known ? "Уже на складе" : `${entry.name}\n${id}`}
                    className={cn(
                      "flex items-center gap-2 rounded-md border p-1.5 text-left transition-colors",
                      checked ? "border-primary bg-primary/10" : "hover:bg-accent",
                      known && "opacity-40",
                    )}
                  >
                    <Checkbox checked={checked} disabled={known} className="pointer-events-none shrink-0" tabIndex={-1} />
                    <ItemIcon identifier={id} size={26} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs">{entry.name}</span>
                      <span className="block truncate font-mono text-[9px] text-muted-foreground">{id}</span>
                    </span>
                  </button>
                )
              })}
              {!visible.length && (
                <div className="col-span-full py-10 text-center text-xs text-muted-foreground">
                  Ничего не найдено — измените фильтр или добавьте идентификатор вручную
                </div>
              )}
            </div>
          </div>

          <div className="flex min-h-0 flex-col gap-2">
            <div className="flex shrink-0 items-center justify-between text-xs">
              <span className="font-medium">Выбрано: {selectedIds.length}</span>
              {selectedIds.length > 0 && (
                <Button type="button" variant="ghost" size="sm" className="h-6 px-2 text-[11px]" onClick={() => setSelected({})}>
                  Очистить
                </Button>
              )}
            </div>

            <div className="shrink-0 space-y-2 rounded-md border p-2">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium">Настройки для всех выбранных</span>
                <Button
                  type="button" variant="outline" size="sm" className="h-6 px-2 text-[11px]"
                  onClick={applyBulk} disabled={!selectedIds.length}
                >
                  Применить
                </Button>
              </div>
              <div>
                <Label className="mb-1 block text-[11px] text-muted-foreground">Количество</Label>
                <Input
                  type="number" min={0} value={bulk.quantity}
                  onChange={(e) => setBulk((current) => ({ ...current, quantity: e.target.value }))}
                  placeholder="например, 10" className="h-7 text-xs"
                />
              </div>
              <LimitFields
                draft={bulk}
                idPrefix="wh-bulk"
                onChange={(patch) => setBulk((current) => ({ ...current, ...patch }))}
              />
              <p className="text-[10px] text-muted-foreground">
                Эти настройки получают и новые отметки — отмечайте предметы после того, как задали их.
              </p>
            </div>

            <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto rounded-md border p-2">
              {selectedIds.map((id) => {
                const draft = selected[id]
                const error = draftError(draft)
                const isOpen = expanded === id
                const limitActive = draft.unlimited || draft.limited
                return (
                  <div key={id} className={cn("rounded-md border p-1.5", error && "border-destructive/50")}>
                    <div className="flex items-center gap-2">
                      <ItemIcon identifier={id} size={22} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs">{itemDisplayName(catalog, id)}</div>
                        <div className="truncate font-mono text-[9px] text-muted-foreground">{id}</div>
                      </div>
                      <Input
                        type="number" min={0} value={draft.quantity}
                        disabled={draft.unlimited}
                        onChange={(e) => patchDraft(id, { quantity: e.target.value })}
                        className="h-7 w-16 text-xs"
                        aria-label={`Количество: ${itemDisplayName(catalog, id)}`}
                      />
                      <Button
                        type="button" variant={isOpen ? "default" : "ghost"} size="icon-xs"
                        onClick={() => setExpanded(isOpen ? null : id)}
                        title="Настройки лимита"
                      >
                        <Settings2 className="size-3.5" />
                      </Button>
                      <Button type="button" variant="ghost" size="icon-xs" onClick={() => toggle(id)}>
                        <Trash2 className="size-3.5 text-destructive" />
                      </Button>
                    </div>

                    {!isOpen && (draft.unlimited || limitActive) && (
                      <div className="mt-1 flex flex-wrap gap-1 pl-7">
                        {draft.unlimited && (
                          <Badge variant="outline" className="gap-1 px-1.5 py-0 text-[9px]">
                            <InfinityIcon className="size-2.5" />бесконечный
                          </Badge>
                        )}
                        {limitActive && draft.limit && (
                          <Badge variant="secondary" className="gap-1 px-1.5 py-0 text-[9px]">
                            {draft.random && <Dices className="size-2.5" />}
                            лимит {draft.random ? `${draft.limitMin || 0}–${draft.limit}` : draft.limit}/раунд
                          </Badge>
                        )}
                      </div>
                    )}

                    {isOpen && (
                      <div className="mt-2 border-t pt-2">
                        <LimitFields
                          draft={draft}
                          idPrefix={`wh-${id}`}
                          onChange={(patch) => patchDraft(id, patch)}
                        />
                      </div>
                    )}

                    {error && <p className="mt-1 pl-7 text-[10px] text-destructive">{error}</p>}
                  </div>
                )
              })}
              {!selectedIds.length && (
                <p className="py-8 text-center text-xs text-muted-foreground">
                  Ничего не выбрано
                </p>
              )}
            </div>

            <div className="shrink-0">
              <Label className="mb-1 block text-[11px] text-muted-foreground">
                Предмета нет в каталоге? Укажите идентификатор
              </Label>
              <div className="flex gap-1.5">
                <Input
                  value={customId}
                  onChange={(e) => setCustomId(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustom() } }}
                  placeholder="например, mod_customitem"
                  className="h-8 font-mono text-xs"
                />
                <Button type="button" variant="outline" size="sm" onClick={addCustom} disabled={!customId.trim()}>
                  <Plus className="size-4" />
                </Button>
              </div>
            </div>
          </div>
        </div>

        <DialogFooter className="shrink-0 sm:items-center sm:justify-between">
          <span className="text-[11px] text-destructive">
            {errors.length ? `Проверьте настройки: ${errors.length}` : ""}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
            <Button
              disabled={!selectedIds.length || errors.length > 0 || addMutation.isPending}
              onClick={() => addMutation.mutate()}
            >
              {addMutation.isPending ? "Добавление…" : `Добавить${selectedIds.length ? ` (${selectedIds.length})` : ""}`}
            </Button>
          </div>
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
  const [random, setRandom] = useState(false)
  const [limitMin, setLimitMin] = useState("0")

  useEffect(() => {
    if (!item) return
    setUnlimited(item.is_unlimited)
    setLimited(item.per_round_limit !== null)
    setLimit(item.per_round_limit !== null ? String(item.per_round_limit) : "")
    setRandom(item.per_round_limit_min !== null)
    setLimitMin(item.per_round_limit_min !== null ? String(item.per_round_limit_min) : "0")
  }, [item])

  const parsedLimit = Math.trunc(Number(limit))
  const parsedMin = Math.trunc(Number(limitMin))
  const limitRequired = unlimited
  const limitActive = unlimited || limited
  const limitInvalid = limitActive && (!Number.isFinite(parsedLimit) || parsedLimit < 1)
  const minInvalid = limitActive && random && (
    !Number.isFinite(parsedMin) || parsedMin < 0 || parsedMin > parsedLimit
  )

  const saveMutation = useMutation({
    mutationFn: () => warehouseApi.setLimits(
      item!.identifier,
      unlimited,
      limitActive ? parsedLimit : null,
      limitActive && random ? parsedMin : null,
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
                aria-label={random ? "Верхняя граница лимита" : "Лимит на раунд"}
              />
            )}
            {limitInvalid && (
              <p className="text-xs text-destructive">Укажите целое число больше нуля</p>
            )}
          </div>

          {limitActive && (
            <div className="space-y-2 rounded-md border p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <Label htmlFor="wh-random" className="text-sm">Случайный лимит на раунд</Label>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Каждый раунд потолок разыгрывается заново в заданном диапазоне.
                    Ноль в нижней границе означает, что в какой-то раунд предмета не будет вовсе.
                  </p>
                </div>
                <Switch id="wh-random" checked={random} onCheckedChange={setRandom} />
              </div>
              {random && (
                <div className="flex items-center gap-2">
                  <div className="flex-1">
                    <Label className="mb-1 block text-[11px] text-muted-foreground">От</Label>
                    <Input
                      type="number"
                      min={0}
                      value={limitMin}
                      onChange={(e) => setLimitMin(e.target.value)}
                      aria-label="Нижняя граница лимита"
                    />
                  </div>
                  <div className="flex-1">
                    <Label className="mb-1 block text-[11px] text-muted-foreground">До</Label>
                    <Input
                      type="number"
                      min={1}
                      value={limit}
                      onChange={(e) => setLimit(e.target.value)}
                      aria-label="Верхняя граница лимита"
                    />
                  </div>
                </div>
              )}
              {minInvalid && (
                <p className="text-xs text-destructive">
                  Нижняя граница — целое число от нуля до верхней границы
                </p>
              )}
            </div>
          )}

          {item.rounds && (
            <div className="space-y-1 rounded-md border p-3 text-xs">
              <div className="mb-1 font-medium">Текущий раунд</div>
              {Object.entries(item.rounds).map(([server, state]) => (
                <div key={server} className="flex items-center justify-between text-muted-foreground">
                  <span className="font-mono text-[11px]">{server}</span>
                  <span>
                    выдано {state.issued}
                    {/* Для диапазона это выпавший потолок, а не максимум настройки. */}
                    {state.limit !== null ? ` из ${state.limit}` : ""}
                    {state.remaining !== null ? ` · осталось ${state.remaining}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={limitInvalid || minInvalid || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
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
            <Badge variant="secondary" className="gap-1 px-1.5 py-0 text-[9px]">
              {item.per_round_limit_min !== null && <Dices className="size-2.5" />}
              лимит {item.per_round_limit_min !== null
                ? `${item.per_round_limit_min}–${item.per_round_limit}`
                : item.per_round_limit}/раунд
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
                  {state.limit !== null ? `/${state.limit}` : ""}
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
