import { useState } from "react"
import { ListFilter, RotateCcw } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger,
} from "@/components/ui/sheet"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import {
  countActivePlayerFilters,
  getDiscordFilterLabel,
  getSortDirectionLabel,
  type ActivityFilter,
  type DiscordLinkFilter,
  type FilterMode,
  type PlayerFilterState,
  type PlayerSort,
  type PlayerSortKey,
  type SortDirection,
} from "@/lib/player-filters"

export interface PlayerFilterOption {
  value: string
  label: string
  icon?: string | null
}

export interface PlayerFilterSortOption {
  key: PlayerSortKey
  label: string
}

interface PlayerFiltersSheetProps {
  tabLabel: string
  filters: PlayerFilterState
  labelOptions: readonly PlayerFilterOption[]
  rankOptions: readonly PlayerFilterOption[]
  sort: PlayerSort
  defaultSort: PlayerSort
  sortOptions: readonly PlayerFilterSortOption[]
  onFiltersChange: (filters: PlayerFilterState) => void
  onSortChange: (sort: PlayerSort) => void
  onReset: () => void
}

const DISCORD_OPTIONS: readonly { value: DiscordLinkFilter; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "linked", label: "Есть привязка" },
  { value: "unlinked", label: "Нет привязки" },
]

const MODE_OPTIONS: readonly { value: FilterMode; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "with", label: "Есть" },
  { value: "without", label: "Нет" },
]

const ACTIVITY_OPTIONS: readonly { value: ActivityFilter; label: string }[] = [
  { value: "all", label: "Любая активность" },
  { value: "new", label: "Новый игрок (до 2 часов)" },
  { value: "inactive_7", label: "Не заходил 7+ дней" },
  { value: "inactive_30", label: "Не заходил 30+ дней" },
  { value: "inactive_90", label: "Не заходил 90+ дней" },
]

