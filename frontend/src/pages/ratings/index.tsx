import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Gift, Medal, Search, Star } from "lucide-react"
import { Link } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { api } from "@/lib/api"
import { RatingConfigSection } from "./config-section"
import { buildRatingV2Query, normalizeRatingLeaderboard, type RatingMetadataValue, type RatingMode } from "./rating-v2-contract"
import { RatingEventDrawer, RatingV2Investigation } from "./rating-v2-views"

const ROLE_LABELS: Record<string, string> = { captain: "Капитан", mechanic: "Механик", medic: "Врач", security: "СБ", engineer: "Инженер" }
const FALLBACK_ROLES = ["captain", "mechanic", "medic", "security", "engineer"]
const PAGE_SIZE = 25

function displayMetadata(value: RatingMetadataValue): string {
  return value === null ? "—" : String(value)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}
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
  const [page, setPage] = useState(1)
  const [selectedPlayer, setSelectedPlayer] = useState<{ steamId: string; role: string } | null>(null)
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
  const selectedRole = selectedPlayer?.role ?? (role === "all" ? "captain" : role)

  useEffect(() => {
    setPage(1)
  }, [mode, role])

  const detailQuery = useQuery({
    queryKey: ["rating-v2-player", mode, selectedPlayer?.steamId, selectedRole],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedPlayer?.steamId ?? "")}?${buildRatingV2Query({ mode, role: selectedRole })}`),
    enabled: Boolean(selectedPlayer),
  })
  const explanationQuery = useQuery({
    queryKey: ["rating-v2-explanation", mode, selectedPlayer?.steamId, selectedRole],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedPlayer?.steamId ?? "")}/explanation?${buildRatingV2Query({ mode, role: selectedRole })}`),
    enabled: Boolean(selectedPlayer),
  })
  const eventsQuery = useQuery({
    queryKey: ["rating-v2-events", mode, selectedPlayer?.steamId, selectedRole],
    queryFn: () => api.get<unknown>(`/api/v2/admin/rating-events?${buildRatingV2Query({ mode, role: selectedRole, page: 1, pageSize: PAGE_SIZE, steamId: selectedPlayer?.steamId })}`),
    enabled: Boolean(selectedPlayer),
  })
  const historyQuery = useQuery({
    queryKey: ["rating-v2-history", mode, selectedPlayer?.steamId, selectedRole],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedPlayer?.steamId ?? "")}/history?${buildRatingV2Query({ mode, role: selectedRole })}`),
    enabled: Boolean(selectedPlayer),
  })
  const deliveryQuery = useQuery({
    queryKey: ["rating-v2-delivery", mode, selectedPlayer?.steamId],
    queryFn: () => api.get<unknown>(`/api/v2/admin/rating-delivery-log?mode=${mode}&steam_id=${encodeURIComponent(selectedPlayer?.steamId ?? "")}`),
    enabled: Boolean(selectedPlayer),
  })

  const selectPlayer = (steamId: string, rowRole?: string | null) => {
    const next = steamId.trim()
    if (next) setSelectedPlayer({ steamId: next, role: rowRole ?? (role === "all" ? "captain" : role) })
  }
  const closePlayer = () => { setSelectedPlayer(null); setSelectedEvent(null) }
  const detailLoading = detailQuery.isLoading || explanationQuery.isLoading || eventsQuery.isLoading || historyQuery.isLoading || deliveryQuery.isLoading

  return <div className="space-y-4">
    <PageHeader title="Рейтинг" description="Mission и PvP — отдельные таблицы, расчёты и журнал изменений" icon={Star} actions={<Button variant="outline" asChild><Link to="/ratings/rewards"><Gift className="size-4" /> Награды</Link></Button>} />

    <div className="flex flex-wrap items-center gap-2">
      <Tabs value={mode} onValueChange={(value) => setMode(value as RatingMode)}><TabsList><TabsTrigger value="mission">Mission</TabsTrigger><TabsTrigger value="pvp">PvP</TabsTrigger></TabsList></Tabs>
      <Select value={role} onValueChange={setRole}><SelectTrigger className="w-44" aria-label="Фильтр роли"><SelectValue placeholder="Все роли" /></SelectTrigger><SelectContent>{roleOptions.map((item) => <SelectItem key={item} value={item}>{item === "all" ? "Все роли" : labelForRole(item)}</SelectItem>)}</SelectContent></Select>
      <SearchInput value={searchInput} onChange={(event) => setSearchInput(event.target.value)} onKeyDown={(event) => event.key === "Enter" && selectPlayer(searchInput)} placeholder="SteamID игрока…" className="max-w-xs" />
      <span className="text-xs text-muted-foreground"><Search className="mr-1 inline size-3.5" />Enter — открыть игрока</span>
    </div>

    {leaderboard.extra.length > 0 && <Card><CardContent className="flex flex-wrap gap-2 p-3 text-xs text-muted-foreground">{leaderboard.extra.map(([key, value]) => <Badge key={key} variant="outline">{key}: {displayMetadata(value)}</Badge>)}</CardContent></Card>}

    <Card className="overflow-hidden py-0">
      <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">{mode === "pvp" ? "PvP" : "Mission"} · рейтинг игроков</CardTitle></CardHeader>
      <CardContent className="p-0">
        {leaderboardQuery.isLoading ? <div className="p-4"><Skeleton className="h-64" /></div> : <Table>
          <TableHeader><TableRow><TableHead className="w-12">#</TableHead><TableHead>Игрок</TableHead><TableHead>Роль</TableHead><TableHead>Рейтинг</TableHead><TableHead>Вклад</TableHead><TableHead>Активность</TableHead><TableHead>Часы</TableHead><TableHead>Раунды</TableHead><TableHead>Данные</TableHead></TableRow></TableHeader>
          <TableBody>{leaderboard.leaderboard.map((entry) => <TableRow key={`${entry.steamId}:${entry.role ?? "unknown"}`} tabIndex={0} className={`cursor-pointer align-top hover:bg-muted/40 focus-visible:bg-muted/40 ${entry.rank <= 3 ? "bg-warning/5" : ""}`} onClick={() => selectPlayer(entry.steamId, entry.role)} onKeyDown={(event) => event.key === "Enter" && selectPlayer(entry.steamId, entry.role)}>
            <TableCell className="text-base">{entry.rank <= 3 ? <PlatformIcon value={["🥇", "🥈", "🥉"][entry.rank - 1]} fallback={Medal} /> : <span className="text-muted-foreground">{entry.rank}</span>}</TableCell>
            <TableCell><div className="font-medium">{entry.playerName}</div><div className="text-xs text-muted-foreground">Последняя активность: {entry.lastActivity ?? "—"}</div>{entry.extra.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{entry.extra.map(([key, value]) => <Badge key={key} variant="outline" className="text-[10px]">{key}: {displayMetadata(value)}</Badge>)}</div>}</TableCell>
            <TableCell className="text-sm">{labelForRole(entry.role)}</TableCell><TableCell className="font-semibold text-primary">{entry.rating ?? "—"}</TableCell><TableCell className="text-muted-foreground">{entry.points ?? "—"}</TableCell><TableCell className="text-xs text-muted-foreground">{entry.activity == null ? "—" : entry.activity.toFixed(2)}</TableCell><TableCell className="text-xs text-muted-foreground">{entry.playtimeHours ?? "—"}</TableCell><TableCell className="text-xs text-muted-foreground">{entry.qualifiedRounds ?? "—"}</TableCell><TableCell className="text-xs text-muted-foreground">{labelForDataQuality(entry.dataQuality ?? entry.eligibility)}</TableCell>
          </TableRow>)}{!leaderboard.leaderboard.length && <TableRow><TableCell colSpan={9} className="py-8 text-center text-muted-foreground">Нет данных</TableCell></TableRow>}</TableBody>
        </Table>}
      </CardContent>
      <div className="flex min-h-12 flex-wrap items-center justify-between gap-3 border-t px-4 py-2 text-xs text-muted-foreground"><span>Страница {leaderboard.pagination.page} из {leaderboard.pagination.totalPages} · всего {leaderboard.pagination.total.toLocaleString("ru-RU")}</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1 || leaderboardQuery.isFetching} onClick={() => setPage((current) => Math.max(1, current - 1))}>Назад</Button><Button variant="outline" size="sm" disabled={!leaderboard.pagination.hasNext || leaderboardQuery.isFetching} onClick={() => setPage((current) => current + 1)}>Дальше</Button></div></div>
    </Card>

    {canConfigure && <RatingConfigSection mode={mode} />}

    <Sheet open={Boolean(selectedPlayer)} onOpenChange={(open) => !open && closePlayer()}><SheetContent side="right" className="flex w-[min(96vw,42rem)] flex-col gap-0 p-0 sm:max-w-none"><SheetHeader className="border-b px-5 py-4 text-left"><SheetTitle className="text-base">Игрок {selectedPlayer?.steamId}</SheetTitle><SheetDescription>{mode === "pvp" ? "PvP" : "Mission"} · {labelForRole(selectedPlayer?.role)}</SheetDescription></SheetHeader><div className="min-h-0 flex-1 overflow-y-auto p-5">{detailLoading ? <Skeleton className="h-96" /> : <RatingV2Investigation player={detailQuery.data} explanation={explanationQuery.data} events={eventsQuery.data} history={historyQuery.data} delivery={deliveryQuery.data} onEventSelect={(event) => setSelectedEvent({ mode, role: selectedPlayer?.role, steam_id: selectedPlayer?.steamId, ...event })} />}</div></SheetContent></Sheet>
    <RatingEventDrawer open={selectedEvent !== null} event={selectedEvent ?? undefined} onClose={() => setSelectedEvent(null)} />
  </div>
}
