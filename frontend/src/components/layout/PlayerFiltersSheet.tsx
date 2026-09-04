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
import type { DiscordLinkFilter, PlayerSort, PlayerSortKey, SortDirection } from "@/lib/player-filters"

export interface PlayerFilterSortOption {
  key: PlayerSortKey
  label: string
}

interface PlayerFiltersSheetProps {
  tabLabel: string
  discordFilter: DiscordLinkFilter
  sort: PlayerSort
  sortOptions: readonly PlayerFilterSortOption[]
  activeCount: number
  onDiscordFilterChange: (value: DiscordLinkFilter) => void
  onSortChange: (value: PlayerSort) => void
  onReset: () => void
}

const DISCORD_OPTIONS: readonly { value: DiscordLinkFilter; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "linked", label: "Есть привязка" },
  { value: "unlinked", label: "Нет привязки" },
]

function directionLabels(key: PlayerSortKey): { asc: string; desc: string } {
  if (key === "name" || key === "server") return { asc: "А — Я", desc: "Я — А" }
  if (key === "play_time") return { asc: "Меньше сначала", desc: "Больше сначала" }
  return { asc: "Сначала старые", desc: "Сначала новые" }
}

export function PlayerFiltersSheet({
  tabLabel,
  discordFilter,
  sort,
  sortOptions,
  activeCount,
  onDiscordFilterChange,
  onSortChange,
  onReset,
}: PlayerFiltersSheetProps) {
  const [open, setOpen] = useState(false)
  const labels = directionLabels(sort.key)

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
                    onChange={() => onDiscordFilterChange(option.value)}
                    className="size-4 accent-primary"
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </fieldset>

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
                  {direction === "asc" ? labels.asc : labels.desc}
                </label>
              ))}
            </div>
          </div>
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
