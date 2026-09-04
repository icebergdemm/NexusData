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
import {
  countActivePlayerFilters,
  getDiscordFilterLabel,
  getSortDirectionLabel,
  type DiscordLinkFilter,
  type PlayerSort,
  type PlayerSortKey,
  type SortDirection,
} from "@/lib/player-filters"

export interface PlayerFilterOption {
  value: string
  label: string
}

export interface PlayerFilterSortOption {
  key: PlayerSortKey
  label: string
}

interface PlayerFiltersSheetProps {
  tabLabel: string
  discordFilter: DiscordLinkFilter
  labelId?: string
  labelOptions?: readonly PlayerFilterOption[]
  rankId?: string
  rankOptions?: readonly PlayerFilterOption[]
  sort: PlayerSort
  sortOptions: readonly PlayerFilterSortOption[]
  activeCount: number
  onDiscordFilterChange: (value: DiscordLinkFilter) => void
  onLabelIdChange?: (value: string) => void
  onRankIdChange?: (value: string) => void
  onSortChange: (value: PlayerSort) => void
  onReset: () => void
}

const DISCORD_OPTIONS: readonly PlayerFilterOption[] = [
  { value: "all", label: "Все" },
  { value: "linked", label: "Есть привязка" },
  { value: "unlinked", label: "Нет привязки" },
]

export function PlayerFiltersSheet({
  tabLabel,
  discordFilter,
  labelId,
  labelOptions,
  rankId,
  rankOptions,
  sort,
  sortOptions,
  activeCount,
  onDiscordFilterChange,
  onLabelIdChange,
  onRankIdChange,
  onSortChange,
  onReset,
}: PlayerFiltersSheetProps) {
  const [open, setOpen] = useState(false)

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <ListFilter className="size-3.5" />
          Фильтры
          {activeCount > 0 && <Badge variant="secondary" className="h-5 min-w-5 justify-center px-1 text-[10px]">{activeCount}</Badge>}
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-[min(100vw-1rem,24rem)] sm:max-w-sm">
        <SheetHeader>
          <SheetTitle>Фильтры игроков</SheetTitle>
          <SheetDescription>Вкладка: {tabLabel}. Изменения применяются сразу.</SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-6 overflow-y-auto px-4">
          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold">Привязка Discord</legend>
            <div className="space-y-2">
              {DISCORD_OPTIONS.map((option) => (
                <label key={option.value} className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="discord-link-filter"
                    value={option.value}
                    checked={discordFilter === option.value}
                    onChange={() => onDiscordFilterChange(option.value as DiscordLinkFilter)}
                    className="size-4 accent-primary"
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </fieldset>

          {labelId != null && labelOptions && onLabelIdChange && (
            <div className="border-t pt-5">
              <Label className="mb-2 block text-sm font-semibold">Метка</Label>
              <Select value={labelId} onValueChange={onLabelIdChange}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {labelOptions.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}

          {rankId != null && rankOptions && onRankIdChange && (
            <div className="border-t pt-5">
              <Label className="mb-2 block text-sm font-semibold">Звание</Label>
              <Select value={rankId} onValueChange={onRankIdChange}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {rankOptions.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="border-t pt-5">
            <Label className="mb-2 block text-sm font-semibold">Сортировать по</Label>
            <Select
              value={sort.key}
              onValueChange={(value) => onSortChange({ key: value as PlayerSortKey, direction: sort.direction })}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {sortOptions.map((option) => <SelectItem key={option.key} value={option.key}>{option.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          {sort.key !== "none" && (
            <div className="space-y-2">
              <Label className="text-sm font-semibold">Порядок</Label>
              <div className="space-y-2">
                {(["asc", "desc"] as const).map((direction: SortDirection) => (
                  <label key={direction} className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="radio"
                      name="player-sort-direction"
                      value={direction}
                      checked={sort.direction === direction}
                      onChange={() => onSortChange({ key: sort.key, direction })}
                      className="size-4 accent-primary"
                    />
                    {getSortDirectionLabel(sort.key, direction)}
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>

        <SheetFooter>
          <Button variant="outline" className="w-full" onClick={onReset}>
            <RotateCcw className="size-3.5" /> Сбросить
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

export interface PlayerFiltersToolbarProps extends Omit<PlayerFiltersSheetProps, "activeCount"> {
  defaultSort: PlayerSort
}

export function PlayerFiltersToolbar({
  defaultSort,
  labelOptions,
  labelId,
  onLabelIdChange,
  rankOptions,
  rankId,
  onRankIdChange,
  ...props
}: PlayerFiltersToolbarProps) {
  const sortOption = props.sortOptions.find((option) => option.key === props.sort.key) ?? props.sortOptions[0]
  const sortChanged = props.sort.key !== defaultSort.key || props.sort.direction !== defaultSort.direction
  const labelActive = labelId != null && labelId !== "all"
  const rankActive = rankId != null && rankId !== "all"
  const selectedLabel = labelOptions?.find((option) => option.value === labelId)
  const selectedRank = rankOptions?.find((option) => option.value === rankId)

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <PlayerFiltersSheet
        {...props}
        labelId={labelId}
        labelOptions={labelOptions}
        rankId={rankId}
        rankOptions={rankOptions}
        onLabelIdChange={onLabelIdChange}
        onRankIdChange={onRankIdChange}
        activeCount={
          countActivePlayerFilters(
            { discordFilter: props.discordFilter, labelId: labelId ?? "all", rankId: rankId ?? "all" },
            props.sort,
            defaultSort,
          )
        }
      />
      {props.discordFilter !== "all" && (
        <Badge variant="secondary" className="gap-1 pl-2">
          Discord: {getDiscordFilterLabel(props.discordFilter)}
          <button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => props.onDiscordFilterChange("all")} aria-label="Убрать фильтр Discord">×</button>
        </Badge>
      )}
      {labelActive && selectedLabel && onLabelIdChange && (
        <Badge variant="secondary" className="gap-1 pl-2">
          Метка: {selectedLabel.label}
          <button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => onLabelIdChange("all")} aria-label="Убрать фильтр метки">×</button>
        </Badge>
      )}
      {rankActive && selectedRank && onRankIdChange && (
        <Badge variant="secondary" className="gap-1 pl-2">
          Звание: {selectedRank.label}
          <button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => onRankIdChange("all")} aria-label="Убрать фильтр звания">×</button>
        </Badge>
      )}
      {sortChanged && sortOption && (
        <Badge variant="secondary" className="gap-1 pl-2">
          Сортировка: {sortOption.label} · {getSortDirectionLabel(props.sort.key, props.sort.direction)}
          <button type="button" className="ml-0.5 rounded-sm px-0.5 text-muted-foreground hover:text-foreground" onClick={() => props.onSortChange(defaultSort)} aria-label="Сбросить сортировку">×</button>
        </Badge>
      )}
    </div>
  )
}
