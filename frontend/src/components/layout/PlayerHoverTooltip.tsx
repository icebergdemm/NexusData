import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink, Lock, StickyNote } from "lucide-react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { PlayerAvatar } from "@/components/layout/PlayerAvatar"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import type { PlayerDetailResponse } from "@/pages/players/detail-types"

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

function fmtPlayTime(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}

function truncate(text: string, max = 90): string {
  if (text.length <= max) return text
  return text.slice(0, max).trimEnd() + "…"
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
  const notesPreview = data.notes?.slice(0, 2) ?? []
  const moreNotes = data.notes.length > 2 ? data.notes.length - 2 : 0
  const rankName = data.ranks[String(data.player.rank_id)] ?? String(data.player.rank_id)
  const actualName = data.player.player_last_time_name
  const forcedName = data.player.forced_display_name

  return (
    <div className="w-80 space-y-2.5 text-left">
      <div className="flex items-center gap-2.5">
        <PlayerAvatar url={data.player.steam_avatar_url} name={actualName} size={36} online={data.is_online} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold leading-tight">{actualName}</div>
          {forcedName && (
            <div className="truncate text-xs text-muted-foreground" title={forcedName}>🔗 {forcedName}</div>
          )}
          <div className="font-mono text-[11px] text-muted-foreground">{data.player.player_steamid}</div>
        </div>
        {data.is_online && <Badge className="bg-success text-white text-[10px] shrink-0">В сети</Badge>}
      </div>

      <div className="grid grid-cols-2 gap-2 rounded-md border bg-muted/50 p-2 text-xs">
        <div><div className="text-muted-foreground">Ранг</div><div className="font-medium">{rankName}</div></div>
        <div><div className="text-muted-foreground">Наиграно</div><div className="font-medium">{fmtPlayTime(data.player.play_time_hours)}</div></div>
        <div><div className="text-muted-foreground">Последний вход</div><div className="font-medium text-[11px]">{fmtDate(data.player.last_login_time)}</div></div>
        <div><div className="text-muted-foreground">Наказаний</div><div className={activePunishments ? "font-medium text-destructive" : "font-medium"}>{activePunishments}</div></div>
      </div>

      {data.label_pairs.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {data.label_pairs.slice(0, 4).map((lp) => (
            <Badge key={lp.id} style={{ backgroundColor: lp.category.color }} className="gap-1 text-white text-[10px]">
              <PlatformIcon value={lp.category.icon} />{lp.category.name}
            </Badge>
          ))}
          {data.label_pairs.length > 4 && <span className="text-[11px] text-muted-foreground">+{data.label_pairs.length - 4}</span>}
        </div>
      )}

      {notesPreview.length > 0 && (
        <div className="space-y-1 rounded-md border bg-card px-2.5 py-2">
          <div className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
            <StickyNote className="size-3" /> Заметки · {data.notes.length}
          </div>
          {notesPreview.map((n) => (
            <div key={n.id} className="line-clamp-2 text-xs leading-snug" title={n.content}>
              — {truncate(n.content, 110)}
            </div>
          ))}
          {moreNotes > 0 && <div className="text-[11px] text-muted-foreground">+ ещё {moreNotes} заметок</div>}
        </div>
      )}

      <div className="flex items-center gap-1 text-[11px] text-muted-foreground"><ExternalLink className="size-3" /> Клик — открыть полный профиль</div>
    </div>
  )
}

export function PlayerHoverLink({ playerId, children, className }: { playerId: number | null | undefined; children: React.ReactNode; className?: string }) {
  if (!playerId) {
    return <span className={className}>{children}</span>
  }
  return (
    <Tooltip delayDuration={300}>
      <TooltipTrigger asChild>
        <Link to={`/players/${playerId}`} className={className ?? "text-primary underline-offset-2 hover:underline"}>
          {children}
        </Link>
      </TooltipTrigger>
      <TooltipContent side="top" sideOffset={8} className="max-w-sm border bg-popover p-3 text-popover-foreground shadow-md">
        <TooltipInner playerId={playerId} />
      </TooltipContent>
    </Tooltip>
  )
}

/** Небольшой hover-only тултип без ссылки — для использования в таблицах где клик уже ведёт куда-то. */
export function PlayerHoverCard({ playerId, children }: { playerId: number | null | undefined; children: React.ReactNode }) {
  if (!playerId) return <>{children}</>
  return (
    <Tooltip delayDuration={300}>
      <TooltipTrigger asChild>
        <span className="cursor-pointer underline decoration-dotted underline-offset-2 hover:text-primary">{children}</span>
      </TooltipTrigger>
      <TooltipContent side="top" sideOffset={8} className="max-w-sm border bg-popover p-3 text-popover-foreground shadow-md">
        <TooltipInner playerId={playerId} />
      </TooltipContent>
    </Tooltip>
  )
}
