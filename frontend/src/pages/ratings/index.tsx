import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { Gift, Medal, RefreshCw, Search, Star } from "lucide-react"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { PlayerAvatar } from "@/components/layout/PlayerAvatar"
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { api } from "@/lib/api"
import { RatingConfigSection } from "./config-section"
import { buildRatingV2Query, normalizeRatingLeaderboard, type RatingMode } from "./rating-v2-contract"
import { RatingEventDrawer, RatingV2Investigation } from "./rating-v2-views"

const ROLE_LABELS: Record<string, string> = { captain: "Капитан", mechanic: "Механик", medic: "Врач", security: "СБ", engineer: "Инженер" }
const FALLBACK_ROLES = ["captain", "mechanic", "medic", "security", "engineer"]
const PAGE_SIZE = 25

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

interface PlayerSuggestion {
  player_id: number
  player_last_time_name: string
  forced_display_name: string | null
  player_steamid: string
  steam_avatar_url: string | null
}

function labelForRole(role: string | null | undefined): string {
  return role ? ROLE_LABELS[role] ?? role : "—"
}

function labelForDataQuality(value: string | null | undefined): string {
  if (value === "legacy") return "Legacy"
  if (value === "sufficient_data" || value === "eligible") return "Достаточно данных"
  if (value === "insufficient_data") return "Мало данных"
  if (value === "no_data") return "Нет данных"
  return value ?? "Мало данных"
}

