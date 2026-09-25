import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useInfiniteScroll } from "@/hooks/useInfiniteScroll"
import { toast } from "sonner"
import {
  CalendarClock,
  Filter,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Search,
  X,
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
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import { api, ApiError } from "@/lib/api"
import { ACTION_COLOR, ACTION_TYPES, type LogEvent } from "./constants"
import { PlayerNamesFilter } from "./player-filter"
import { useCurrentAdmin } from "@/lib/auth"
import { IS_V2 } from "@/lib/ui-version"
import { cn } from "@/lib/utils"
import { formatBrowserDate } from "@/lib/format-date"
import { CommunicationsView } from "./CommunicationsView"
import { LogsWorkspace, LogsWorkspaceHeader } from "./logs-workspace"

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

const PAGE_SIZE_OPTIONS = [5, 10, 25, 50, 75, 100] as const

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

function LogViewToggle({ communications }: { communications: boolean }) {
  return (
    <div className="flex items-center gap-1">
      <Button asChild variant={communications ? "outline" : "secondary"} size="sm">
        <Link to="/logs">Журнал событий</Link>
      </Button>
      <Button asChild variant={communications ? "secondary" : "outline"} size="sm">
        <Link to="/logs?view=communications">Общение игроков</Link>
      </Button>
    </div>
  )
}

export function LogsPage() {
  const { data: admin } = useCurrentAdmin()
  const [searchParams] = useSearchParams()
  const eventId = searchParams.get("event_id")
  const communicationMode = searchParams.get("view") === "communications"
  const [draftFilters, setDraftFilters] = useState<LogFilters>(makeDefaultFilters)
  const [appliedFilters, setAppliedFilters] = useState<LogFilters>(makeDefaultFilters)
  const [pageSize, setPageSize] = useState(100)
  const [summaryPlayerId, setSummaryPlayerId] = useState<number | null>(null)
  const [v2FiltersOpen, setV2FiltersOpen] = useState(false)
  const queryClient = useQueryClient()

  const appliedActionType = appliedFilters.consoleOnly ? "console" : appliedFilters.actionType
  const draftActionType = draftFilters.consoleOnly ? "console" : draftFilters.actionType
  const hasPendingChanges = filterSignature(draftFilters) !== filterSignature(appliedFilters)
  const appliedCount = activeFilterCount(appliedFilters)

  const { data: infiniteData, isLoading, isFetching, isFetchingNextPage, hasNextPage, fetchNextPage, isError, error, refetch } = useInfiniteQuery({
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
      eventId,
      pageSize,
    ],
    queryFn: ({ pageParam = null as string | null }) => {
      const params = new URLSearchParams({ pagination: "cursor", page_size: String(pageSize) })
      if (pageParam) params.set("cursor", pageParam as string)
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
      if (eventId) params.set("event_id", eventId)
      return api.get<LogsResponse>(`/api/v1/admin/logs/events?${params}`)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !communicationMode,
    retry: false,
  })
  const data = infiniteData?.pages[infiniteData.pages.length - 1]
  const allEvents = infiniteData?.pages.flatMap((p) => p.events) ?? []
  const sentinelRef = useInfiniteScroll(() => { if (hasNextPage) fetchNextPage() }, !!hasNextPage, isFetchingNextPage || isFetching)
  const { data: stats } = useQuery({
    queryKey: ["logs-stats"],
    queryFn: () => api.get<Record<string, LogStatEntry>>("/api/v1/admin/logs/stats"),
    enabled: !communicationMode,
  })

  const { data: searchIndex } = useQuery({
    queryKey: ["logs-search-index"],
    queryFn: () => api.get<SearchIndexStatus>("/api/v1/admin/logs/search-index"),
    enabled: !communicationMode,
    refetchInterval: (query) => (!communicationMode && query.state.data?.ready === false ? 15000 : false),
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
    setV2FiltersOpen(false)
  }

  const resetFilters = () => {
    setDraftFilters(makeDefaultFilters())
    setAppliedFilters(makeDefaultFilters())
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
  }

  const applyFiltersImmediately = (patch: Partial<LogFilters>) => {
    setDraftFilters((current) => ({ ...current, ...patch }))
    setAppliedFilters((current) => ({ ...current, ...patch }))
  }

  const removeAppliedPlayer = (name: string) => {
    const playerNames = appliedFilters.playerNames.filter((current) => current !== name)
    applyFiltersImmediately({ playerNames })
  }

  const changePageSize = (value: string) => {
    setPageSize(Number(value))
  }

  const resultLabel = data?.total != null
    ? `${data.total.toLocaleString("ru-RU")}${data.total_is_capped ? "+" : ""} событий · загружено ${allEvents.length}`
    : allEvents.length
      ? `${allEvents.length} загружено${hasNextPage ? " · есть ещё" : ""}`
      : ""

  if (communicationMode) {
    if (IS_V2) return <CommunicationsView />
    return (
      <div>
        <PageHeader
          title="Логи сервера"
          description="Журнал событий и общение игроков"
          icon={ScrollText}
          actions={<LogViewToggle communications />}
        />
        <CommunicationsView />
      </div>
    )
  }

  if (IS_V2) {
    const categoryLabel = appliedFilters.category === "player"
      ? "Игровые"
      : appliedFilters.category === "technical"
        ? "Технические"
        : ""
    const actionLabel = appliedFilters.consoleOnly
      ? "Только консоль"
      : appliedFilters.actionType !== "all"
        ? appliedFilters.actionType
        : ""
    const activeChips: Array<{ key: string; label: string; onRemove: () => void }> = []

    if (appliedFilters.server !== "all") {
      activeChips.push({
        key: "server",
        label: `Сервер: ${appliedFilters.server}`,
        onRemove: () => applyFiltersImmediately({ server: "all" }),
      })
    }
    if (actionLabel) {
      activeChips.push({
        key: "action",
        label: `Действие: ${actionLabel}`,
        onRemove: () => applyFiltersImmediately({ actionType: "all", consoleOnly: false }),
      })
    }
    if (categoryLabel) {
      activeChips.push({
        key: "category",
        label: `Категория: ${categoryLabel}`,
        onRemove: () => applyFiltersImmediately({ category: "all" }),
      })
    }
    if (appliedFilters.search) {
      activeChips.push({
        key: "search",
        label: `Текст: ${appliedFilters.search}`,
        onRemove: () => applyFiltersImmediately({ search: "" }),
      })
    }
    appliedFilters.playerNames.forEach((name) => {
      activeChips.push({
        key: `player:${name}`,
        label: `Игрок: ${name}`,
        onRemove: () => removeAppliedPlayer(name),
      })
    })
    if (appliedFilters.timeFrom || appliedFilters.timeTo) {
      activeChips.push({
        key: "period",
        label: `Период: ${appliedFilters.timeFrom ? formatBrowserDate(appliedFilters.timeFrom) : "…"} — ${appliedFilters.timeTo ? formatBrowserDate(appliedFilters.timeTo) : "…"}`,
        onRemove: () => applyFiltersImmediately({ timeFrom: "", timeTo: "" }),
      })
    }

    return (
      <LogsWorkspace>
        <LogsWorkspaceHeader communications={false} filtersOpen={v2FiltersOpen}
          onToggleFilters={() => setV2FiltersOpen((current) => !current)} filterCount={appliedCount}
          actions={admin?.is_superuser && (
              <Button variant="outline" size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
                <RefreshCw className={cn("size-4", refresh.isPending && "animate-spin")} />
                <span className="hidden sm:inline">Обновить из SFTP</span>
              </Button>
          )} />

        <div className={cn("grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)] lg:grid-cols-[21rem_minmax(0,1fr)] lg:grid-rows-1", v2FiltersOpen && "max-lg:grid-rows-[minmax(0,1fr)_minmax(0,1fr)]")}>
          <aside
            id="logs-filters-v2"
            className={cn(
              "min-h-0 overflow-auto border-b bg-background lg:block lg:border-r lg:border-b-0",
              v2FiltersOpen ? "block" : "hidden",
            )}
          >
            <form onSubmit={applyFilters} className="flex h-full min-h-0 flex-col">
              <div className="flex shrink-0 items-center justify-between border-b px-4 py-3">
                <div className="flex items-center gap-2">
                  <Filter className="size-4" />
                  <span className="text-xs font-semibold uppercase tracking-[0.12em]">Фильтры</span>
                </div>
                <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                  {hasPendingChanges && <span>Есть изменения</span>}
                  {appliedCount > 0 && <Badge variant="outline">{appliedCount}</Badge>}
                </div>
              </div>

              <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
                {searchIndex?.available && !searchIndex.ready && (
                  <div className="border border-warning/40 px-3 py-2 text-xs text-muted-foreground">
                    Поисковый индекс готов на {searchIndex.progress_pct}%. Текстовый поиск может отвечать медленнее.
                  </div>
                )}

                <div className="space-y-1.5">
                  <Label htmlFor="logs-search-v2">Текст события</Label>
                  <SearchInput
                    id="logs-search-v2"
                    value={draftFilters.search}
                    onChange={(event) => updateDraft("search", event.target.value)}
                    placeholder="wire, название предмета…"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label>Игроки</Label>
                  <PlayerNamesFilter
                    names={draftFilters.playerNames}
                    onChange={(names) => updateDraft("playerNames", names)}
                    server={draftFilters.server}
                    scope={draftFilters.playerScope}
                    className="w-full max-w-none"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label>Режим игрока</Label>
                    <Select
                      value={draftFilters.playerScope}
                      onValueChange={(value: PlayerScope) => updateDraft("playerScope", value)}
                    >
                      <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="actor">Действия игрока</SelectItem>
                        <SelectItem value="related">С упоминаниями</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
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
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label>Тип действия</Label>
                    <Select
                      value={draftFilters.actionType}
                      onValueChange={(value) => updateDraft("actionType", value)}
                      disabled={draftFilters.consoleOnly}
                    >
                      <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">Все действия</SelectItem>
                        {[...ACTION_TYPES].sort((a, b) => a.localeCompare(b)).map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
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
                </div>

                {/* One per row: a datetime value ("дд.мм.рррр, --:--") plus its
                    picker icon does not fit half of this panel, and the icon
                    ended up covering the date and hiding the time. */}
                <div className="grid grid-cols-1 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="logs-time-from-v2">С даты</Label>
                    <Input
                      id="logs-time-from-v2"
                      type="datetime-local"
                      value={draftFilters.timeFrom}
                      onChange={(event) => updateDraft("timeFrom", event.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="logs-time-to-v2">По дату</Label>
                    <Input
                      id="logs-time-to-v2"
                      type="datetime-local"
                      value={draftFilters.timeTo}
                      onChange={(event) => updateDraft("timeTo", event.target.value)}
                    />
                  </div>
                </div>

                <label className="flex min-h-9 items-center gap-2 border border-input px-3 text-xs text-muted-foreground">
                  <Checkbox
                    checked={draftFilters.consoleOnly}
                    onCheckedChange={(checked) => updateDraft("consoleOnly", checked === true)}
                  />
                  Только консольные команды
                </label>

                <div className="space-y-2 border-t pt-4">
                  <div className="flex items-center gap-2 text-xs font-medium">
                    <CalendarClock className="size-4" /> Быстрый период
                  </div>
                  <div className="grid grid-cols-4 gap-1.5">
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
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    className="w-full"
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
                </div>

                <p className="border-t pt-3 text-[11px] leading-4 text-muted-foreground">
                  {draftFilters.playerScope === "actor"
                    ? "Точное имя ищется по быстрому индексу."
                    : "Упоминания также ищутся внутри текста и могут потребовать ограничить период."}
                  {draftFilters.consoleOnly && ` Выбран тип: ${draftActionType}.`}
                </p>
                {stats && Object.entries(stats).map(([serverName, stat]) => (
                  <div key={serverName} className="flex flex-col gap-1 border-t pt-3 text-[11px] text-muted-foreground">
                    <span>{serverName} · {stat.event_count.toLocaleString("ru-RU")} событий</span>
                    <span>Обновлено: {stat.last_refresh ? formatBrowserDate(stat.last_refresh) : "Не обновлялось"}</span>
                  </div>
                ))}
              </div>

              <div className="grid shrink-0 grid-cols-2 gap-2 border-t bg-background p-3">
                <Button type="button" variant="outline" onClick={resetFilters} disabled={isFetching}>
                  <RotateCcw className="size-4" /> Сбросить
                </Button>
                <Button type="submit" disabled={isFetching || !hasPendingChanges}>
                  {isFetching ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
                  Применить
                </Button>
              </div>
            </form>
          </aside>

          <section className="flex min-h-0 min-w-0 flex-col">
            <div className="shrink-0 border-b px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="mr-1 text-xs font-semibold uppercase tracking-[0.12em]">Результаты</span>
                {resultLabel && <Badge variant="secondary">{resultLabel}</Badge>}
                {isFetching && (
                  <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <LoaderCircle className="size-3.5 animate-spin" /> Выполняется запрос
                  </span>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="ml-auto"
                  onClick={() => refetch()}
                  disabled={isFetching}
                  aria-label="Обновить результаты"
                >
                  <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
                </Button>
              </div>
              <div className="mt-2 flex min-h-7 flex-wrap items-center gap-1.5">
                <span className="mr-1 text-[11px] text-muted-foreground">Активные фильтры</span>
                {activeChips.map((chip) => (
                  <button
                    key={chip.key}
                    type="button"
                    className="flex max-w-72 items-center gap-1.5 border border-border bg-muted/35 px-2 py-1 text-[11px] hover:bg-accent"
                    onClick={chip.onRemove}
                    title={`Убрать фильтр «${chip.label}»`}
                  >
                    <span className="truncate">{chip.label}</span>
                    <X className="size-3 shrink-0" />
                  </button>
                ))}
                {!activeChips.length && <span className="text-[11px] text-muted-foreground">Нет</span>}
                {activeChips.length > 1 && (
                  <button
                    type="button"
                    className="ml-1 text-[11px] text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                    onClick={resetFilters}
                  >
                    Сбросить все
                  </button>
                )}
              </div>
            </div>

            {isError && (
              <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-destructive/50 px-4 py-3 text-sm">
                <div>
                  <p className="font-medium text-destructive">Не удалось выполнить поиск</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {logSearchErrorMessage(error)}. Ниже оставлены строки предыдущего запроса.
                  </p>
                </div>
                <Button variant="outline" size="sm" onClick={() => refetch()}>Повторить</Button>
              </div>
            )}

            <div data-logs-results-scroll className="min-h-0 flex-1 overflow-auto overscroll-contain [&>[data-slot=table-container]]:overflow-visible">
              {isLoading ? (
                <div className="space-y-2 p-4">
                  {Array.from({ length: 12 }).map((_, index) => <Skeleton key={index} className="h-9" />)}
                </div>
              ) : (
                <>
                  <Table className="min-w-[980px]">
                    <TableHeader className="sticky top-0 z-10 bg-background">
                      <TableRow>
                        <TableHead className="w-44 border-r pl-4">Время</TableHead>
                        <TableHead className="w-24 border-r">Сервер</TableHead>
                        <TableHead className="w-60 border-r">Игрок</TableHead>
                        <TableHead className="w-40 border-r">Действие</TableHead>
                        <TableHead className="pr-4">Детали</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody className={isFetching ? "opacity-60" : undefined}>
                      {allEvents.map((logEvent, index) => (
                        <TableRow key={logEvent.event_id ?? `${logEvent.timestamp}-${logEvent.server_name}-${index}`}>
                          <TableCell className="border-r pl-4 font-mono text-[11px] whitespace-nowrap text-muted-foreground">
                            {formatBrowserDate(logEvent.timestamp)}
                          </TableCell>
                          <TableCell className="border-r"><Badge variant="outline">{logEvent.server_name ?? "-"}</Badge></TableCell>
                          <TableCell className="max-w-60 truncate border-r text-xs font-medium" title={logEvent.player_name ?? undefined}>
                            {logEvent.player_name ? (
                              logEvent.player_id ? (
                                <PlayerHoverLink playerId={logEvent.player_id}>{logEvent.player_name}</PlayerHoverLink>
                              ) : logEvent.player_name
                            ) : "-"}
                          </TableCell>
                          <TableCell className="border-r">
                            <span className={cn("font-mono text-[11px]", ACTION_COLOR[logEvent.action_type] ?? "text-foreground")}>
                              {logEvent.action_type}
                            </span>
                          </TableCell>
                          <TableCell className="min-w-[420px] whitespace-normal break-words pr-4 text-xs leading-5 text-muted-foreground">
                            {logEvent.details ?? "-"}
                          </TableCell>
                        </TableRow>
                      ))}
                      {!allEvents.length && !isLoading && (
                        <TableRow>
                          <TableCell colSpan={5} className="py-16 text-center text-muted-foreground">
                            Событий по выбранным условиям не найдено
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                  <div ref={sentinelRef as never} className="h-1" />
                  {isFetchingNextPage && (
                    <div className="flex justify-center gap-2 p-3 text-sm text-muted-foreground">
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" /> Загрузка…
                    </div>
                  )}
                  {!hasNextPage && allEvents.length > 0 && (
                    <div className="p-3 text-center text-xs text-muted-foreground">Загружено {allEvents.length} событий · конец списка</div>
                  )}
                </>
              )}
            </div>

            <div className="flex min-h-12 shrink-0 flex-wrap items-center justify-between gap-3 border-t px-4 py-2">
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Строк на странице</span>
                <Select value={String(pageSize)} onValueChange={changePageSize} disabled={isFetching}>
                  <SelectTrigger className="h-8 w-20" aria-label="Количество строк на странице">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent align="start">
                    {PAGE_SIZE_OPTIONS.map((size) => (
                      <SelectItem key={size} value={String(size)}>{size}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center gap-2">
                <span className="min-w-24 text-center font-mono text-xs">Загружено {allEvents.length}{hasNextPage ? " · есть ещё" : " · конец"}</span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!hasNextPage || isFetching}
                  onClick={() => hasNextPage && fetchNextPage()}
                  aria-label="Загрузить ещё"
                >
                  Загрузить ещё
                </Button>
              </div>
              <span className="text-xs text-muted-foreground">
                {hasNextPage ? "Есть следующая страница" : "Конец выборки"}
              </span>
            </div>
          </section>
        </div>

        <PlayerSummaryDialog
          playerId={summaryPlayerId}
          open={summaryPlayerId != null}
          onOpenChange={(open) => !open && setSummaryPlayerId(null)}
        />
      </LogsWorkspace>
    )
  }

  return (
    <div>
      <PageHeader
        title="Логи сервера"
        description="Быстрый разбор действий игроков, игровых событий и технических записей"
        icon={ScrollText}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <LogViewToggle communications={false} />
            {admin?.is_superuser && (
              <Button variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
                <RefreshCw className={`h-4 w-4 ${refresh.isPending ? "animate-spin" : ""}`} />
                Обновить из SFTP
              </Button>
            )}
          </div>
        }
      />

      {stats && Object.keys(stats).length > 0 && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          {Object.entries(stats).map(([serverName, stat]) => (
            <Card key={serverName} className="gap-0 py-4">
              <CardContent className="flex items-center justify-between gap-4">
                <div>
                  <div className="font-semibold">{serverName}</div>
                  <div className="text-xs text-muted-foreground">
                    Обновлено: {stat.last_refresh ? formatBrowserDate(stat.last_refresh) : "-"}
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
                    {[...ACTION_TYPES].sort((a, b) => a.localeCompare(b)).map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}
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
                  {allEvents.map((logEvent, index) => (
                    <TableRow key={logEvent.event_id ?? `${logEvent.timestamp}-${logEvent.server_name}-${index}`}>
                      <TableCell className="pl-4 text-xs whitespace-nowrap text-muted-foreground">
                        {formatBrowserDate(logEvent.timestamp)}
                      </TableCell>
                      <TableCell><Badge variant="outline">{logEvent.server_name ?? "-"}</Badge></TableCell>
                      <TableCell className="max-w-52 truncate font-medium" title={logEvent.player_name ?? undefined}>
                        {logEvent.player_name ? (
                          logEvent.player_id ? (
                            <PlayerHoverLink playerId={logEvent.player_id}>{logEvent.player_name}</PlayerHoverLink>
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
                  {!allEvents.length && !isLoading && (
                    <TableRow>
                      <TableCell colSpan={5} className="py-14 text-center text-muted-foreground">
                        Событий по выбранным условиям не найдено
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              <div ref={sentinelRef as never} className="h-1" />
              {isFetchingNextPage && (
                <div className="flex justify-center gap-2 p-3 text-sm text-muted-foreground">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" /> Загрузка…
                </div>
              )}
              {!hasNextPage && allEvents.length > 0 && (
                <div className="border-t p-3 text-center text-xs text-muted-foreground">Загружено {allEvents.length} событий · конец списка</div>
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
