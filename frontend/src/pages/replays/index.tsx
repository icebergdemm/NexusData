import { useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  History, PlayCircle, MapPin, Ship, Target, Users2, CheckCircle2, XCircle, Video, Crown, LogOut,
  Filter, X, Volume2, FileDown,
} from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { PlayerSummaryDialog } from "@/components/layout/PlayerSummaryDialog"
import { AdaptiveIcon } from "@/components/layout/PlatformIcon"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"

interface ReplayRow {
  id: number; server_name: string | null; submarine_name: string | null; level_name: string | null
  file_size: number | null; has_voice: boolean; recorded_at: string | null; uploaded_at: string | null
  replay_kind: "round" | "lobby" | "lobby_dump"
  dumped_by_name: string | null
}
interface ReplaysResponse {
  replays: ReplayRow[]; total: number; page: number; total_pages: number
  retention_days: number; can_delete: boolean; is_superuser: boolean
  can_request_lobby_dump: boolean
}
interface RoundPlayer {
  steamid: string
  player_name: string
  real_name?: string
  role?: string
  /** Профиль игрока в Nexus Data — null, если игрока в базе ещё нет. */
  player_id?: number | null
  /** Игрока не было в раунде на момент его окончания. */
  left?: boolean
  left_at?: string | null
}
interface RoundRow {
  id: number
  server_name: string | null
  submarine_name: string | null
  mission: string | null
  location: string | null
  started_at: string | null
  ended_at: string | null
  mission_completed: boolean | null
  reached_station: boolean | null
  players: RoundPlayer[]
  player_count: number
  player_left_count: number
  replay: ReplayRow | null
  lobby_replay: ReplayRow | null
  lobby_replay_status: "ready" | "waiting"
}
interface RoundsResponse {
  rounds: RoundRow[]; total: number; page: number; total_pages: number
  selected_server: string | null; servers: string[]; truncated: boolean
  can_request_lobby_dump: boolean
}

