import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  CalendarClock,
  Filter,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Search,
} from "lucide-react"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { PlayerSummaryDialog } from "@/components/layout/PlayerSummaryDialog"
import { api, ApiError } from "@/lib/api"
import { ACTION_COLOR, ACTION_TYPES, type LogEvent } from "./constants"
import { PlayerNamesFilter } from "./player-filter"
import { useCurrentAdmin } from "@/lib/auth"

interface LogsResponse {
  events: LogEvent[]
  /** null — подсчёт не уложился в короткий бюджет, сами строки доступны. */
  total: number | null
  total_is_capped: boolean
  has_more: boolean
  next_cursor: string | null
  pagination: "offset" | "cursor"
  page: number
  page_size: number
  total_pages: number | null
}

interface LogStatEntry {
  event_count: number
  last_refresh: string | null
  oldest_event: string | null
  newest_event: string | null
}

interface SearchIndexStatus { available: boolean; ready: boolean; progress_pct: number }
type PlayerScope = "actor" | "related"

interface LogFilters {
  server: string
  category: string
  actionType: string
  consoleOnly: boolean
  playerNames: string[]
  playerScope: PlayerScope
  search: string
  timeFrom: string
  timeTo: string
}

const makeDefaultFilters = (): LogFilters => ({
  server: "all",
  category: "all",
  actionType: "all",
  consoleOnly: false,
  playerNames: [],
  playerScope: "actor",
  search: "",
  timeFrom: "",
  timeTo: "",
})

const TIME_PRESETS = [
  { label: "1 час", hours: 1 },
  { label: "6 часов", hours: 6 },
  { label: "24 часа", hours: 24 },
  { label: "7 дней", hours: 24 * 7 },
] as const

function toLocalDateTimeValue(date: Date) {
  const localDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return localDate.toISOString().slice(0, 16)
}

function filterSignature(filters: LogFilters) {
  return [
    filters.server,
    filters.category,
    filters.actionType,
    filters.consoleOnly ? "1" : "0",
    filters.playerNames.join("\u0000"),
    filters.playerScope,
    filters.search,
    filters.timeFrom,
    filters.timeTo,
  ].join("\u0001")
}

function activeFilterCount(filters: LogFilters) {
  return Number(filters.server !== "all")
    + Number(filters.category !== "all")
    + Number(filters.consoleOnly || filters.actionType !== "all")
    + Number(filters.playerNames.length > 0)
    + Number(Boolean(filters.search))
    + Number(Boolean(filters.timeFrom || filters.timeTo))
}

function logSearchErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return `HTTP ${error.status}: ${error.message || "ошибка запроса"}`
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return "Не удалось получить ответ от сервера"
}

