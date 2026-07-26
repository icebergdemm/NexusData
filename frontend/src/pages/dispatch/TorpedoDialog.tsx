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

const SERVERS = ["ASPER1", "ASPER2"]

/** Torpedo strike — pick a server, click a point on that round's live map
 * (WorldCanvas in target-picking mode) to set the crosshair, confirm. Only
 * ever queues a ServerAction with the coordinates (see actionsApi.ts) — the
 * actual strike is the game server's job. */
export function TorpedoDialog({ open, onOpenChange, defaultServer }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  defaultServer?: string
}) {
  const [serverName, setServerName] = useState(
    defaultServer && SERVERS.includes(defaultServer) ? defaultServer : SERVERS[0]
  )
  const [target, setTarget] = useState<{ x: number; y: number } | null>(null)

  const { data: roundsData } = useQuery({
    queryKey: ["world-rounds"],
    queryFn: () => worldStateApi.listRounds(),
    enabled: open,
    refetchInterval: open ? 5000 : false,
  })
  const round = roundsData?.rounds.find((r) => r.server_name === serverName) ?? null

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
    mutationFn: () => dispatchActionsApi.torpedo(serverName, target!.x, target!.y),
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

        <div className="flex items-center gap-3">
          <Select value={serverName} onValueChange={(v) => { setServerName(v); setTarget(null) }}>
            <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SERVERS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
          {target ? (
            <span className="text-xs text-muted-foreground">Цель: x={target.x}, y={target.y}</span>
          ) : (
            <span className="text-xs text-muted-foreground">Кликните по карте, чтобы выбрать точку удара</span>
          )}
        </div>

        <div className="h-[420px]">
          {!round ? (
            <div className="flex h-full items-center justify-center rounded-lg border text-sm text-muted-foreground">
              На сервере {serverName} нет активного раунда
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
            disabled={!target || strikeMutation.isPending}
            onClick={() => strikeMutation.mutate()}
          >
            Нанести удар
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
