import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { toast } from "sonner"
import { ScrollText, RefreshCw } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { PlayerSummaryDialog } from "@/components/layout/PlayerSummaryDialog"
import { api } from "@/lib/api"
import { ACTION_COLOR, ACTION_TYPES, type LogEvent } from "./constants"
import { PlayerNamesFilter } from "./player-filter"
import { useCurrentAdmin } from "@/lib/auth"

interface LogsResponse {
  events: LogEvent[]
  /** null — бэкенд не смог посчитать совпадения за отведённое время
   *  (services_logs._bounded_count). Строки при этом всё равно приходят. */
  total: number | null
  total_is_capped: boolean
  /** Есть ли страница после текущей. Считается независимо от total (запросом
   *  на одну строку больше), поэтому листание работает и без точного счёта. */
  has_more: boolean
  page: number
  page_size: number
  total_pages: number | null
}
interface LogStatEntry { event_count: number; last_refresh: string | null; oldest_event: string | null; newest_event: string | null }
interface SearchIndexStatus { available: boolean; ready: boolean; progress_pct: number }

export function LogsPage() {
  const { data: admin } = useCurrentAdmin()
  const [server, setServer] = useState("all")
  const [category, setCategory] = useState("all")
  const [actionType, setActionType] = useState("all")
  const [consoleOnly, setConsoleOnly] = useState(false)
  const [playerNames, setPlayerNames] = useState<string[]>([])
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [summaryPlayerId, setSummaryPlayerId] = useState<number | null>(null)
  const queryClient = useQueryClient()

  // Флажок "только консольные команды" — это то же самое, что выбрать тип
  // действия console, просто отдельным переключателем, чтобы не лезть в
  // выпадающий список ради самого частого запроса модераторов. Держит select
  // синхронным (взаимоисключающе), а не отдельным AND-условием — "console"
  // это единственный action_type, если галочка стоит.
  const effectiveActionType = consoleOnly ? "console" : actionType

  // Text filters run an unindexed substring scan over the whole log table
  // server-side, so they're debounced — without this every keystroke fired
  // its own request ("владимир" = 8 parallel full scans), which saturated
  // the backend's worker threads and made the proxy serve the maintenance
  // page. The inputs stay bound to the raw state, so typing is unaffected.
  const debouncedSearch = useDebouncedValue(search.trim())
  // A 1-character substring matches essentially every row, making the query
  // maximally expensive while being useless as a filter — wait for 2+.
  const effectiveSearch = debouncedSearch.length >= 2 ? debouncedSearch : ""

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["logs-events", server, category, effectiveActionType, playerNames, effectiveSearch, page],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), page_size: "100" })
      if (server !== "all") params.set("server", server)
      if (category !== "all") params.set("category", category)
      if (effectiveActionType !== "all") params.set("action_type", effectiveActionType)
      // Комбинация фильтров, как поиск с несколькими условиями в Discord:
      // несколько игроков + свободный текст + сервер + категория + тип
      // действия — всё применяется одновременно (AND на бэкенде).
      if (playerNames.length) params.set("player_names", playerNames.join(","))
      if (effectiveSearch) params.set("search", effectiveSearch)
      return api.get<LogsResponse>(`/api/v1/admin/logs/events?${params}`)
    },
    // Keep showing the previous page/results while the next query runs
    // instead of flashing an empty table between keystrokes.
    placeholderData: (prev) => prev,
  })

  const { data: stats } = useQuery({
    queryKey: ["logs-stats"],
    queryFn: () => api.get<Record<string, LogStatEntry>>("/api/v1/admin/logs/stats"),
  })

  // Пока полнотекстовый индекс достраивается в фоне, поиск по тексту идёт по
  // медленному пути — показываем прогресс, чтобы это не выглядело поломкой.
  const { data: searchIndex } = useQuery({
    queryKey: ["logs-search-index"],
    queryFn: () => api.get<SearchIndexStatus>("/api/v1/admin/logs/search-index"),
    refetchInterval: (q) => (q.state.data?.ready === false ? 15000 : false),
  })

  const refresh = useMutation({
    mutationFn: () => api.post("/api/v1/admin/logs/refresh"),
    onSuccess: () => { toast.success("Обновление логов запущено в фоне"); setTimeout(() => queryClient.invalidateQueries({ queryKey: ["logs-stats"] }), 5000) },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div>
      <PageHeader
        title="Логи сервера"
        description="События игровых серверов: действия игроков, консоль, подключения"
        icon={ScrollText}
        actions={admin?.is_superuser && (
          <Button variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            <RefreshCw className={`h-4 w-4 ${refresh.isPending ? "animate-spin" : ""}`} /> Обновить из SFTP
          </Button>
        )}
      />

      {stats && Object.keys(stats).length > 0 && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          {Object.entries(stats).map(([sn, s]) => (
            <Card key={sn}>
              <CardContent className="flex items-center justify-between pt-6 text-sm">
                <div>
                  <div className="font-semibold">{sn}</div>
                  <div className="text-xs text-muted-foreground">
                    Обновлено: {s.last_refresh ? new Date(s.last_refresh).toLocaleString("ru-RU") : "-"}
                  </div>
                </div>
                <Badge variant="secondary">{s.event_count.toLocaleString("ru-RU")} событий</Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {searchIndex?.available && !searchIndex.ready && (
        <Card className="mb-4 border-warning/40">
          <CardContent className="pt-6 text-sm text-muted-foreground">
            Строится поисковый индекс по истории логов — {searchIndex.progress_pct}%.
            Поиск по тексту уже работает, но пока медленнее обычного.
          </CardContent>
        </Card>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Поиск по тексту события (от 2 символов)..." className="max-w-56" />
        <PlayerNamesFilter names={playerNames} onChange={(v) => { setPlayerNames(v); setPage(1) }} server={server} />
        <Select value={server} onValueChange={(v) => { setServer(v); setPage(1) }}>
          <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все серверы</SelectItem>
            <SelectItem value="ASPER1">ASPER1</SelectItem>
            <SelectItem value="ASPER2">ASPER2</SelectItem>
          </SelectContent>
        </Select>
        <Select value={category} onValueChange={(v) => { setCategory(v); setPage(1) }}>
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все категории</SelectItem>
            <SelectItem value="player">Игровые</SelectItem>
            <SelectItem value="technical">Технические</SelectItem>
          </SelectContent>
        </Select>
        <Select value={actionType} onValueChange={(v) => { setActionType(v); setPage(1) }} disabled={consoleOnly}>
          <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все типы действий</SelectItem>
            {ACTION_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
          </SelectContent>
        </Select>
        <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Checkbox checked={consoleOnly} onCheckedChange={(c) => { setConsoleOnly(c === true); setPage(1) }} />
          Только консольные команды
        </label>
        {data && (
          <span className="ml-auto text-xs text-muted-foreground">
            {data.total != null ? `${data.total.toLocaleString("ru-RU")}${data.total_is_capped ? "+" : ""} событий` : "много событий"}
            {isFetching && !isLoading && " · обновление…"}
          </span>
        )}
      </div>

      {/* Запрос логов может не дойти (таймаут на тяжёлой выборке, 429 от
          лимитера, обрыв): react-query оставляет на экране строки ПРЕДЫДУЩЕГО
          запроса (placeholderData), и молчаливый провал выглядит так, будто
          новый фильтр или следующая страница «не работают». Поэтому ошибку
          показываем явно и даём повторить. */}
      {isError && (
        <Card className="mb-4 border-destructive/50">
          <CardContent className="flex flex-wrap items-center justify-between gap-2 pt-6 text-sm">
            <span className="text-destructive">
              Не удалось загрузить события: {(error as Error)?.message || "ошибка запроса"}.
              {" "}Ниже — результат предыдущего запроса, он не соответствует текущим фильтрам.
            </span>
            <Button variant="outline" size="sm" onClick={() => refetch()}>Повторить</Button>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-8" />)}</div> : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-36">Время</TableHead>
                    <TableHead className="w-24">Сервер</TableHead>
                    <TableHead className="w-40">Игрок</TableHead>
                    <TableHead className="w-32">Действие</TableHead>
                    <TableHead>Детали</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.events.map((e, i) => (
                    <TableRow key={i}>
                      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">{new Date(e.timestamp).toLocaleString("ru-RU")}</TableCell>
                      <TableCell><Badge variant="outline">{e.server_name ?? "-"}</Badge></TableCell>
                      <TableCell className="truncate font-medium">
                        {e.player_name ? (
                          e.player_id ? (
                            <button
                              type="button"
                              className="text-left text-primary underline-offset-2 hover:underline"
                              onClick={() => setSummaryPlayerId(e.player_id!)}
                            >
                              {e.player_name}
                            </button>
                          ) : e.player_name
                        ) : "-"}
                      </TableCell>
                      <TableCell>
                        <span className={`font-mono text-xs ${ACTION_COLOR[e.action_type] ?? "text-foreground"}`}>{e.action_type}</span>
                      </TableCell>
                      <TableCell className="max-w-md truncate text-sm text-muted-foreground" title={e.details ?? undefined}>{e.details ?? "-"}</TableCell>
                    </TableRow>
                  ))}
                  {!data?.events.length && <TableRow><TableCell colSpan={5} className="py-10 text-center text-muted-foreground">Событий не найдено</TableCell></TableRow>}
                </TableBody>
              </Table>
              {/* Листание опирается на has_more, а не на total_pages: точное
                  число совпадений под фильтром бэкенд считать не обязан (и
                  специально не считает, когда это дорого), а «Вперёд» должно
                  работать в любом случае. */}
              {data && (data.has_more || page > 1) && (
                <div className="flex items-center justify-center gap-2 border-t p-3">
                  <Button variant="outline" size="sm" disabled={page <= 1 || isFetching} onClick={() => setPage((p) => p - 1)}>Назад</Button>
                  <span className="text-sm text-muted-foreground">
                    Страница {page}
                    {data.total_pages != null && ` из ${data.total_pages}`}
                    {data.total != null && ` (${data.total.toLocaleString("ru-RU")}${data.total_is_capped ? "+" : ""})`}
                  </span>
                  <Button variant="outline" size="sm" disabled={!data.has_more || isFetching} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <PlayerSummaryDialog playerId={summaryPlayerId} open={summaryPlayerId != null} onOpenChange={(o) => !o && setSummaryPlayerId(null)} />
    </div>
  )
}