function fmtSize(n: number | null): string {
  if (!n) return "0 B"
  const units = ["B", "KB", "MB", "GB"]
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

function isCaptain(role: string | undefined): boolean {
  return !!role && role.toLowerCase().includes("captain")
}

function fmtDuration(startIso: string | null, endIso: string | null): string {
  if (!startIso || !endIso) return "-"
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (ms <= 0) return "-"
  const totalMin = Math.round(ms / 60000)
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  return h > 0 ? `${h}ч ${m}м` : `${m}м`
}

const ANY = "__any__"

function LobbyDumpButton({ serverName }: { serverName: string | null }) {
  const mutation = useMutation({
    mutationFn: () => api.post<{ action_id: number }>("/api/v1/admin/replays/lobby-dump", { server_name: serverName }),
    onSuccess: ({ action_id }) => toast.success(`Дамп лобби поставлен в очередь, action #${action_id}`),
    onError: (error: Error) => toast.error(error.message),
  })
  return (
    <Button
      size="sm"
      variant="outline"
      className="border-yellow-500/70 bg-yellow-500/10 text-yellow-700 hover:bg-yellow-500/20 dark:text-yellow-300"
      disabled={!serverName || mutation.isPending}
      onClick={(event) => { event.stopPropagation(); mutation.mutate() }}
    >
      <FileDown className="size-3.5" /> {mutation.isPending ? "Запрашиваем..." : "Принудительно получить запись лобби"}
    </Button>
  )
}

interface RoundFilters {
  q: string
  player: string
  captain: string
  submarine: string
  server: string
  dateFrom: string
  dateTo: string
  result: string
}

const EMPTY_FILTERS: RoundFilters = {
  q: "", player: "", captain: "", submarine: "", server: ANY, dateFrom: "", dateTo: "", result: ANY,
}

/** Быстрые пресеты периода — «за неделю» руками набирать обе даты незачем. */
const PERIOD_PRESETS: { label: string; days: number }[] = [
  { label: "Сегодня", days: 0 },
  { label: "7 дней", days: 7 },
  { label: "30 дней", days: 30 },
  { label: "90 дней", days: 90 },
]

function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

function RoundsFilterBar({ filters, setFilters, servers }: {
  filters: RoundFilters
  setFilters: (f: RoundFilters) => void
  servers: string[]
}) {
  const set = <K extends keyof RoundFilters>(key: K, value: RoundFilters[K]) =>
    setFilters({ ...filters, [key]: value })

  const applyPreset = (days: number) => {
    const to = new Date()
    const from = new Date()
    from.setDate(from.getDate() - days)
    setFilters({ ...filters, dateFrom: isoDay(from), dateTo: isoDay(to) })
  }

  const active = (Object.keys(EMPTY_FILTERS) as (keyof RoundFilters)[])
    .filter((k) => filters[k] !== EMPTY_FILTERS[k]).length

  return (
    <Card className="py-0">
      <CardContent className="space-y-3 p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1.5 sm:col-span-2">
            <Label className="text-xs text-muted-foreground">Поиск по всему раунду</Label>
            <SearchInput
              value={filters.q}
              onChange={(e) => set("q", e.target.value)}
              placeholder="Корабль, миссия, локация, игрок…"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Игрок</Label>
            <Input value={filters.player} onChange={(e) => set("player", e.target.value)} placeholder="Ник или steamid" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Капитан</Label>
            <Input value={filters.captain} onChange={(e) => set("captain", e.target.value)} placeholder="Ник или steamid" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Корабль</Label>
            <Input value={filters.submarine} onChange={(e) => set("submarine", e.target.value)} placeholder="Название корабля" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Сервер</Label>
            <Select value={filters.server} onValueChange={(v) => set("server", v)}>
              <SelectTrigger><SelectValue placeholder="Любой" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Любой</SelectItem>
                {servers.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Дата с</Label>
            <Input type="date" value={filters.dateFrom} onChange={(e) => set("dateFrom", e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Дата по</Label>
            <Input type="date" value={filters.dateTo} onChange={(e) => set("dateTo", e.target.value)} />
          </div>
          <div className="space-y-1.5 lg:col-span-2">
            <Label className="text-xs text-muted-foreground">Итог раунда</Label>
            <Select value={filters.result} onValueChange={(v) => set("result", v)}>
              <SelectTrigger><SelectValue placeholder="Любой" /></SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Любой</SelectItem>
                <SelectItem value="completed">Миссия выполнена</SelectItem>
                <SelectItem value="failed">Миссия провалена</SelectItem>
                <SelectItem value="reached">Добрались до станции</SelectItem>
                <SelectItem value="not_reached">Не добрались до станции</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1 text-xs text-muted-foreground"><Filter className="size-3.5" /> Период:</span>
          {PERIOD_PRESETS.map((p) => (
            <Button key={p.label} size="sm" variant="outline" className="h-7 text-xs" onClick={() => applyPreset(p.days)}>
              {p.label}
            </Button>
          ))}
          <div className="flex-1" />
          {active > 0 && (
            <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setFilters(EMPTY_FILTERS)}>
              <X className="size-3.5" /> Сбросить фильтры ({active})
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

/** Кликабельный игрок: открывает краткую карточку профиля, а из неё — полное дело. */
function PlayerChip({ player, onOpen }: { player: RoundPlayer; onOpen: (id: number) => void }) {
  const captain = isCaptain(player.role)
  const clickable = player.player_id != null
  return (
    <Badge
      variant={captain ? "default" : "outline"}
      onClick={clickable ? (e) => { e.stopPropagation(); onOpen(player.player_id!) } : undefined}
      className={[
        "gap-1 text-xs",
        player.left ? "border-warning/50 text-warning" : "",
        clickable ? "cursor-pointer hover:ring-1 hover:ring-primary/50" : "",
      ].join(" ")}
      title={[
        player.real_name ? `Настоящий ник: ${player.real_name}` : null,
        player.role ? `Должность: ${player.role}` : null,
        player.left ? "Вышел до конца раунда" : null,
        clickable ? "Открыть профиль" : "Игрока нет в базе Nexus Data",
      ].filter(Boolean).join(" · ")}
    >
      {captain && <Crown className="size-3" />} {player.player_name}
      {player.left && <LogOut className="size-3" />}
    </Badge>
  )
}

function RoundsTab() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [filters, setFiltersRaw] = useState<RoundFilters>(EMPTY_FILTERS)
  const [summaryPlayerId, setSummaryPlayerId] = useState<number | null>(null)

  // Любая смена фильтра возвращает на первую страницу: иначе после сужения
  // выборки пользователь оказывается на несуществующей странице и видит пусто.
  const setFilters = (f: RoundFilters) => { setFiltersRaw(f); setPage(1) }

  const debounced = useDebouncedValue(filters, 400)

  const queryString = useMemo(() => {
    const params = new URLSearchParams({ page: String(page) })
    if (debounced.q.trim()) params.set("q", debounced.q.trim())
    if (debounced.player.trim()) params.set("player", debounced.player.trim())
    if (debounced.captain.trim()) params.set("captain", debounced.captain.trim())
    if (debounced.submarine.trim()) params.set("submarine", debounced.submarine.trim())
    if (debounced.server !== ANY) params.set("server", debounced.server)
    if (debounced.dateFrom) params.set("date_from", debounced.dateFrom)
    if (debounced.dateTo) params.set("date_to", debounced.dateTo)
    if (debounced.result !== ANY) params.set("result", debounced.result)
    return params.toString()
  }, [page, debounced])

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["rounds", queryString],
    queryFn: () => api.get<RoundsResponse>(`/api/v1/admin/replays/rounds?${queryString}`),
    placeholderData: (prev) => prev,
  })

  return (
    <div className="space-y-3">
      <RoundsFilterBar filters={filters} setFilters={setFilters} servers={data?.servers ?? []} />

      <div className="flex items-center justify-between px-1 text-xs text-muted-foreground">
        <span>Найдено раундов: {data?.total ?? 0}{isFetching && " · обновление…"}</span>
        {data?.truncated && (
          <span className="text-warning">Показаны не все совпадения — сузьте период поиска</span>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-3">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-32" />)}</div>
      ) : (
        <>
          {data?.rounds.map((r) => (
            <Card key={r.id} className="cursor-pointer overflow-hidden py-0 transition-colors hover:border-primary/40" onClick={() => navigate(`/replays/rounds/${r.id}`)}>
              <CardContent className="p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{r.server_name ?? "-"}</Badge>
                  <span className="font-semibold">{r.submarine_name ?? "Раунд"}</span>
                  <span className="text-xs text-muted-foreground">{fmtDate(r.started_at)} - {fmtDate(r.ended_at)} · {fmtDuration(r.started_at, r.ended_at)}</span>
                  <div className="flex-1" />
                  {r.mission_completed != null && (
                    <Badge variant={r.mission_completed ? "default" : "destructive"} className={r.mission_completed ? "bg-success text-white" : ""}>
                      {r.mission_completed ? <CheckCircle2 className="size-3" /> : <XCircle className="size-3" />}
                      {r.mission_completed ? "Миссия выполнена" : "Миссия провалена"}
                    </Badge>
                  )}
                  {r.reached_station != null && (
                    <Badge variant={r.reached_station ? "default" : "secondary"} className={r.reached_station ? "bg-info text-white" : ""}>
                      {r.reached_station ? "Добрались до станции" : "Не добрались до станции"}
                    </Badge>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
                  <div className="flex items-center gap-1.5 text-muted-foreground"><MapPin className="size-3.5 shrink-0" /> {r.location ?? "-"}</div>
                  <div className="flex items-center gap-1.5 text-muted-foreground"><Ship className="size-3.5 shrink-0" /> {r.submarine_name ?? "-"}</div>
                  <div className="flex items-center gap-1.5 text-muted-foreground"><Target className="size-3.5 shrink-0" /> {r.mission ?? "-"}</div>
                  <div className="flex items-center gap-1.5 text-muted-foreground"><Users2 className="size-3.5 shrink-0" />
                    {r.player_count} игроков
                    {r.player_left_count > 0 && (
                      <span className="text-warning">· {r.player_left_count} вышли</span>
                    )}
                  </div>
                </div>
                <div className="mt-3 flex items-center justify-between border-t pt-3">
                  <div className="flex flex-wrap gap-1">
                    {r.players.slice(0, 8).map((p) => (
                      <PlayerChip key={p.steamid} player={p} onOpen={setSummaryPlayerId} />
                    ))}
                    {r.players.length > 8 && <Badge variant="outline" className="text-xs">+{r.players.length - 8}</Badge>}
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    {r.replay ? (
                      <Button size="sm" onClick={(e) => { e.stopPropagation(); navigate(`/replays/${r.replay!.id}`) }}><Video className="size-3.5" /> Демо раунда</Button>
                    ) : <span className="text-xs text-muted-foreground">Демо не найдено</span>}
                    {r.lobby_replay ? (
                      <Button
                        variant="outline"
                        size="sm"
                        className={r.lobby_replay.replay_kind === "lobby_dump" ? "border-yellow-500 bg-yellow-500/10 text-yellow-700 dark:text-yellow-300" : undefined}
                        onClick={(e) => { e.stopPropagation(); navigate(`/replays/${r.lobby_replay!.id}`) }}
                      ><Volume2 className="size-3.5" /> {r.lobby_replay.replay_kind === "lobby_dump" ? `Дамп лобби · ${r.lobby_replay.dumped_by_name ?? "неизвестный пользователь"}` : "Демо лобби"}</Button>
                    ) : <span className="text-xs text-muted-foreground">Ожидаем демо лобби...</span>}
                    {data.can_request_lobby_dump && <LobbyDumpButton serverName={r.server_name} />}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
          {!data?.rounds.length && (
            <Card><CardContent className="py-12 text-center text-muted-foreground">
              Раундов по заданным условиям не нашлось
            </CardContent></Card>
          )}
        </>
      )}

      {data && data.total_pages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
          <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages}</span>
          <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
        </div>
      )}

      <PlayerSummaryDialog
        playerId={summaryPlayerId}
        open={summaryPlayerId != null}
        onOpenChange={(open) => { if (!open) setSummaryPlayerId(null) }}
      />
    </div>
  )
}

function DemosTab() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ["replays", page],
    queryFn: () => api.get<ReplaysResponse>(`/api/v1/admin/replays?page=${page}`),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/replays/${id}`),
    onSuccess: () => { toast.success("Демо-запись удалена"); queryClient.invalidateQueries({ queryKey: ["replays"] }) },
  })

  return (
    <Card className="overflow-hidden py-0">
      <CardContent className="p-0">
        {isLoading ? <Skeleton className="m-4 h-64" /> : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>Корабль</TableHead><TableHead>Сервер</TableHead><TableHead>Размер</TableHead><TableHead>Голос</TableHead><TableHead>Загружен</TableHead><TableHead /></TableRow>
            </TableHeader>
            <TableBody>
              {data?.replays.map((r) => (
                <TableRow key={r.id} className={r.replay_kind === "lobby_dump" ? "cursor-pointer bg-yellow-500/10 hover:bg-yellow-500/15" : "cursor-pointer"} onClick={() => navigate(`/replays/${r.id}`)}>
                  <TableCell className="font-medium">
                    {r.replay_kind === "round" ? (r.submarine_name ?? "Раунд") : "Лобби"} #{r.id}
                    <Badge variant={r.replay_kind === "round" ? "secondary" : "outline"} className={r.replay_kind === "lobby_dump" ? "ml-2 border-yellow-500 bg-yellow-500/15 text-yellow-700 dark:text-yellow-300" : "ml-2"}>
                      {r.replay_kind === "lobby_dump" ? "Принудительный дамп" : r.replay_kind === "lobby" ? "Лобби" : "Раунд"}
                    </Badge>
                    {r.level_name && r.replay_kind === "round" && <span className="text-xs text-muted-foreground"> · {r.level_name}</span>}
                    {r.replay_kind === "lobby_dump" && <div className="mt-1 text-xs text-yellow-700 dark:text-yellow-300">Инициировал: {r.dumped_by_name ?? "неизвестный пользователь"}</div>}
                  </TableCell>
                  <TableCell><Badge variant="secondary">{r.server_name ?? "-"}</Badge></TableCell>
                  <TableCell>{fmtSize(r.file_size)}</TableCell>
                  <TableCell>{r.has_voice ? <AdaptiveIcon emoji="🔊" icon={Volume2} /> : "-"}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{fmtDate(r.uploaded_at)}</TableCell>
                  <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                    <div className="flex justify-end gap-2">
                      {data?.can_request_lobby_dump && <LobbyDumpButton serverName={r.server_name} />}
                      {data?.can_delete && (
                      <Button size="sm" variant="destructive" onClick={() => deleteMutation.mutate(r.id)}>Удалить</Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {!data?.replays.length && <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">Демо-записей нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        )}
        {data && data.total_pages > 1 && (
          <div className="flex items-center justify-center gap-2 border-t bg-muted/30 py-3">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
            <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages}</span>
            <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function ReplaysListPage() {
  return (
    <div>
      <PageHeader title="Прошедшие раунды" description="История раундов и записи демо для просмотра" icon={PlayCircle} />
      <Tabs defaultValue="rounds" className="space-y-4">
        <TabsList>
          <TabsTrigger value="rounds" className="gap-1.5"><History className="size-3.5" /> Раунды</TabsTrigger>
          <TabsTrigger value="demos" className="gap-1.5"><Video className="size-3.5" /> Демо записи</TabsTrigger>
        </TabsList>
        <TabsContent value="rounds"><RoundsTab /></TabsContent>
        <TabsContent value="demos"><DemosTab /></TabsContent>
      </Tabs>
    </div>
  )
}