export function LogsPage() {
  const { data: admin } = useCurrentAdmin()
  const [draftFilters, setDraftFilters] = useState<LogFilters>(makeDefaultFilters)
  const [appliedFilters, setAppliedFilters] = useState<LogFilters>(makeDefaultFilters)
  // Первый элемент означает начало выборки. Каждый следующий — непрозрачная
  // позиция последней строки предыдущей страницы; поэтому «Назад» не требует
  // OFFSET и просто снимает один курсор со стека.
  const [cursorStack, setCursorStack] = useState<Array<string | null>>([null])
  const [summaryPlayerId, setSummaryPlayerId] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const appliedActionType = appliedFilters.consoleOnly ? "console" : appliedFilters.actionType
  const draftActionType = draftFilters.consoleOnly ? "console" : draftFilters.actionType
  const hasPendingChanges = filterSignature(draftFilters) !== filterSignature(appliedFilters)
  const appliedCount = activeFilterCount(appliedFilters)
  const currentCursor = cursorStack[cursorStack.length - 1]
  const pageNumber = cursorStack.length

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: [
      "logs-events",
      appliedFilters.server,
      appliedFilters.category,
      appliedActionType,
      appliedFilters.playerNames.join("\u0000"),
      appliedFilters.playerScope,
      appliedFilters.search,
      appliedFilters.timeFrom,
      appliedFilters.timeTo,
      currentCursor,
    ],
    queryFn: () => {
      const params = new URLSearchParams({ pagination: "cursor", page_size: "100" })
      if (currentCursor) params.set("cursor", currentCursor)
      if (appliedFilters.server !== "all") params.set("server", appliedFilters.server)
      if (appliedFilters.category !== "all") params.set("category", appliedFilters.category)
      if (appliedActionType !== "all") params.set("action_type", appliedActionType)
      if (appliedFilters.playerNames.length) {
        params.set("player_names", appliedFilters.playerNames.join(","))
        params.set("player_scope", appliedFilters.playerScope)
      }
      if (appliedFilters.search) params.set("search", appliedFilters.search)
      if (appliedFilters.timeFrom) params.set("time_from", appliedFilters.timeFrom)
      if (appliedFilters.timeTo) params.set("time_to", appliedFilters.timeTo)
      return api.get<LogsResponse>(`/api/v1/admin/logs/events?${params}`)
    },
    // Тяжёлый запрос, который уже вернул 503, не должен автоматически
    // запускаться второй раз и занимать ещё один поток SQLite.
    retry: false,
    placeholderData: (previous) => previous,
  })

  const { data: stats } = useQuery({
    queryKey: ["logs-stats"],
    queryFn: () => api.get<Record<string, LogStatEntry>>("/api/v1/admin/logs/stats"),
  })

  const { data: searchIndex } = useQuery({
    queryKey: ["logs-search-index"],
    queryFn: () => api.get<SearchIndexStatus>("/api/v1/admin/logs/search-index"),
    refetchInterval: (query) => (query.state.data?.ready === false ? 15000 : false),
  })

  const refresh = useMutation({
    mutationFn: () => api.post("/api/v1/admin/logs/refresh"),
    onSuccess: () => {
      toast.success("Обновление логов запущено в фоне")
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["logs-stats"] }), 5000)
    },
    onError: (refreshError: Error) => toast.error(refreshError.message),
  })

  const updateDraft = <Key extends keyof LogFilters>(key: Key, value: LogFilters[Key]) => {
    setDraftFilters((current) => ({ ...current, [key]: value }))
  }

  const applyFilters = (event: React.FormEvent) => {
    event.preventDefault()
    const trimmedSearch = draftFilters.search.trim()
    if (trimmedSearch.length === 1) {
      toast.error("Для поиска по тексту введите минимум 2 символа")
      return
    }
    setAppliedFilters({
      ...draftFilters,
      search: trimmedSearch,
      playerNames: [...draftFilters.playerNames],
    })
    setDraftFilters((current) => ({ ...current, search: trimmedSearch }))
    setCursorStack([null])
  }

  const resetFilters = () => {
    setDraftFilters(makeDefaultFilters())
    setAppliedFilters(makeDefaultFilters())
    setCursorStack([null])
  }

  const applyTimePreset = (hours: number | null) => {
    const now = new Date()
    const period = hours == null
      ? { timeFrom: "", timeTo: "" }
      : {
          timeFrom: toLocalDateTimeValue(new Date(now.getTime() - hours * 60 * 60 * 1000)),
          timeTo: toLocalDateTimeValue(now),
        }
    // Быстрый период — самостоятельный однозначный фильтр, поэтому
    // применяется сразу. Остальные ещё не применённые поля не затрагиваются.
    setDraftFilters((current) => ({ ...current, ...period }))
    setAppliedFilters((current) => ({ ...current, ...period }))
    setCursorStack([null])
  }

  const goToNextPage = () => {
    if (!data?.next_cursor) return
    setCursorStack((current) => [...current, data.next_cursor])
  }

  const goToPreviousPage = () => {
    setCursorStack((current) => current.length > 1 ? current.slice(0, -1) : current)
  }

  const resultLabel = data?.total != null
    ? `${data.total.toLocaleString("ru-RU")}${data.total_is_capped ? "+" : ""} событий`
    : data
      ? `${data.events.length} на странице${data.has_more ? " · есть ещё" : ""}`
      : ""

  return (
    <div>
      <PageHeader
        title="Логи сервера"
        description="Быстрый разбор действий игроков, игровых событий и технических записей"
        icon={ScrollText}
        actions={admin?.is_superuser && (
          <Button variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            <RefreshCw className={`h-4 w-4 ${refresh.isPending ? "animate-spin" : ""}`} />
            Обновить из SFTP
          </Button>
        )}
      />

      {stats && Object.keys(stats).length > 0 && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          {Object.entries(stats).map(([serverName, stat]) => (
            <Card key={serverName} className="gap-0 py-4">
              <CardContent className="flex items-center justify-between gap-4">
                <div>
                  <div className="font-semibold">{serverName}</div>
                  <div className="text-xs text-muted-foreground">
                    Обновлено: {stat.last_refresh ? new Date(stat.last_refresh).toLocaleString("ru-RU") : "-"}
                  </div>
                </div>
                <Badge variant="secondary">{stat.event_count.toLocaleString("ru-RU")} событий</Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {searchIndex?.available && !searchIndex.ready && (
        <Card className="mb-4 border-warning/40 gap-0 py-4">
          <CardContent className="text-sm text-muted-foreground">
            Поисковый индекс истории готов на {searchIndex.progress_pct}%. Точный поиск действий игрока уже быстрый;
            текст и упоминания временно могут отвечать медленнее.
          </CardContent>
        </Card>
      )}

      <form onSubmit={applyFilters}>
        <Card className="mb-4 gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <Filter className="size-4 text-primary" />
              Поиск и фильтры
            </CardTitle>
            <CardDescription>
              Настройте условия и примените их одним запросом. Поля объединяются по принципу «И».
            </CardDescription>
            <CardAction className="flex items-center gap-2">
              {hasPendingChanges && <Badge variant="outline">Есть изменения</Badge>}
              {appliedCount > 0 && <Badge variant="secondary">Активно: {appliedCount}</Badge>}
            </CardAction>
          </CardHeader>

          <CardContent className="p-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-12">
              <div className="space-y-2 xl:col-span-4">
                <Label htmlFor="logs-search">Текст события</Label>
                <SearchInput
                  id="logs-search"
                  value={draftFilters.search}
                  onChange={(event) => updateDraft("search", event.target.value)}
                  placeholder="Например: wire, название предмета…"
                />
              </div>

              <div className="space-y-2 xl:col-span-4">
                <Label>Игроки</Label>
                <PlayerNamesFilter
                  names={draftFilters.playerNames}
                  onChange={(names) => updateDraft("playerNames", names)}
                  server={draftFilters.server}
                  scope={draftFilters.playerScope}
                  className="w-full max-w-none"
                />
              </div>

              <div className="space-y-2 xl:col-span-2">
                <Label>Режим игрока</Label>
                <Select
                  value={draftFilters.playerScope}
                  onValueChange={(value: PlayerScope) => updateDraft("playerScope", value)}
                >
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="actor">Действия игрока</SelectItem>
                    <SelectItem value="related">Включая упоминания</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2 xl:col-span-2">
                <Label>Сервер</Label>
                <Select value={draftFilters.server} onValueChange={(value) => updateDraft("server", value)}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Все серверы</SelectItem>
                    <SelectItem value="ASPER1">ASPER1</SelectItem>
                    <SelectItem value="ASPER2">ASPER2</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2 xl:col-span-3">
                <Label>Тип действия</Label>
                <Select
                  value={draftFilters.actionType}
                  onValueChange={(value) => updateDraft("actionType", value)}
                  disabled={draftFilters.consoleOnly}
                >
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Все типы действий</SelectItem>
                    {ACTION_TYPES.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2 xl:col-span-2">
                <Label>Категория</Label>
                <Select value={draftFilters.category} onValueChange={(value) => updateDraft("category", value)}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Все категории</SelectItem>
                    <SelectItem value="player">Игровые</SelectItem>
                    <SelectItem value="technical">Технические</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2 xl:col-span-2">
                <Label htmlFor="logs-time-from">С даты</Label>
                <Input
                  id="logs-time-from"
                  type="datetime-local"
                  value={draftFilters.timeFrom}
                  onChange={(event) => updateDraft("timeFrom", event.target.value)}
                />
              </div>

              <div className="space-y-2 xl:col-span-2">
                <Label htmlFor="logs-time-to">По дату</Label>
                <Input
                  id="logs-time-to"
                  type="datetime-local"
                  value={draftFilters.timeTo}
                  onChange={(event) => updateDraft("timeTo", event.target.value)}
                />
              </div>

              <div className="flex items-end xl:col-span-3">
                <label className="flex h-9 w-full items-center gap-2 rounded-md border border-input px-3 text-sm text-muted-foreground dark:bg-input/30">
                  <Checkbox
                    checked={draftFilters.consoleOnly}
                    onCheckedChange={(checked) => updateDraft("consoleOnly", checked === true)}
                  />
                  Только консольные команды
                </label>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-dashed px-3 py-2">
              <CalendarClock className="size-4 text-muted-foreground" />
              <span className="mr-1 text-xs font-medium">Быстрый период:</span>
              {TIME_PRESETS.map((preset) => (
                <Button
                  key={preset.hours}
                  type="button"
                  variant="outline"
                  size="xs"
                  disabled={isFetching}
                  onClick={() => applyTimePreset(preset.hours)}
                >
                  {preset.label}
                </Button>
              ))}
              <Button
                type="button"
                variant="ghost"
                size="xs"
                disabled={isFetching || (
                  !draftFilters.timeFrom
                  && !draftFilters.timeTo
                  && !appliedFilters.timeFrom
                  && !appliedFilters.timeTo
                )}
                onClick={() => applyTimePreset(null)}
              >
                Всё время
              </Button>
              <span className="ml-auto text-[11px] text-muted-foreground">
                Период применяется сразу
              </span>
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
              <p className="text-xs text-muted-foreground">
                {draftFilters.playerScope === "actor"
                  ? "«Действия игрока» ищет точное имя по быстрому индексу."
                  : "«Включая упоминания» также ищет имя внутри текста и может потребовать период."}
                {draftFilters.consoleOnly && ` Выбран тип: ${draftActionType}.`}
              </p>
              <div className="flex items-center gap-2">
                <Button type="button" variant="ghost" onClick={resetFilters} disabled={isFetching}>
                  <RotateCcw className="size-4" /> Сбросить
                </Button>
                <Button type="submit" disabled={isFetching || !hasPendingChanges}>
                  {isFetching ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
                  Применить
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </form>

      {isError && (
        <Card className="mb-4 border-destructive/50 gap-0 py-4">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <div>
              <p className="font-medium text-destructive">Не удалось выполнить поиск</p>
              <p className="mt-1 text-muted-foreground">
                {logSearchErrorMessage(error)}. Показанные ниже строки относятся к предыдущему запросу.
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => refetch()}>Повторить</Button>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden gap-0 py-0">
        <div className="flex min-h-14 flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="font-medium">Результаты</span>
            {resultLabel && <Badge variant="secondary">{resultLabel}</Badge>}
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {isFetching && <><LoaderCircle className="size-3.5 animate-spin" /> Выполняется запрос…</>}
            {!isFetching && appliedCount > 0 && <>Применено фильтров: {appliedCount}</>}
          </div>
        </div>

        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 10 }).map((_, index) => <Skeleton key={index} className="h-8" />)}
            </div>
          ) : (
            <>
              <Table>
                <TableHeader className="bg-muted/35">
                  <TableRow>
                    <TableHead className="w-40 pl-4">Время</TableHead>
                    <TableHead className="w-24">Сервер</TableHead>
                    <TableHead className="w-52">Игрок</TableHead>
                    <TableHead className="w-36">Действие</TableHead>
                    <TableHead className="pr-4">Детали</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody className={isFetching ? "opacity-60" : undefined}>
                  {data?.events.map((logEvent, index) => (
                    <TableRow key={logEvent.event_id ?? `${logEvent.timestamp}-${logEvent.server_name}-${index}`}>
                      <TableCell className="pl-4 text-xs whitespace-nowrap text-muted-foreground">
                        {new Date(logEvent.timestamp).toLocaleString("ru-RU")}
                      </TableCell>
                      <TableCell><Badge variant="outline">{logEvent.server_name ?? "-"}</Badge></TableCell>
                      <TableCell className="max-w-52 truncate font-medium" title={logEvent.player_name ?? undefined}>
                        {logEvent.player_name ? (
                          logEvent.player_id ? (
                            <button
                              type="button"
                              className="text-left text-primary underline-offset-2 hover:underline"
                              onClick={() => setSummaryPlayerId(logEvent.player_id!)}
                            >
                              {logEvent.player_name}
                            </button>
                          ) : logEvent.player_name
                        ) : "-"}
                      </TableCell>
                      <TableCell>
                        <span className={`font-mono text-xs ${ACTION_COLOR[logEvent.action_type] ?? "text-foreground"}`}>
                          {logEvent.action_type}
                        </span>
                      </TableCell>
                      <TableCell className="min-w-[420px] whitespace-normal break-words pr-4 text-sm text-muted-foreground">
                        {logEvent.details ?? "-"}
                      </TableCell>
                    </TableRow>
                  ))}
                  {!data?.events.length && (
                    <TableRow>
                      <TableCell colSpan={5} className="py-14 text-center text-muted-foreground">
                        Событий по выбранным условиям не найдено
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>

              {data && (data.has_more || pageNumber > 1) && (
                <div className="flex items-center justify-center gap-2 border-t p-3">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={pageNumber <= 1 || isFetching}
                    onClick={goToPreviousPage}
                  >
                    Назад
                  </Button>
                  <span className="text-sm text-muted-foreground">
                    Страница {pageNumber} · по {data.page_size} записей
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!data.next_cursor || isFetching}
                    onClick={goToNextPage}
                  >
                    Вперёд
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <PlayerSummaryDialog
        playerId={summaryPlayerId}
        open={summaryPlayerId != null}
        onOpenChange={(open) => !open && setSummaryPlayerId(null)}
      />
    </div>
  )
}
