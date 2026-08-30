/** Логи сервера, синхронизированные с воспроизведением демозаписи.
 *
 *  Смысл в том, чтобы не переключаться между вкладкой «Логи» и просмотром
 *  записи, сопоставляя время вручную: события идут тем же таймингом, что и
 *  картинка. Строка текущего момента подсвечивается и сама подматывается в
 *  видимую область, а клик по событию перематывает запись на этот момент.
 *
 *  Логи грузятся ОДНИМ запросом на весь раунд и дальше фильтруются на месте:
 *  раунд ограничен по времени, событий в нём тысячи, а не миллионы, и дёргать
 *  сервер на каждое движение ползунка было бы и медленнее, и тяжелее для БД.
 *  Фильтры, сужающие выборку на стороне сервера (игрок, тип, поиск), всё же
 *  уходят в запрос — иначе в редких длинных раундах можно упереться в лимит.
 */
import { useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Search, Crosshair, ListFilter, ScrollText } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { ACTION_COLOR, ACTION_TYPES } from "@/pages/logs/constants"
import { AdaptiveIcon } from "@/components/layout/PlatformIcon"

interface TimedLogEvent {
  timestamp: string
  /** Сдвиг от начала раунда — по нему идёт синхронизация с плеером. */
  offset_ms: number
  player_name: string | null
  action_type: string
  details: string | null
}

interface ReplayLogsResponse {
  status?: "ok" | "unavailable"
  events: TimedLogEvent[]
  total: number
  total_is_capped: boolean
  round_id: number | null
  server_name: string | null
  started_at: string | null
  ended_at: string | null
  unavailable_reason: string | null
}

function fmtOffset(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
}

export function ReplayLogsPanel({ replayId }: { replayId: string }) {
  const [category, setCategory] = useState("all")
  const [actionType, setActionType] = useState("all")
  const [player, setPlayer] = useState("")
  const [search, setSearch] = useState("")
  const [follow, setFollow] = useState(true)
  const [timeMs, setTimeMs] = useState(0)

  const debouncedPlayer = useDebouncedValue(player, 350)
  const debouncedSearch = useDebouncedValue(search, 350)

  const listRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef<HTMLButtonElement>(null)

  // Плеер сообщает своё время событием — так React-часть не лезет внутрь
  // легаси-скрипта и не зависит от его внутренних переменных.
  useEffect(() => {
    const onTime = (e: Event) => {
      const detail = (e as CustomEvent<{ timeMs: number }>).detail
      if (detail) setTimeMs(detail.timeMs)
    }
    window.addEventListener("baroreplay:time", onTime)
    return () => window.removeEventListener("baroreplay:time", onTime)
  }, [])

  const { data, isLoading } = useQuery({
    queryKey: ["replay-logs", replayId, category, actionType, debouncedPlayer, debouncedSearch],
    queryFn: () => {
      const p = new URLSearchParams()
      if (category !== "all") p.set("category", category)
      if (actionType !== "all") p.set("action_type", actionType)
      if (debouncedPlayer) p.set("player", debouncedPlayer)
      if (debouncedSearch) p.set("search", debouncedSearch)
      return api.get<ReplayLogsResponse>(`/api/v1/admin/replays/${replayId}/logs?${p}`)
    },
    staleTime: 5 * 60 * 1000,   // раунд уже закончился — логи не меняются
  })

  const events = useMemo(() => data?.events ?? [], [data])

  // Индекс последнего события, уже наступившего к текущему моменту записи.
  const activeIndex = useMemo(() => {
    let lo = 0
    let hi = events.length - 1
    let found = -1
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (events[mid].offset_ms <= timeMs) { found = mid; lo = mid + 1 } else { hi = mid - 1 }
    }
    return found
  }, [events, timeMs])

  useEffect(() => {
    if (!follow || activeIndex < 0) return
    activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" })
  }, [activeIndex, follow])

  const seekTo = (ms: number) => {
    const ui = (window as unknown as { __BaroReplayUI?: { seekTo: (ms: number) => void } }).__BaroReplayUI
    ui?.seekTo(ms)
  }

  return (
    <div className="rpv-panel">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5" style={{ margin: 0 }}><AdaptiveIcon emoji="📜" icon={ScrollText} />Логи раунда</h3>
        <Button
          variant={follow ? "default" : "outline"}
          size="sm"
          className="h-7 gap-1 text-xs"
          onClick={() => setFollow((v) => !v)}
          title="Прокручивать список за воспроизведением"
        >
          <Crosshair className="size-3" /> {follow ? "Следует" : "Не следует"}
        </Button>
      </div>

      {data?.unavailable_reason ? (
        <p className="text-xs text-muted-foreground">{data.unavailable_reason}</p>
      ) : (
        <>
          <div className="mb-2 space-y-1.5">
            <div className="flex gap-1.5">
              <Select value={category} onValueChange={setCategory}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Все категории</SelectItem>
                  <SelectItem value="player">Игроки</SelectItem>
                  <SelectItem value="technical">Технические</SelectItem>
                </SelectContent>
              </Select>
              <Select value={actionType} onValueChange={setActionType}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent className="max-h-72">
                  <SelectItem value="all">Все действия</SelectItem>
                  {ACTION_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <Input
              className="h-8 text-xs"
              placeholder="Игрок"
              value={player}
              onChange={(e) => setPlayer(e.target.value)}
            />
            <div className="relative">
              <Search className="absolute left-2 top-2 size-3.5 text-muted-foreground" />
              <Input
                className="h-8 pl-7 text-xs"
                placeholder="Поиск по тексту"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <ListFilter className="size-3" />
            {isLoading ? "Загрузка…" : (
              <>
                {events.length} событий
                {data?.total_is_capped && " (показаны не все — сузьте фильтры)"}
              </>
            )}
          </div>

          <div
            ref={listRef}
            className="flex max-h-72 flex-col gap-0.5 overflow-y-auto"
            onWheel={() => setFollow(false)}
          >
            {isLoading && Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-6" />)}
            {!isLoading && events.map((e, i) => {
              const isActive = i === activeIndex
              const isPast = i <= activeIndex
              return (
                <button
                  key={`${e.timestamp}-${i}`}
                  ref={isActive ? activeRef : undefined}
                  type="button"
                  onClick={() => seekTo(e.offset_ms)}
                  title="Перемотать запись на этот момент"
                  className={`flex w-full items-start gap-1.5 rounded px-1.5 py-1 text-left text-[11px] leading-tight transition ${
                    isActive ? "bg-primary/15 ring-1 ring-primary" : "hover:bg-accent"
                  } ${isPast ? "" : "opacity-45"}`}
                >
                  <span className="shrink-0 font-mono text-muted-foreground">{fmtOffset(e.offset_ms)}</span>
                  <span className={`shrink-0 font-mono ${ACTION_COLOR[e.action_type] ?? "text-foreground"}`}>
                    {e.action_type}
                  </span>
                  <span className="min-w-0 flex-1">
                    {e.player_name && <b className="text-info">{e.player_name}</b>}
                    {e.player_name && " "}
                    <span className="text-muted-foreground">{e.details ?? ""}</span>
                  </span>
                </button>
              )
            })}
            {!isLoading && !events.length && (
              <p className="py-4 text-center text-xs text-muted-foreground">
                Событий по заданным фильтрам нет
              </p>
            )}
          </div>

          {data?.round_id && (
            <Badge variant="outline" className="mt-2 text-[10px]">
              раунд #{data.round_id}{data.server_name ? ` · ${data.server_name}` : ""}
            </Badge>
          )}
        </>
      )}
    </div>
  )
}
