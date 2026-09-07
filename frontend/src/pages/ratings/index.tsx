import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Gift, Medal, Search, Star } from "lucide-react"
import { Link } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { api } from "@/lib/api"
import { RatingConfigSection } from "./config-section"
import { buildRatingV2Query, normalizeRatingLeaderboard, type RatingMetadataValue, type RatingMode } from "./rating-v2-contract"
import { RatingV2Investigation } from "./rating-v2-views"

const ROLE_LABELS: Record<string, string> = {
  captain: "Капитан", mechanic: "Механик", medic: "Врач", security: "СБ", engineer: "Инженер",
}
const FALLBACK_ROLES = ["captain", "mechanic", "medic", "security", "engineer"]

function displayMetadata(value: RatingMetadataValue): string {
  return value === null ? "—" : String(value)
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function RatingsPage() {
  const [mode, setMode] = useState<RatingMode>("mission")
  const [role, setRole] = useState("captain")
  const [searchInput, setSearchInput] = useState("")
  const [selectedSteamId, setSelectedSteamId] = useState("")
  const leaderboardQuery = useQuery({
    queryKey: ["ratings-v2", mode, role],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings?${buildRatingV2Query({ mode, role })}`),
  })
  const leaderboard = normalizeRatingLeaderboard(leaderboardQuery.data)
  const roles = leaderboard.roles.length ? leaderboard.roles : FALLBACK_ROLES
  const topLevel = asRecord(leaderboardQuery.data)
  const canConfigure = topLevel.is_superuser === true || topLevel.can_edit === true

  useEffect(() => {
    if (!roles.includes(role)) setRole(roles[0] ?? "captain")
  }, [role, roles])

  const detailQuery = useQuery({
    queryKey: ["rating-v2-player", mode, role, selectedSteamId],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedSteamId)}?${buildRatingV2Query({ mode, role })}`),
    enabled: Boolean(selectedSteamId),
  })
  const explanationQuery = useQuery({
    queryKey: ["rating-v2-explanation", mode, role, selectedSteamId],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedSteamId)}/explanation?${buildRatingV2Query({ mode, role })}`),
    enabled: Boolean(selectedSteamId),
  })
  const eventsQuery = useQuery({
    queryKey: ["rating-v2-events", mode, role, selectedSteamId],
    queryFn: () => api.get<unknown>(`/api/v2/admin/rating-events?${buildRatingV2Query({ mode, role, steamId: selectedSteamId })}`),
    enabled: Boolean(selectedSteamId),
  })
  const historyQuery = useQuery({
    queryKey: ["rating-v2-history", mode, role, selectedSteamId],
    queryFn: () => api.get<unknown>(`/api/v2/admin/ratings/${encodeURIComponent(selectedSteamId)}/history?${buildRatingV2Query({ mode, role })}`),
    enabled: Boolean(selectedSteamId),
  })
  const deliveryQuery = useQuery({
    queryKey: ["rating-v2-delivery", mode, selectedSteamId],
    queryFn: () => api.get<unknown>(`/api/v2/admin/rating-delivery-log?mode=${mode}&steam_id=${encodeURIComponent(selectedSteamId)}`),
    enabled: Boolean(selectedSteamId),
  })

  const selectPlayer = (steamId: string) => {
    const next = steamId.trim()
    if (next) setSelectedSteamId(next)
  }

  return <div className="space-y-4">
    <PageHeader
      title="Рейтинг"
      description="Mission и PvP — отдельные таблицы, расчёты и журнал изменений"
      icon={Star}
      actions={<Button variant="outline" asChild><Link to="/ratings/rewards"><Gift className="size-4" /> Награды</Link></Button>}
    />

    <div className="flex flex-wrap items-center gap-2">
      <Tabs value={mode} onValueChange={(value) => setMode(value as RatingMode)}>
        <TabsList><TabsTrigger value="mission">Mission</TabsTrigger><TabsTrigger value="pvp">PvP</TabsTrigger></TabsList>
      </Tabs>
      <Tabs value={role} onValueChange={setRole}>
        <TabsList className="flex-wrap">{roles.map((item) => <TabsTrigger key={item} value={item}>{ROLE_LABELS[item] ?? item}</TabsTrigger>)}</TabsList>
      </Tabs>
      <SearchInput value={searchInput} onChange={(event) => setSearchInput(event.target.value)} onKeyDown={(event) => event.key === "Enter" && selectPlayer(searchInput)} placeholder="SteamID игрока…" className="max-w-xs" />
      <Button variant="secondary" onClick={() => selectPlayer(searchInput)}><Search className="size-4" /> Открыть игрока</Button>
    </div>

    {canConfigure && <RatingConfigSection mode={mode} />}

    {leaderboard.extra.length > 0 && <Card><CardContent className="flex flex-wrap gap-2 p-3 text-xs text-muted-foreground">
      {leaderboard.extra.map(([key, value]) => <Badge key={key} variant="outline">{key}: {displayMetadata(value)}</Badge>)}
    </CardContent></Card>}

    <Card className="overflow-hidden py-0">
      <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">{mode === "pvp" ? "PvP" : "Mission"} · лидерборд · {ROLE_LABELS[role] ?? role}</CardTitle></CardHeader>
      <CardContent className="p-0">
        {leaderboardQuery.isLoading ? <div className="p-4"><Skeleton className="h-64" /></div> : <Table>
          <TableHeader><TableRow><TableHead className="w-12">#</TableHead><TableHead>Игрок</TableHead><TableHead>Рейтинг</TableHead><TableHead>Вклад</TableHead><TableHead>Активность</TableHead><TableHead>Часы</TableHead><TableHead>Раунды</TableHead><TableHead>Достоверность</TableHead><TableHead className="text-right">Просмотр</TableHead></TableRow></TableHeader>
          <TableBody>{leaderboard.leaderboard.map((entry) => <TableRow key={entry.steamId} className={entry.rank <= 3 ? "bg-warning/5" : ""}>
            <TableCell className="text-base">{entry.rank <= 3 ? <PlatformIcon value={["🥇", "🥈", "🥉"][entry.rank - 1]} fallback={Medal} /> : <span className="text-muted-foreground">{entry.rank}</span>}</TableCell>
            <TableCell><div className="font-medium">{entry.playerName}</div><div className="text-xs text-muted-foreground">Последняя активность: {entry.lastActivity ?? "—"}</div>{entry.extra.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{entry.extra.map(([key, value]) => <Badge key={key} variant="outline" className="text-[10px]">{key}: {displayMetadata(value)}</Badge>)}</div>}</TableCell>
            <TableCell className="font-semibold text-primary">{entry.rating ?? "—"}</TableCell>
            <TableCell className="text-muted-foreground">{entry.points ?? "—"}</TableCell>
            <TableCell className="text-xs text-muted-foreground">{entry.activity == null ? "—" : entry.activity.toFixed(2)}</TableCell>
            <TableCell className="text-xs text-muted-foreground">{entry.playtimeHours ?? "—"}</TableCell>
            <TableCell className="text-xs text-muted-foreground">{entry.qualifiedRounds ?? "—"}</TableCell>
            <TableCell className="text-xs text-muted-foreground">{entry.confidence === null ? entry.eligibility ?? "—" : `${Math.round(entry.confidence * 100)}%`}</TableCell>
            <TableCell className="text-right"><Button size="sm" variant="outline" onClick={() => selectPlayer(entry.steamId)}>Открыть</Button></TableCell>
          </TableRow>)}
          {!leaderboard.leaderboard.length && <TableRow><TableCell colSpan={9} className="py-8 text-center text-muted-foreground">Нет данных</TableCell></TableRow>}</TableBody>
        </Table>}
      </CardContent>
    </Card>

    {selectedSteamId && <section className="space-y-2">
      <div className="flex items-center justify-between"><h2 className="text-sm font-semibold">Игрок {selectedSteamId}</h2><span className="text-xs text-muted-foreground">{mode} · {role}</span></div>
      {(detailQuery.isLoading || explanationQuery.isLoading || eventsQuery.isLoading || historyQuery.isLoading || deliveryQuery.isLoading) ? <Skeleton className="h-64" /> : <RatingV2Investigation player={detailQuery.data} explanation={explanationQuery.data} events={eventsQuery.data} history={historyQuery.data} delivery={deliveryQuery.data} />}
    </section>}
  </div>
}
