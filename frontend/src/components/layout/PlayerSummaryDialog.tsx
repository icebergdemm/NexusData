import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink, Lock } from "lucide-react"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { PlayerAvatar } from "@/components/layout/PlayerAvatar"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import type { PlayerDetailResponse } from "@/pages/players/detail-types"

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}

function fmtPlayTime(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}

function InfoTile({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`font-medium ${className ?? ""}`}>{value}</div>
    </div>
  )
}

export function PlayerSummaryDialog({ playerId, open, onOpenChange }: {
  playerId: number | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["player-summary", playerId],
    queryFn: () => api.get<PlayerDetailResponse>(`/api/v1/admin/players/${playerId}`),
    enabled: open && playerId != null,
  })

  const activePunishments = data?.punishments?.filter((p) => p.status === "active").length ?? 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : !data || data.classified ? (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2"><Lock className="size-4" /> Профиль недоступен</DialogTitle>
              <DialogDescription>Профиль засекречен или у вас нет прав на его просмотр.</DialogDescription>
            </DialogHeader>
          </>
        ) : (
          <>
            <DialogHeader>
              <div className="flex items-center gap-3">
                <PlayerAvatar url={data.player.steam_avatar_url} name={data.player.player_last_time_name} size={48} online={data.is_online} />
                <div className="min-w-0">
                  <DialogTitle className="truncate">{data.player.forced_display_name || data.player.player_last_time_name}</DialogTitle>
                  <div className="font-mono text-xs text-muted-foreground">{data.player.player_steamid}</div>
                </div>
                {data.is_online && <Badge className="bg-success text-white">В сети</Badge>}
              </div>
              <DialogDescription className="sr-only">Краткая сводка профиля игрока</DialogDescription>
            </DialogHeader>

            <div className="grid grid-cols-2 gap-3 rounded-lg border p-3 text-sm">
              <InfoTile label="Ранг" value={data.ranks[String(data.player.rank_id)] ?? String(data.player.rank_id)} />
              <InfoTile label="Наиграно" value={fmtPlayTime(data.player.play_time_hours)} />
              <InfoTile label="Последний вход" value={fmtDate(data.player.last_login_time)} />
              <InfoTile
                label="Активных наказаний"
                value={String(activePunishments)}
                className={activePunishments ? "text-destructive" : ""}
              />
              <InfoTile label="Заметок" value={String(data.notes.length)} />
              <InfoTile label="Меток" value={String(data.label_pairs.length)} />
            </div>

            {data.label_pairs.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {data.label_pairs.map((lp) => (
                  <Badge key={lp.id} style={{ backgroundColor: lp.category.color }} className="gap-1.5 text-white">
                    <PlatformIcon value={lp.category.icon} />{lp.category.name}
                  </Badge>
                ))}
              </div>
            )}

            <DialogFooter>
              <Button asChild>
                <Link to={`/players/${playerId}`} onClick={() => onOpenChange(false)}>
                  <ExternalLink className="size-4" /> Открыть полный профиль
                </Link>
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