function ModeField({ label, value, onChange }: { label: string; value: FilterMode; onChange: (value: FilterMode) => void }) {
  return (
    <fieldset className="space-y-2 border-t pt-5">
      <legend className="mb-1 text-sm font-semibold">{label}</legend>
      <div className="flex flex-wrap gap-2">
        {MODE_OPTIONS.map((option) => (
          <label key={option.value} className="flex cursor-pointer items-center gap-1.5 text-sm">
            <input type="radio" name={`player-filter-${label}`} value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} className="size-4 accent-primary" />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function updateFilters(filters: PlayerFilterState, onFiltersChange: (filters: PlayerFilterState) => void, changes: Partial<PlayerFilterState>) {
  onFiltersChange({ ...filters, ...changes })
}

export function PlayerFiltersSheet({ tabLabel, filters, labelOptions, rankOptions, sort, defaultSort, sortOptions, onFiltersChange, onSortChange, onReset }: PlayerFiltersSheetProps) {
  const [open, setOpen] = useState(false)
  const activeCount = countActivePlayerFilters(filters, sort, defaultSort)

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5"><ListFilter className="size-3.5" />Фильтры{activeCount > 0 && <Badge variant="secondary" className="h-5 min-w-5 justify-center px-1 text-[10px]">{activeCount}</Badge>}</Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-[min(100vw-1rem,24rem)] sm:max-w-sm">
        <SheetHeader><SheetTitle>Фильтры игроков</SheetTitle><SheetDescription>Вкладка: {tabLabel}. Изменения применяются сразу.</SheetDescription></SheetHeader>
        <div className="flex-1 space-y-6 overflow-y-auto px-4">
          <fieldset className="space-y-3"><legend className="text-sm font-semibold">Привязка Discord</legend><div className="space-y-2">
            {DISCORD_OPTIONS.map((option) => <label key={option.value} className="flex cursor-pointer items-center gap-2 text-sm"><input type="radio" name="discord-link-filter" value={option.value} checked={filters.discordFilter === option.value} onChange={() => updateFilters(filters, onFiltersChange, { discordFilter: option.value })} className="size-4 accent-primary" />{option.label}</label>)}
          </div></fieldset>

          <fieldset className="space-y-3 border-t pt-5"><legend className="text-sm font-semibold">Метки</legend><div className="flex flex-wrap gap-2">
            {([[
              "all", "Все",
            ], ["with", "Есть метки"], ["without", "Без меток"]] as const).map(([value, label]) => <label key={value} className="flex cursor-pointer items-center gap-1.5 text-sm"><input type="radio" name="player-label-mode" value={value} checked={filters.labelMode === value} onChange={() => updateFilters(filters, onFiltersChange, { labelMode: value, labelIds: [] })} className="size-4 accent-primary" />{label}</label>)}
          </div><div className="max-h-56 space-y-1 overflow-y-auto rounded-md border p-2">
            {labelOptions.filter((option) => option.value !== "all").map((option) => { const selected = filters.labelIds.includes(option.value); const nextLabelIds = selected ? filters.labelIds.filter((id) => id !== option.value) : [...filters.labelIds, option.value]; return <label key={option.value} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-muted"><input type="checkbox" checked={selected} onChange={() => updateFilters(filters, onFiltersChange, { labelMode: nextLabelIds.length > 0 ? "any" : "all", labelIds: nextLabelIds })} className="size-4 accent-primary" /><PlatformIcon value={option.icon} fallback={ListFilter} className="size-3.5" /><span className="truncate">{option.label}</span></label> })}
            {!labelOptions.some((option) => option.value !== "all") && <div className="px-1.5 py-1 text-xs text-muted-foreground">Метки ещё не созданы</div>}
          </div>{filters.labelMode === "any" && filters.labelIds.length > 0 && <div className="text-xs text-muted-foreground">Подойдёт любая выбранная метка</div>}</fieldset>

          {rankOptions.length > 0 && <div className="border-t pt-5"><Label className="mb-2 block text-sm font-semibold">Звание</Label><Select value={filters.rankId} onValueChange={(rankId) => updateFilters(filters, onFiltersChange, { rankId })}><SelectTrigger className="w-full"><SelectValue /></SelectTrigger><SelectContent>{rankOptions.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent></Select></div>}
          <ModeField label="Заметки" value={filters.notesMode} onChange={(notesMode) => updateFilters(filters, onFiltersChange, { notesMode })} />
          <ModeField label="Действующие взыскания" value={filters.sanctionsMode} onChange={(sanctionsMode) => updateFilters(filters, onFiltersChange, { sanctionsMode })} />
          <ModeField label="Открытые сигналы" value={filters.anomaliesMode} onChange={(anomaliesMode) => updateFilters(filters, onFiltersChange, { anomaliesMode })} />
          <ModeField label="История имён" value={filters.nameHistoryMode} onChange={(nameHistoryMode) => updateFilters(filters, onFiltersChange, { nameHistoryMode })} />
          <div className="border-t pt-5"><Label className="mb-2 block text-sm font-semibold">Активность</Label><Select value={filters.activityMode} onValueChange={(activityMode) => updateFilters(filters, onFiltersChange, { activityMode: activityMode as ActivityFilter })}><SelectTrigger className="w-full"><SelectValue /></SelectTrigger><SelectContent>{ACTIVITY_OPTIONS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent></Select></div>
          <div className="border-t pt-5"><Label className="mb-2 block text-sm font-semibold">Сортировать по</Label><Select value={sort.key} onValueChange={(key) => onSortChange({ key: key as PlayerSortKey, direction: sort.direction })}><SelectTrigger className="w-full"><SelectValue /></SelectTrigger><SelectContent>{sortOptions.map((option) => <SelectItem key={option.key} value={option.key}>{option.label}</SelectItem>)}</SelectContent></Select></div>
          {sort.key !== "none" && <div className="space-y-2"><Label className="text-sm font-semibold">Порядок</Label><div className="space-y-2">{(["asc", "desc"] as const).map((direction: SortDirection) => <label key={direction} className="flex cursor-pointer items-center gap-2 text-sm"><input type="radio" name="player-sort-direction" value={direction} checked={sort.direction === direction} onChange={() => onSortChange({ key: sort.key, direction })} className="size-4 accent-primary" />{getSortDirectionLabel(sort.key, direction)}</label>)}</div></div>}
        </div>
        <SheetFooter><Button variant="outline" className="w-full" onClick={onReset}><RotateCcw className="size-3.5" /> Сбросить</Button></SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

export type PlayerFiltersToolbarProps = PlayerFiltersSheetProps

function modeLabel(mode: FilterMode, withLabel: string, withoutLabel: string): string { return mode === "with" ? withLabel : withoutLabel }

export function PlayerFiltersToolbar({ ...props }: PlayerFiltersToolbarProps) {
  const { filters, labelOptions, sort, defaultSort, onFiltersChange, onSortChange } = props
  const selectedLabels = labelOptions.filter((option) => filters.labelIds.includes(option.value))
  const labelSummary = filters.labelMode === "any" && selectedLabels.length > 0 ? `Метки: ${selectedLabels.length}` : filters.labelMode === "with" ? "Есть метки" : filters.labelMode === "without" ? "Без меток" : null
  const sortOption = props.sortOptions.find((option) => option.key === sort.key) ?? props.sortOptions[0]
  const sortChanged = sort.key !== defaultSort.key || sort.direction !== defaultSort.direction
  const clear = (changes: Partial<PlayerFilterState>) => onFiltersChange({ ...filters, ...changes })
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <PlayerFiltersSheet {...props} />
      {filters.discordFilter !== "all" && <Badge variant="secondary" className="gap-1 pl-2">Discord: {getDiscordFilterLabel(filters.discordFilter)}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => clear({ discordFilter: "all" })} aria-label="Убрать фильтр Discord">×</button></Badge>}
      {labelSummary && <Badge variant="secondary" className="gap-1 pl-2">{labelSummary}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => clear({ labelMode: "all", labelIds: [] })} aria-label="Убрать фильтр меток">×</button></Badge>}
      {filters.rankId !== "all" && <Badge variant="secondary" className="gap-1 pl-2">Звание: {props.rankOptions.find((option) => option.value === filters.rankId)?.label ?? filters.rankId}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => clear({ rankId: "all" })} aria-label="Убрать фильтр звания">×</button></Badge>}
      {filters.notesMode !== "all" && <Badge variant="secondary" className="gap-1 pl-2">Заметки: {modeLabel(filters.notesMode, "есть", "нет")}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => clear({ notesMode: "all" })} aria-label="Убрать фильтр заметок">×</button></Badge>}
      {filters.sanctionsMode !== "all" && <Badge variant="secondary" className="gap-1 pl-2">Взыскания: {modeLabel(filters.sanctionsMode, "есть", "нет")}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => clear({ sanctionsMode: "all" })} aria-label="Убрать фильтр взысканий">×</button></Badge>}
      {filters.anomaliesMode !== "all" && <Badge variant="secondary" className="gap-1 pl-2">Сигналы: {modeLabel(filters.anomaliesMode, "есть", "нет")}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => clear({ anomaliesMode: "all" })} aria-label="Убрать фильтр сигналов">×</button></Badge>}
      {filters.nameHistoryMode !== "all" && <Badge variant="secondary" className="gap-1 pl-2">История имён: {modeLabel(filters.nameHistoryMode, "есть", "нет")}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => clear({ nameHistoryMode: "all" })} aria-label="Убрать фильтр истории имён">×</button></Badge>}
      {filters.activityMode !== "all" && <Badge variant="secondary" className="gap-1 pl-2">Активность: {ACTIVITY_OPTIONS.find((option) => option.value === filters.activityMode)?.label}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => clear({ activityMode: "all" })} aria-label="Убрать фильтр активности">×</button></Badge>}
      {sortChanged && sortOption && <Badge variant="secondary" className="gap-1 pl-2">Сортировка: {sortOption.label} · {getSortDirectionLabel(sort.key, sort.direction)}<button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => onSortChange(defaultSort)} aria-label="Сбросить сортировку">×</button></Badge>}
    </div>
  )
}
