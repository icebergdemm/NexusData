import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { Crosshair, Search } from "lucide-react"
import { api } from "@/lib/api"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ACTION_TYPES } from "@/pages/logs/constants"
import { activeContextEventIndex, contextEventIsPast, contextEventText, sortContextEvents, type ReplayContextEvent } from "./replay-context-utils"

interface ReplayLogsResponse {
  status?: "ok" | "unavailable"
  events: Array<Omit<ReplayContextEvent, "id"> & { id?: string; event_id?: number }>
  total: number
  total_is_capped: boolean
  unavailable_reason: string | null
}

type ReplayKind = "round" | "lobby" | "lobby_dump"

function formatOffset(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1_000))
  return `${String(Math.floor(totalSeconds / 60)).padStart(2, "0")}:${String(totalSeconds % 60).padStart(2, "0")}`
}

function eventLabel(event: ReplayContextEvent): string {
  if (event.action_type === "chat") return "чат"
  return event.action_type
}

function ContextEventList({
  events,
  timeMs,
  onSeek,
  emptyText,
}: {
  events: readonly ReplayContextEvent[]
  timeMs: number
  onSeek: (ms: number) => void
  emptyText: string
}) {
  const [follow, setFollow] = useState(true)
  const listRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef<HTMLButtonElement>(null)
  const activeIndex = activeContextEventIndex(events, timeMs)

  useEffect(() => {
    if (follow && activeIndex >= 0) activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" })
  }, [activeIndex, follow])

  return <>
    <div ref={listRef} className="rvw-context-list" onWheel={() => setFollow(false)}>
      {events.map((event, index) => {
        const isActive = index === activeIndex
        const isPast = contextEventIsPast(event, timeMs)
        const text = contextEventText(event)
        return <button
          key={event.id}
          ref={isActive ? activeRef : undefined}
          type="button"
          className="rvw-context-row"
          data-active={isActive}
          data-past={isPast}
          onClick={() => onSeek(event.offset_ms)}
          title="Перемотать запись на этот момент"
        >
          <span className="rvw-context-time">{formatOffset(event.offset_ms)}</span>
          <span className="rvw-context-body">
            <span className="rvw-context-meta"><b>{event.player_name || "Система"}</b><span>{eventLabel(event)}</span></span>
            <span className="rvw-context-text">{text || "Текст недоступен"}</span>
            {event.transcript_text && <span className="rvw-context-asr">распознано</span>}
          </span>
        </button>
      })}
      {!events.length && <p className="rvw-context-empty">{emptyText}</p>}
    </div>
    <Button type="button" variant={follow ? "secondary" : "outline"} size="sm" className="rvw-context-follow" onClick={() => setFollow((value) => !value)}>
      <Crosshair className="size-3" /> {follow ? "Следует" : "Следовать"}
    </Button>
  </>
}

function ContextCard({
  title,
  panel,
  children,
}: {
  title: string
  panel: "chat" | "logs"
  children: ReactNode
}) {
  return <section className="rvw-context-card" data-context-panel={panel}>
    <div className="rvw-context-card-head"><h3>{title}</h3></div>
    {children}
  </section>
}

export function ReplayContextPanels({
  replayId,
  replayKind,
  events,
  timeMs,
  onSeek,
}: {
  replayId: string
  replayKind: ReplayKind
  events: readonly ReplayContextEvent[]
  timeMs: number
  onSeek: (ms: number) => void
}) {
  const [chatPlayer, setChatPlayer] = useState("all")
  const [logCategory, setLogCategory] = useState("all")
  const [logActionType, setLogActionType] = useState("all")
  const [logSearch, setLogSearch] = useState("")
  const debouncedSearch = useDebouncedValue(logSearch, 250)
  const allChatEvents = useMemo(() => sortContextEvents(events.filter((event) => event.action_type === "chat")), [events])
  const chatEvents = useMemo(() => {
    const sorted = allChatEvents
    return chatPlayer === "all" ? sorted : sorted.filter((event) => (event.player_name || "") === chatPlayer)
  }, [allChatEvents, chatPlayer])
  const chatPlayers = useMemo(() => [...new Set(allChatEvents.map((event) => event.player_name).filter((name): name is string => Boolean(name)))].sort((a, b) => a.localeCompare(b, "ru")), [allChatEvents])
  const { data: logs, isLoading: logsLoading, isError: logsError } = useQuery({
    queryKey: ["replay-context-logs", replayId, logCategory, logActionType, debouncedSearch],
    enabled: replayKind === "round" && Boolean(replayId),
    queryFn: () => {
      const params = new URLSearchParams()
      if (logCategory !== "all") params.set("category", logCategory)
      if (logActionType !== "all") params.set("action_type", logActionType)
      if (debouncedSearch.trim()) params.set("search", debouncedSearch.trim())
      return api.get<ReplayLogsResponse>(`/api/v1/admin/replays/${replayId}/logs?${params}`)
    },
    staleTime: 5 * 60 * 1_000,
  })
  const logEvents = useMemo(() => sortContextEvents((logs?.events ?? []).map((event, index) => ({
    ...event,
    id: event.id || `log:${event.event_id ?? index}:${event.offset_ms}:${event.action_type}`,
  }))), [logs])

  return <div className="rvw-context-grid">
    <ContextCard title="Чат" panel="chat">
      <div className="rvw-context-filter-row">
        <select className="rvw-select" aria-label="Фильтр чата" value={chatPlayer} onChange={(event) => setChatPlayer(event.target.value)}>
          <option value="all">Все участники</option>
          {chatPlayers.map((player) => <option key={player} value={player}>{player}</option>)}
        </select>
      </div>
      <ContextEventList events={chatEvents} timeMs={timeMs} onSeek={onSeek} emptyText="Сообщений чата в записи нет." />
    </ContextCard>
    <ContextCard title="Логи раунда" panel="logs">
      {replayKind !== "round" ? <p className="rvw-context-empty">Для лобби логи раунда не привязаны.</p> : <>
        <div className="rvw-context-filter-row">
          <select className="rvw-select" aria-label="Категория логов" value={logCategory} onChange={(event) => setLogCategory(event.target.value)}>
            <option value="all">Все категории</option><option value="player">Игроки</option><option value="technical">Технические</option>
          </select>
          <select className="rvw-select" aria-label="Тип события логов" value={logActionType} onChange={(event) => setLogActionType(event.target.value)}>
            <option value="all">Все действия</option>{ACTION_TYPES.map((actionType) => <option key={actionType} value={actionType}>{actionType}</option>)}
          </select>
          <div className="rvw-context-search"><Search className="size-3.5" /><Input aria-label="Поиск по логам" placeholder="Поиск" value={logSearch} onChange={(event) => setLogSearch(event.target.value)} /></div>
        </div>
        {logsLoading && <p className="rvw-context-empty">Загрузка логов…</p>}
        {logsError && <p className="rvw-context-empty">Логи временно недоступны.</p>}
        {!logsLoading && !logsError && <ContextEventList events={logEvents} timeMs={timeMs} onSeek={onSeek} emptyText={logs?.unavailable_reason || "Событий по заданным фильтрам нет."} />}
      </>}
    </ContextCard>
  </div>
}
