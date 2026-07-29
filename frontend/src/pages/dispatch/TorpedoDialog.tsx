import { useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { Zap } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { WorldCanvas } from "@/pages/world-overview/WorldCanvas"
import { worldStateApi } from "@/pages/world-overview/api"
import { dispatchActionsApi } from "./actionsApi"

/** Имя сервера из мировой телеметрии → ASPER1/ASPER2.
 *
 *  Плагин присылает server_name в свободной форме («Asper 1», «asper1»,
 *  «ASPER1»), а очередь действий различает серверы строго по ASPER1/ASPER2.
 *  Сравнение строка-в-строку промахивалось, и диалог считал, что активного
 *  раунда нет, хотя «Обзор мира» тот же раунд показывал. Сводим по цифре в
 *  названии — ровно так же, как это делает бэкенд при выборе Discord-канала
 *  диспетчерской (_resolve_dispatch_channel_key). */
function resolveTargetServer(raw: string | null | undefined): string | null {
  const name = (raw ?? "").toUpperCase()
  if (name.includes("1")) return "ASPER1"
  if (name.includes("2")) return "ASPER2"
  return null
}

/** Torpedo strike — pick a server, click a point on that round's live map
 * (WorldCanvas in target-picking mode) to set the crosshair, confirm. Only
 * ever queues a ServerAction with the coordinates (see actionsApi.ts) — the
 * actual strike is the game server's job. */
export function TorpedoDialog({ open, onOpenChange, defaultServer }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  defaultServer?: string
}) {
  // Выбираем не сервер, а конкретный активный раунд: бить всё равно можно
  // только по карте, а карта есть лишь у раунда из мировой телеметрии.
  const [roundId, setRoundId] = useState<string | null>(null)
  const [target, setTarget] = useState<{ x: number; y: number } | null>(null)

  const { data: roundsData, isLoading: roundsLoading } = useQuery({
    queryKey: ["world-rounds"],
    queryFn: () => worldStateApi.listRounds(),
    enabled: open,
    refetchInterval: open ? 5000 : false,
  })
  const rounds = roundsData?.rounds ?? []
  const round =
    rounds.find((r) => r.round_id === roundId) ??
    rounds.find((r) => resolveTargetServer(r.server_name) === resolveTargetServer(defaultServer)) ??
    rounds[0] ??
    null
  const serverName = resolveTargetServer(round?.server_name)

  const { data: geometry } = useQuery({
    queryKey: ["world-geometry", round?.round_id],
    queryFn: () => worldStateApi.getGeometry(round!.round_id),
    enabled: open && !!round,
    staleTime: Infinity,
  })
  const { data: state } = useQuery({
    queryKey: ["world-state", round?.round_id],
    queryFn: () => worldStateApi.getState(round!.round_id),
    enabled: open && !!round,
    refetchInterval: open ? 3000 : false,
  })

  const strikeMutation = useMutation({
    mutationFn: () => dispatchActionsApi.torpedo(serverName!, target!.x, target!.y),
    onSuccess: () => {
      toast.success("Торпедный удар поставлен в очередь")
      onOpenChange(false)
      setTarget(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setTarget(null) }}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-1.5"><Zap className="size-4" /> Торпедный удар</DialogTitle>
          <DialogDescription>Выберите сервер и укажите точку удара кликом по карте раунда.</DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-3">
          <Select
            value={round?.round_id ?? ""}
            onValueChange={(v) => { setRoundId(v); setTarget(null) }}
            disabled={!rounds.length}
          >
            <SelectTrigger className="w-72">
              <SelectValue placeholder={roundsLoading ? "Загрузка…" : "Активных раундов нет"} />
            </SelectTrigger>
            <SelectContent>
              {rounds.map((r) => (
                <SelectItem key={r.round_id} value={r.round_id}>
                  {r.server_name ?? "?"} — {r.level_name ?? "?"}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {target ? (
            <span className="text-xs text-muted-foreground">Цель: x={target.x}, y={target.y}</span>
          ) : (
            <span className="text-xs text-muted-foreground">Кликните по карте, чтобы выбрать точку удара</span>
          )}
        </div>

        {round && !serverName && (
          <p className="text-xs text-destructive">
            Не удалось определить, какому серверу адресовать удар: раунд идёт на «{round.server_name ?? "?"}»,
            а очередь действий различает только ASPER1 и ASPER2.
          </p>
        )}

        <div className="h-[420px]">
          {!round ? (
            <div className="flex h-full items-center justify-center rounded-lg border text-sm text-muted-foreground">
              {roundsLoading ? "Загрузка активных раундов…" : "Нет активных раундов"}
            </div>
          ) : !geometry || !state ? (
            <Skeleton className="h-full" />
          ) : (
            <WorldCanvas
              geometry={geometry}
              state={state}
              selectedCharacterId={null}
              onSelectCharacter={() => {}}
              targetMode
              targetPoint={target}
              onPickTarget={(x, y) => setTarget({ x, y })}
            />
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button
            variant="destructive"
            disabled={!target || !serverName || strikeMutation.isPending}
            onClick={() => strikeMutation.mutate()}
          >
            Нанести удар
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
