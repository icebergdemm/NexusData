import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink, Lock } from "lucide-react"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { PlayerAvatar } from "@/components/layout/PlayerAvatar"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import type { PlayerDetailResponse } from "@/pages/players/detail-types"

function fmtDate(iso: string | null): string {
  // Используем таймзону браузера — new Date(iso) парсит ISO как UTC (если есть Z) и
  // toLocaleString конвертит в локаль браузера.
  if (!iso) return "-"
  const normalized = iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z"
  return new Date(normalized).toLocaleString("ru-RU")
}

function fmtPlayTime(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}

function TooltipInner({ playerId }: { playerId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["player-tooltip", playerId],
    queryFn: () => api.get<PlayerDetailResponse>(`/api/v1/admin/players/${playerId}`),
    staleTime: 2 * 60 * 1000,
  })

  if (isLoading) {
    return <div className="w-64 space-y-2"><Skeleton className="h-10 w-full" /><Skeleton className="h-16 w-full" /></div>
  }
  if (!data || data.classified) {
    return <div className="flex items-center gap-2 text-xs"><Lock className="size-3" /> Профиль засекречен</div>
  }

  const activePunishments = data.punishments?.filter((p) => p.status === "active").length ?? 0
  return (
    <div className="w-72 space-y-2 text-left">
      <div className="flex items-center gap-2">
        <PlayerAvatar url={data.player.steam_avatar_url} name={data.player.player_last_time_name} size={36} online={data.is_online} />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{data.player.forced_display_name || data.player.player_last_time_name}</div>
          <div className="font-mono text-[11px] opacity-80">{data.player.player_steamid}</div>
        </div>
        {data.is_online && <Badge className="bg-success text-white text-[10px]">В сети</Badge>}
      </div>
      <div className="grid grid-cols-2 gap-2 rounded-md bg-background/10 p-2 text-xs">
        <div><div className="opacity-60">Ранг</div><div className="font-medium">{data.ranks[String(data.player.rank_id)] ?? String(data.player.rank_id)}</div></div>
        <div><div className="opacity-60">Наиграно</div><div className="font-medium">{fmtPlayTime(data.player.play_time_hours)}</div></div>
        <div><div className="opacity-60">Последний вход</div><div className="font-medium">{fmtDate(data.player.last_login_time)}</div></div>
        <div><div className="opacity-60">Наказаний</div><div className={activePunishments ? "font-medium text-destructive" : "font-medium"}>{activePunishments}</div></div>
      </div>
      {data.label_pairs.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {data.label_pairs.slice(0, 4).map((lp) => (
            <Badge key={lp.id} style={{ backgroundColor: lp.category.color }} className="gap-1 text-white text-[10px]">
              <PlatformIcon value={lp.category.icon} />{lp.category.name}
            </Badge>
          ))}
          {data.label_pairs.length > 4 && <span className="text-[11px] opacity-60">+{data.label_pairs.length - 4}</span>}
        </div>
      )}
      <div className="flex items-center gap-1 text-[11px] opacity-70"><ExternalLink className="size-3" /> Клик — открыть полный профиль</div>
    </div>
  )
}

export function PlayerHoverLink({ playerId, children, className }: { playerId: number | null | undefined; children: React.ReactNode; className?: string }) {
  if (!playerId) {
    return <span className={className}>{children}</span>
  }
  return (
    <TooltipProvider>
      <Tooltip delayDuration={300}>
        <TooltipTrigger asChild>
          <Link to={`/players/${playerId}`} className={className ?? "text-primary underline-offset-2 hover:underline"}>
            {children}
          </Link>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm bg-foreground text-background">
          <TooltipInner playerId={playerId} />
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