export function RatingsPage() {
  const [mode, setMode] = useState<RatingMode>("mission")
  const [role, setRole] = useState("all")
  const [searchInput, setSearchInput] = useState("")
  const [debouncedSearch, setDebouncedSearch] = useState("")
  const [suggestOpen, setSuggestOpen] = useState(false)
  const blurTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [page, setPage] = useState(1)
  const [selectedPlayer, setSelectedPlayer] = useState<{ steamId: string; sourceRole: string | null } | null>(null)
  const [drawerRole, setDrawerRole] = useState("all")
  const [selectedEvent, setSelectedEvent] = useState<Record<string, unknown> | null>(null)
  const leaderboardQuery = useQuery({
    queryKey: ["ratings-v2", mode, role, page, PAGE_SIZE],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings?${buildRatingV2Query({ mode, role, page, pageSize: PAGE_SIZE })}`),
  })
  const leaderboard = normalizeRatingLeaderboard(leaderboardQuery.data)
  const roles = leaderboard.roles.length ? leaderboard.roles : FALLBACK_ROLES
  const roleOptions = ["all", ...roles.filter((item) => item !== "all")]
  const topLevel = asRecord(leaderboardQuery.data)
  const canConfigure = topLevel.is_superuser === true || topLevel.can_edit === true
  useEffect(() => {
    const timeout = setTimeout(() => setDebouncedSearch(searchInput.trim()), 300)
    return () => clearTimeout(timeout)
  }, [searchInput])

  const { data: suggestData } = useQuery({
    queryKey: ["rating-player-suggestions", debouncedSearch],
    queryFn: () => api.get<{ players: PlayerSuggestion[] }>(`/api/v1/admin/players/search-suggestions?q=${encodeURIComponent(debouncedSearch)}`),
    enabled: debouncedSearch.length >= 2,
  })
  const suggestions = debouncedSearch.length >= 2 ? (suggestData?.players ?? []) : []

  const discordRefreshMutation = useMutation({
    mutationFn: () => api.post<{ status: string }>("/api/v2/admin/ratings/discord-refresh", {}),
    onSuccess: () => toast.success("Рейтинг опубликован в Discord"),
    onError: (error: Error) => toast.error(error.message),
  })
  useEffect(() => {
    setPage(1)
  }, [mode, role])

  const detailQuery = useQuery({
    queryKey: ["rating-v2-player", mode, selectedPlayer?.steamId, drawerRole],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedPlayer?.steamId ?? "")}?${buildRatingV2Query({ mode, role: drawerRole })}`),
    enabled: Boolean(selectedPlayer),
  })
  const explanationQuery = useQuery({
    queryKey: ["rating-v2-explanation", mode, selectedPlayer?.steamId, drawerRole],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedPlayer?.steamId ?? "")}/explanation?${buildRatingV2Query({ mode, role: drawerRole })}`),
    enabled: Boolean(selectedPlayer),
  })
  const eventsQuery = useQuery({
    queryKey: ["rating-v2-events", mode, selectedPlayer?.steamId, drawerRole],
    queryFn: () => api.get<unknown>(`/api/v2/admin/rating-events?${buildRatingV2Query({ mode, role: drawerRole, page: 1, pageSize: PAGE_SIZE, steamId: selectedPlayer?.steamId })}`),
    enabled: Boolean(selectedPlayer),
  })
  const historyQuery = useQuery({
    queryKey: ["rating-v2-history", mode, selectedPlayer?.steamId, drawerRole],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedPlayer?.steamId ?? "")}/history?${buildRatingV2Query({ mode, role: drawerRole })}`),
    enabled: Boolean(selectedPlayer),
  })
  const deliveryQuery = useQuery({
    queryKey: ["rating-v2-delivery", mode, selectedPlayer?.steamId],
    queryFn: () => api.get<unknown>(`/api/v2/admin/rating-delivery-log?mode=${mode}&steam_id=${encodeURIComponent(selectedPlayer?.steamId ?? "")}`),
    enabled: Boolean(selectedPlayer),
  })

  const selectPlayer = (steamId: string, rowRole?: string | null) => {
    const next = steamId.trim()
    if (next) {
      setDrawerRole("all")
      setSelectedPlayer({ steamId: next, sourceRole: rowRole ?? null })
    }
  }
  const closePlayer = () => { setSelectedPlayer(null); setSelectedEvent(null) }
  const detailLoading = detailQuery.isLoading || explanationQuery.isLoading || eventsQuery.isLoading || historyQuery.isLoading || deliveryQuery.isLoading

  return <div className="space-y-4">
    <PageHeader title="Рейтинг" description="Mission и PvP — отдельные таблицы, расчёты и журнал изменений" icon={Star} actions={<div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => discordRefreshMutation.mutate()} disabled={!canConfigure || discordRefreshMutation.isPending} title="Опубликовать обе доски рейтинга в Discord прямо сейчас"><RefreshCw className={`size-3.5 ${discordRefreshMutation.isPending ? "animate-spin" : ""}`} /> Обновить Discord</Button><Button variant="outline" asChild><Link to="/ratings/rewards"><Gift className="size-4" /> Награды</Link></Button></div>} />

    <div className="flex flex-wrap items-center gap-2">
      <Tabs value={mode} onValueChange={(value) => setMode(value as RatingMode)}><TabsList><TabsTrigger value="mission">Mission</TabsTrigger><TabsTrigger value="pvp">PvP</TabsTrigger></TabsList></Tabs>
      <Select value={role} onValueChange={setRole}><SelectTrigger className="w-44" aria-label="Фильтр роли"><SelectValue placeholder="Все роли" /></SelectTrigger><SelectContent>{roleOptions.map((item) => <SelectItem key={item} value={item}>{item === "all" ? "Все роли" : labelForRole(item)}</SelectItem>)}</SelectContent></Select>
      <div className="relative min-w-56 max-w-xs flex-1">
        <SearchInput value={searchInput} onChange={(event) => setSearchInput(event.target.value)} onFocus={() => setSuggestOpen(true)} onBlur={() => { blurTimeout.current = setTimeout(() => setSuggestOpen(false), 150) }} placeholder="Поиск по нику, SteamID, Discord ID…" />
        {suggestOpen && suggestions.length > 0 && <div className="absolute top-full left-0 z-20 mt-1 w-full overflow-hidden rounded-md border bg-popover shadow-md">{suggestions.map((player) => <button key={player.player_id} type="button" className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm hover:bg-accent" onMouseDown={(event) => { event.preventDefault(); if (blurTimeout.current) clearTimeout(blurTimeout.current); setSearchInput(player.forced_display_name || player.player_last_time_name); selectPlayer(player.player_steamid) }}><PlayerAvatar url={player.steam_avatar_url} name={player.forced_display_name || player.player_last_time_name} size={22} /><span className="min-w-0 flex-1 truncate">{player.forced_display_name || player.player_last_time_name}</span><span className="shrink-0 font-mono text-[10px] text-muted-foreground">{player.player_steamid}</span></button>)}</div>}
      </div>
      <Button variant="outline" size="sm" disabled={!searchInput.trim()} onClick={() => selectPlayer(searchInput)}><Search className="size-3.5" />Открыть игрока</Button>
    </div>

    <Card className="overflow-hidden py-0">
      <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">{mode === "pvp" ? "PvP" : "Mission"} · рейтинг игроков</CardTitle></CardHeader>
      <CardContent className="p-0">
        {leaderboardQuery.isLoading ? <div className="p-4"><Skeleton className="h-64" /></div> : <Table>
          <TableHeader><TableRow><TableHead className="w-12">#</TableHead><TableHead>Игрок</TableHead><TableHead>Роль</TableHead><TableHead>Рейтинг</TableHead><TableHead>Вклад</TableHead><TableHead>Активность</TableHead><TableHead>Часы</TableHead><TableHead>Раунды</TableHead><TableHead>Данные</TableHead></TableRow></TableHeader>
          <TableBody>{leaderboard.leaderboard.map((entry) => <TableRow key={`${entry.steamId}:${entry.role ?? "unknown"}`} tabIndex={0} className={`cursor-pointer align-top hover:bg-muted/40 focus-visible:bg-muted/40 ${entry.rank <= 3 ? "bg-warning/5" : ""}`} onClick={() => selectPlayer(entry.steamId, entry.role)} onKeyDown={(event) => event.key === "Enter" && selectPlayer(entry.steamId, entry.role)}>
            <TableCell className="text-base">{entry.rank <= 3 ? <PlatformIcon value={["🥇", "🥈", "🥉"][entry.rank - 1]} fallback={Medal} /> : <span className="text-muted-foreground">{entry.rank}</span>}</TableCell>
            <TableCell><span onClick={(event) => event.stopPropagation()}><PlayerHoverLink playerId={entry.playerId} className="font-medium text-primary underline-offset-2 hover:underline">{entry.playerName}</PlayerHoverLink></span><div className="text-xs text-muted-foreground">Последняя активность: {entry.lastActivity ?? "—"}</div></TableCell>
            <TableCell className="text-sm">{labelForRole(entry.role)}</TableCell><TableCell className="font-semibold text-primary">{entry.rating ?? "—"}</TableCell><TableCell className="text-muted-foreground">{entry.points ?? "—"}</TableCell><TableCell className="text-xs text-muted-foreground">{entry.activity == null ? "—" : entry.activity.toFixed(2)}</TableCell><TableCell className="text-xs text-muted-foreground">{entry.playtimeHours ?? "—"}</TableCell><TableCell className="text-xs text-muted-foreground">{entry.qualifiedRounds ?? "—"}</TableCell><TableCell className="text-xs text-muted-foreground">{labelForDataQuality(entry.dataQuality ?? entry.eligibility)}</TableCell>
          </TableRow>)}{!leaderboard.leaderboard.length && <TableRow><TableCell colSpan={9} className="py-8 text-center text-muted-foreground">Нет данных</TableCell></TableRow>}</TableBody>
        </Table>}
      </CardContent>
      <div className="flex min-h-12 flex-wrap items-center justify-between gap-3 border-t px-4 py-2 text-xs text-muted-foreground"><span>Страница {leaderboard.pagination.page} из {leaderboard.pagination.totalPages} · всего {leaderboard.pagination.total.toLocaleString("ru-RU")}</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1 || leaderboardQuery.isFetching} onClick={() => setPage((current) => Math.max(1, current - 1))}>Назад</Button><Button variant="outline" size="sm" disabled={!leaderboard.pagination.hasNext || leaderboardQuery.isFetching} onClick={() => setPage((current) => current + 1)}>Дальше</Button></div></div>
    </Card>

    {canConfigure && <RatingConfigSection mode={mode} />}

    <Sheet open={Boolean(selectedPlayer)} onOpenChange={(open) => !open && closePlayer()}><SheetContent side="right" className="flex w-[min(96vw,42rem)] flex-col gap-0 p-0 sm:max-w-none"><SheetHeader className="border-b px-5 py-4 text-left"><SheetTitle className="text-base">Игрок {selectedPlayer?.steamId}</SheetTitle><SheetDescription>{mode === "pvp" ? "PvP" : "Mission"} · {drawerRole === "all" ? "Все роли" : labelForRole(drawerRole)}</SheetDescription><Select value={drawerRole} onValueChange={setDrawerRole}><SelectTrigger className="w-full" aria-label="Роль в превью"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все роли</SelectItem>{roles.filter((item) => item !== "all").map((item) => <SelectItem key={item} value={item}>{labelForRole(item)}</SelectItem>)}</SelectContent></Select></SheetHeader><div className="min-h-0 flex-1 overflow-y-auto p-5">{detailLoading ? <Skeleton className="h-96" /> : <RatingV2Investigation player={detailQuery.data} explanation={explanationQuery.data} events={eventsQuery.data} history={historyQuery.data} delivery={deliveryQuery.data} onEventSelect={(event) => setSelectedEvent({ mode, role: event.role ?? drawerRole, steam_id: selectedPlayer?.steamId, ...event })} />}</div></SheetContent></Sheet>
    <RatingEventDrawer open={selectedEvent !== null} event={selectedEvent ?? undefined} onClose={() => setSelectedEvent(null)} />
  </div>
}
