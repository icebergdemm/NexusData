import { useMemo, useState } from "react"
import { Clipboard, FileJson, LoaderCircle } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { Checkbox } from "@/components/ui/checkbox"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { api } from "@/lib/api"
import type { AIDossierResponse, PlayerDetailResponse } from "./detail-types"
import { PlayerExportPreview } from "./PlayerExportPreview"
import {
  buildPlayerExport,
  PLAYER_EXPORT_GROUPS,
  serializePlayerExport,
  type PlayerExportGroup,
  type PlayerTicketExportResponse,
} from "./player-export"

const GROUP_LABELS: Record<PlayerExportGroup, string> = {
  profile: "Профиль",
  activity: "Игровая активность",
  history: "История",
  moderation: "Модерация",
  tickets: "Тикеты и архив переписки",
  rewards: "Награды",
  ai_assessment: "AI-сводка",
}

interface PlayerExportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  playerId: number
  player: PlayerDetailResponse
  dossier: AIDossierResponse | null
  dossierLoading: boolean
  dossierError?: unknown
}

function formatError(error: unknown): string {
  return error instanceof Error ? error.message : "Неизвестная ошибка"
}

function groupCount(group: PlayerExportGroup, player: PlayerDetailResponse, tickets?: PlayerTicketExportResponse): string {
  if (group === "profile") return "1 объект"
  if (group === "activity") return `${player.kills.length + player.role_actions.length + player.role_stats.length} записей`
  if (group === "history") return `${player.names.length + player.ips.length + player.sessions.length} записей`
  if (group === "moderation") return `${player.punishments.length + player.notes.length + player.label_pairs.length + player.traps.length} записей`
  if (group === "tickets") return tickets ? `${tickets.ticket_count} тикетов` : "загрузка…"
  if (group === "rewards") return `${Object.keys(player.kval_kits).length + Object.keys(player.custom_items).length} наборов/предметов`
  return "1 объект"
}

export function PlayerExportDialog({
  open,
  onOpenChange,
  playerId,
  player,
  dossier,
  dossierLoading,
  dossierError,
}: PlayerExportDialogProps) {
  const [includedGroups, setIncludedGroups] = useState<ReadonlySet<PlayerExportGroup>>(
    () => new Set(PLAYER_EXPORT_GROUPS),
  )
  const [exportedAt, setExportedAt] = useState(() => new Date().toISOString())

  const ticketQuery = useQuery({
    queryKey: ["player-export-tickets", playerId],
    queryFn: () => api.get<PlayerTicketExportResponse>(`/api/v1/admin/players/${playerId}/export-data`),
    enabled: open,
    staleTime: 0,
  })

  const exportData = useMemo(() => buildPlayerExport({
    player,
    dossier,
    ticketArchive: ticketQuery.data ?? null,
    exportedAt,
  }, includedGroups), [player, dossier, ticketQuery.data, exportedAt, includedGroups])
  const serializedExport = useMemo(() => serializePlayerExport(exportData), [exportData])

  const ticketsUnavailable = includedGroups.has("tickets")
    && (ticketQuery.isLoading || ticketQuery.isError || !ticketQuery.data)
  const aiUnavailable = includedGroups.has("ai_assessment")
    && (dossierLoading || Boolean(dossierError))
  const canCopy = !ticketsUnavailable && !aiUnavailable

  const resetForOpen = (nextOpen: boolean) => {
    if (nextOpen) {
      setIncludedGroups(new Set(PLAYER_EXPORT_GROUPS))
      setExportedAt(new Date().toISOString())
    }
    onOpenChange(nextOpen)
  }

  const setGroupIncluded = (group: PlayerExportGroup, checked: boolean) => {
    setIncludedGroups((current) => {
      const next = new Set(current)
      if (checked) next.add(group)
      else next.delete(group)
      return next
    })
  }

  const copyExport = () => {
    if (!canCopy) return
    navigator.clipboard.writeText(serializedExport)
      .then(() => toast.success("JSON скопирован"))
      .catch(() => toast.error("Не удалось скопировать JSON"))
  }

  return (
    <Dialog open={open} onOpenChange={resetForOpen}>
      <DialogContent className="flex max-h-[92vh] w-[min(96vw,72rem)] max-w-none flex-col gap-4 overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><FileJson className="size-4" /> Экспорт личного дела</DialogTitle>
          <DialogDescription>
            {player.player.player_last_time_name} · данные собираются из профиля и сохранённого архива тикетов. Поиск по Discord не выполняется.
          </DialogDescription>
        </DialogHeader>

        <section className="space-y-3 rounded-lg border bg-muted/20 p-4" aria-labelledby="player-export-settings">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="player-export-settings" className="text-sm font-semibold">Настройки экспорта</h2>
            <div className="flex gap-2">
              <Button type="button" size="sm" variant="outline" onClick={() => setIncludedGroups(new Set(PLAYER_EXPORT_GROUPS))}>Выбрать всё</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setIncludedGroups(new Set())}>Снять всё</Button>
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {PLAYER_EXPORT_GROUPS.map((group) => (
              <label key={group} className="flex cursor-pointer items-start gap-2 rounded-md border bg-background/50 p-2 text-sm">
                <Checkbox
                  checked={includedGroups.has(group)}
                  onCheckedChange={(checked) => setGroupIncluded(group, checked === true)}
                />
                <span className="min-w-0">
                  <span className="block truncate">{GROUP_LABELS[group]}</span>
                  <span className="block text-xs text-muted-foreground">{groupCount(group, player, ticketQuery.data)}</span>
                </span>
              </label>
            ))}
          </div>
        </section>

        {ticketQuery.isError && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            Не удалось загрузить архив тикетов: {formatError(ticketQuery.error)}. Пока группа «Тикеты» выбрана, копирование заблокировано.
          </div>
        )}
        {dossierError != null && includedGroups.has("ai_assessment") && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            Не удалось загрузить AI-сводку: {formatError(dossierError)}. Пока группа выбрана, копирование заблокировано.
          </div>
        )}

        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Предпросмотр схемы</h2>
          <Button type="button" size="sm" onClick={copyExport} disabled={!canCopy || ticketQuery.isFetching}>
            <Clipboard className="size-3.5" /> Копировать JSON
          </Button>
        </div>

        <div className="min-h-0 flex-1">
          {ticketQuery.isLoading || (includedGroups.has("ai_assessment") && dossierLoading) ? (
            <div className="flex h-full min-h-32 items-center justify-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" /> Загружаем данные для preview…
            </div>
          ) : (
            <ScrollArea className="h-full pr-3">
              <PlayerExportPreview data={exportData} />
            </ScrollArea>
          )}
        </div>

        <DialogFooter className="border-t pt-3">
          <Button type="button" variant="outline" onClick={() => resetForOpen(false)}>Закрыть</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
