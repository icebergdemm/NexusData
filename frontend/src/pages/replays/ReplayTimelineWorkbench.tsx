import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Box,
  Crosshair,
  Filter,
  Focus,
  MessageSquare,
  Minus,
  Package,
  Pause,
  PanelRightOpen,
  Play,
  Plus,
  Search,
  ShieldAlert,
  SkipBack,
  SkipForward,
  UserRound,
  Wrench,
} from "lucide-react"
import { api } from "@/lib/api"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import {
  clamp,
  findEventAtOrBefore,
  formatTimecode,
  isInvestigationTimelineEvent,
  nearestTickInterval,
  parseTimecode,
  zoomWindowAroundPoint,
} from "./replay-timeline-utils"
import "./replay-timeline.css"

type TimelineLane = "combat" | "inventory" | "communication" | "systems" | "system"

interface TimelineItem {
  identifier: string | null
  name: string
  has_icon?: boolean
}

export interface TimelineEvent {
  id: string
  event_id?: number | string
  timestamp?: string | null
  offset_ms: number
  player_name: string | null
  action_type: string
  details: string
  source_file?: string | null
  source: "server_log" | "demo_chat" | "demo_event" | "demo_spawn"
  lane: TimelineLane
  item?: TimelineItem | null
  aggregate_count?: number
  player_count?: number
  precision_ms?: number
  player_overlay?: boolean
}

function playerCountLabel(count: number): string {
  const value = Math.max(0, Math.round(count))
  const mod100 = value % 100
  const mod10 = value % 10
  const noun = mod100 >= 11 && mod100 <= 14
    ? "игроков"
    : mod10 === 1
      ? "игрок"
      : mod10 >= 2 && mod10 <= 4
        ? "игрока"
        : "игроков"
  return `${value} ${noun}`
}

function eventActorLabel(event: TimelineEvent, systemLabel = "Система"): string {
  if (event.player_name) return event.player_name
  if ((event.player_count ?? 0) > 0) return playerCountLabel(event.player_count ?? 0)
  return systemLabel
}

interface TimelineResponse {
  status: "ok" | "unavailable"
  events: Array<Omit<TimelineEvent, "id" | "source"> & { event_id: number | string }>
  overview?: Array<Omit<TimelineEvent, "id" | "source"> & { event_id: number | string }>
  total: number
  total_is_capped: boolean
  has_more: boolean
  loaded: number
  round_id: number | null
  server_name: string | null
  duration_ms: number
  duration_source: string
  players: Array<{ name: string; event_count: number }>
  action_types: Array<{ name: string; event_count: number }>
  unavailable_reason: string | null
}

interface ReplayBridgePlayer {
  id: number
  name: string
  species: string
  teamId: number
}

interface ReplayReadyDetail {
  replayUrl?: string
  durationMs: number
  tickRate: number
  players: ReplayBridgePlayer[]
  timelineEvents: TimelineEvent[]
}

interface ReplayBridge {
  seekTo: (ms: number) => void
  previewSeek?: (ms: number) => void
  commitSeek?: () => void
  getTimeMs: () => number
  getDurationMs: () => number
  setPlaying: (playing: boolean) => void
  getTimelineData?: () => ReplayReadyDetail
  selectPlayer?: (name: string | null) => void
}

interface TimelineHit {
  x: number
  y: number
  events: TimelineEvent[]
}

interface TimelineRenderHit extends TimelineHit {
  belongsToSelected: boolean
  clusterCount: number
  isGlobal: boolean
  isSelected: boolean
  representative: TimelineEvent
}

const LANES: Array<{ id: TimelineLane; label: string }> = [
  { id: "combat", label: "БОЙ" },
  { id: "inventory", label: "ПРЕДМЕТЫ" },
  { id: "communication", label: "СВЯЗЬ" },
  { id: "systems", label: "СИСТЕМЫ" },
  { id: "system", label: "СЕРВЕР" },
]
const LANE_INDEX = new Map<TimelineLane, number>(LANES.map((lane, index) => [lane.id, index]))

const DETAIL_WINDOW_MAX_MS = 20 * 60 * 1_000
const DETAIL_WINDOW_PADDING_MS = 1_000
const UNAVAILABLE_RETRY_MS = 15_000

const ACTION_LABELS: Record<string, string> = {
  picked_up: "поднял предмет",
  placed: "положил предмет",
  removed: "извлёк предмет",
  equipped: "экипировал",
  unequipped: "снял",
  dropped: "выбросил",
  threw: "бросил",
  used: "использовал",
  opened: "открыл",
  closed: "закрыл",
  activated: "активировал",
  deactivated: "деактивировал",
  fabrication: "начал производство",
  fabrication_cancel: "отменил производство",
  attack: "атака",
  damage: "получил урон",
  shot: "выстрел",
  demo_event: "событие демозаписи",
  death: "смерть",
  structure: "повреждение корпуса",
  reactor: "настройка реактора",
  repair: "ремонт",
  repair_failed: "ошибка ремонта",
  wire: "проводка",
  pump: "насос",
  settings_change: "изменение настройки",
  chat: "чат",
  radio: "радио",
  terminal: "терминал",
  login: "вход",
  logout: "выход",
  spawn: "появление",
  despawn: "исчезновение",
  attached: "прикрепил предмет",
  ban: "блокировка игрока",
  client_error: "ошибка клиента",
  command_denied: "команда отклонена",
  connection: "соединение",
  console: "команда консоли",
  joined: "подключился к серверу",
  kick: "отключение игрока",
  looted_body: "обыскал тело",
  name_change: "сменил имя",
  name_history: "история имён",
  other: "прочее событие",
  ready: "готов к раунду",
  respawn: "возрождение",
  talent_gained: "получил талант",
}

function actionLabel(actionType: string): string {
  return ACTION_LABELS[actionType] ?? actionType.replaceAll("_", " ")
}

function getReplayBridge(): ReplayBridge | undefined {
  return (window as unknown as { __BaroReplayUI?: ReplayBridge }).__BaroReplayUI
}

function lowerBound(events: TimelineEvent[], value: number): number {
  let low = 0
  let high = events.length
  while (low < high) {
    const middle = (low + high) >> 1
    if (events[middle].offset_ms < value) low = middle + 1
    else high = middle
  }
  return low
}

function laneIcon(lane: TimelineLane) {
  if (lane === "combat") return ShieldAlert
  if (lane === "communication") return MessageSquare
  if (lane === "systems") return Wrench
  if (lane === "inventory") return Package
  return Box
}

function fallbackLane(actionType: string): TimelineLane {
  if (["attack", "death", "structure", "damage", "shot"].includes(actionType)) return "combat"
  if (["chat", "radio", "terminal"].includes(actionType)) return "communication"
  if (["reactor", "repair", "repair_failed", "wire", "pump", "settings_change"].includes(actionType)) return "systems"
  if ([
    "picked_up", "placed", "removed", "opened", "closed", "equipped", "unequipped",
    "dropped", "threw", "used", "fabrication", "fabrication_cancel", "activated", "deactivated", "attached", "looted_body",
  ].includes(actionType)) return "inventory"
  return "system"
}

function communicationText(event: Pick<TimelineEvent, "details" | "player_name">): string {
  let text = event.details.trim()
  const actor = event.player_name?.trim()
  if (actor) {
    const prefix = `${actor}:`
    if (text.toLocaleLowerCase("ru").startsWith(prefix.toLocaleLowerCase("ru"))) {
      text = text.slice(prefix.length).trim()
    }
  }
  return text.toLocaleLowerCase("ru")
}

function formatServerClock(value: string): string {
  // Text logs contain a server-local wall clock without an offset. Parsing it
  // through `Date` would silently reinterpret it in the administrator's
  // browser timezone, so keep the original clock and only localise its shape.
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,3}))?/)
  if (!match) return value.replace("T", " ")
  const [, year, month, day, time, fraction] = match
  return `${day}.${month}.${year}, ${time}${fraction ? `.${fraction.padEnd(3, "0")}` : ""}`
}

function TimecodeInput({ valueMs, durationMs, onSeek }: {
  valueMs: number
  durationMs: number
  onSeek: (ms: number) => void
}) {
  const [value, setValue] = useState(() => formatTimecode(valueMs))
  const [editing, setEditing] = useState(false)
  const [invalid, setInvalid] = useState(false)
  const cancelBlurRef = useRef(false)

  useEffect(() => {
    if (!editing) setValue(formatTimecode(valueMs))
  }, [editing, valueMs])

  const commit = () => {
    const parsed = parseTimecode(value)
    if (parsed === null) {
      setInvalid(true)
      return
    }
    const next = clamp(parsed, 0, durationMs)
    setInvalid(false)
    setEditing(false)
    setValue(formatTimecode(next))
    onSeek(next)
  }

  return (
    <div className="rpv-editor-timecode">
      <label htmlFor="rpvExactTime">ТАЙМКОД</label>
      <Input
        id="rpvExactTime"
        value={value}
        spellCheck={false}
        aria-invalid={invalid}
        aria-describedby={invalid ? "rpvExactTimeError" : undefined}
        onFocus={() => setEditing(true)}
        onChange={(event) => { setEditing(true); setInvalid(false); setValue(event.target.value) }}
        onBlur={() => {
          if (cancelBlurRef.current) { cancelBlurRef.current = false; return }
          commit()
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") { event.preventDefault(); commit() }
          if (event.key === "Escape") {
            cancelBlurRef.current = true
            setInvalid(false)
            setEditing(false)
            setValue(formatTimecode(valueMs))
            event.currentTarget.blur()
          }
        }}
      />
      {invalid && <span id="rpvExactTimeError" role="alert">Формат: ЧЧ:ММ:СС.мс</span>}
    </div>
  )
}

function TimelineCanvas({
  events,
  durationMs,
  timeMs,
  viewStartMs,
  windowMs,
  selectedPlayer,
  selectedEvent,
  onSeek,
  onPreviewSeek,
  onCommitSeek,
  onSelectEvent,
  onViewChange,
  onTogglePlaying,
}: {
  events: TimelineEvent[]
  durationMs: number
  timeMs: number
  viewStartMs: number
  windowMs: number
  selectedPlayer: string | null
  selectedEvent: TimelineEvent | null
  onSeek: (ms: number) => void
  onPreviewSeek: (ms: number) => void
  onCommitSeek: () => void
  onSelectEvent: (event: TimelineEvent) => void
  onViewChange: (next: { viewStartMs: number; windowMs: number }) => void
  onTogglePlaying: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const hitsRef = useRef<TimelineHit[]>([])
  const dragRef = useRef(false)
  const [canvasSize, setCanvasSize] = useState({ width: 900, height: 214 })
  const [hover, setHover] = useState<{ x: number; y: number; label: string } | null>(null)
  const [themeRevision, setThemeRevision] = useState(0)
  const viewEndMs = Math.min(durationMs, viewStartMs + windowMs)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return
      setCanvasSize({ width: Math.max(320, entry.contentRect.width), height: 214 })
    })
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeRevision((revision) => revision + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])

  const visibleEvents = useMemo(() => {
    const start = lowerBound(events, viewStartMs)
    const end = lowerBound(events, viewEndMs + 1)
    return events.slice(start, end)
  }, [events, viewEndMs, viewStartMs])

  const clusters = useMemo<TimelineHit[]>(() => {
    const width = canvasSize.width
    const labelWidth = width < 700 ? 66 : 92
    const plotWidth = Math.max(1, width - 12 - labelWidth)
    const rulerHeight = 34
    const laneHeight = 34
    const grouped = new Map<string, TimelineHit>()
    for (const event of visibleEvents) {
      const laneIndex = LANE_INDEX.get(event.lane)
      if (laneIndex === undefined) continue
      const x = labelWidth + ((event.offset_ms - viewStartMs) / windowMs) * plotWidth
      const y = rulerHeight + laneIndex * laneHeight + laneHeight / 2
      const key = `${laneIndex}:${Math.round(x / 4)}`
      const existing = grouped.get(key)
      if (existing) existing.events.push(event)
      else grouped.set(key, { x, y, events: [event] })
    }
    return [...grouped.values()]
  }, [canvasSize.width, viewStartMs, visibleEvents, windowMs])

  const renderHits = useMemo<TimelineRenderHit[]>(() => clusters.map((cluster) => {
    const representative = cluster.events.find((event) => event.id === selectedEvent?.id)
      ?? (selectedPlayer ? cluster.events.find((event) => event.player_name === selectedPlayer) : undefined)
      ?? cluster.events[0]
    return {
      ...cluster,
      representative,
      belongsToSelected: !selectedPlayer || cluster.events.some((event) => event.player_name === selectedPlayer),
      isGlobal: !representative.player_name,
      isSelected: cluster.events.some((event) => event.id === selectedEvent?.id),
      clusterCount: cluster.events.reduce(
        (sum, event) => sum + (event.player_overlay ? 0 : (event.aggregate_count ?? 1)),
        0,
      ),
    }
  }), [clusters, selectedEvent?.id, selectedPlayer])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || durationMs <= 0) return
    const dpr = Math.max(1, window.devicePixelRatio || 1)
    const width = canvasSize.width
    const height = canvasSize.height
    canvas.width = Math.round(width * dpr)
    canvas.height = Math.round(height * dpr)
    const context = canvas.getContext("2d")
    if (!context) return
    context.setTransform(dpr, 0, 0, dpr, 0, 0)
    context.clearRect(0, 0, width, height)

    const css = getComputedStyle(document.documentElement)
    const foreground = css.getPropertyValue("--foreground").trim() || "#f4f4f4"
    const muted = css.getPropertyValue("--muted-foreground").trim() || "#8a8a8a"
    const border = css.getPropertyValue("--border").trim() || "#343434"
    const background = css.getPropertyValue("--background").trim() || "#080808"
    const card = css.getPropertyValue("--card").trim() || "#111111"
    const destructive = css.getPropertyValue("--destructive").trim() || "#ef4444"
    const labelWidth = width < 700 ? 66 : 92
    const plotLeft = labelWidth
    const plotRight = width - 12
    const plotWidth = Math.max(1, plotRight - plotLeft)
    const rulerHeight = 34
    const laneHeight = 34
    const timeToX = (value: number) => plotLeft + ((value - viewStartMs) / windowMs) * plotWidth

    context.fillStyle = background
    context.fillRect(0, 0, width, height)
    context.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace"
    context.textBaseline = "middle"

    const tick = nearestTickInterval(windowMs, plotWidth)
    const firstTick = Math.ceil(viewStartMs / tick) * tick
    for (let value = firstTick; value <= viewEndMs; value += tick) {
      const x = Math.round(timeToX(value)) + 0.5
      context.strokeStyle = border
      context.lineWidth = 1
      context.beginPath()
      context.moveTo(x, rulerHeight - 8)
      context.lineTo(x, height)
      context.stroke()
      context.fillStyle = muted
      context.textAlign = "center"
      context.fillText(formatTimecode(value).replace(/^00:/, "").replace(/\.000$/, ""), x, 12)
    }

    LANES.forEach((lane, index) => {
      const top = rulerHeight + index * laneHeight
      context.fillStyle = index % 2 ? background : card
      context.globalAlpha = 0.45
      context.fillRect(0, top, width, laneHeight)
      context.globalAlpha = 1
      context.strokeStyle = border
      context.beginPath()
      context.moveTo(0, top + laneHeight + 0.5)
      context.lineTo(width, top + laneHeight + 0.5)
      context.stroke()
      context.fillStyle = muted
      context.textAlign = "left"
      context.fillText(width < 700 ? lane.label.slice(0, 4) : lane.label, 10, top + laneHeight / 2)
    })

    for (const cluster of renderHits) {
      const { belongsToSelected, clusterCount, isGlobal, isSelected, representative } = cluster
      context.globalAlpha = belongsToSelected ? 1 : (isGlobal ? 0.48 : 0.2)
      context.fillStyle = representative.lane === "combat" ? destructive : foreground
      context.strokeStyle = foreground
      context.lineWidth = isSelected ? 2 : 1
      const radius = isSelected ? 6 : (clusterCount > 1 ? 5 : 3.5)

      context.beginPath()
      if (representative.lane === "combat") {
        context.moveTo(cluster.x, cluster.y - radius)
        context.lineTo(cluster.x + radius, cluster.y)
        context.lineTo(cluster.x, cluster.y + radius)
        context.lineTo(cluster.x - radius, cluster.y)
        context.closePath()
      } else if (representative.lane === "communication") {
        context.moveTo(cluster.x, cluster.y - radius)
        context.lineTo(cluster.x + radius, cluster.y + radius)
        context.lineTo(cluster.x - radius, cluster.y + radius)
        context.closePath()
      } else if (representative.lane === "inventory") {
        context.rect(cluster.x - radius, cluster.y - radius, radius * 2, radius * 2)
      } else {
        context.arc(cluster.x, cluster.y, radius, 0, Math.PI * 2)
      }
      context.fill()
      if (isSelected) context.stroke()
      context.globalAlpha = 1
      if (clusterCount > 1 && windowMs < durationMs * 0.7) {
        context.fillStyle = background
        context.font = "bold 8px ui-monospace, monospace"
        context.textAlign = "center"
        context.fillText(String(Math.min(99, clusterCount)), cluster.x, cluster.y + 0.5)
        context.font = "10px ui-monospace, monospace"
      }
    }
    hitsRef.current = clusters

    if (timeMs >= viewStartMs && timeMs <= viewEndMs) {
      const x = Math.round(timeToX(timeMs)) + 0.5
      context.globalAlpha = 1
      context.strokeStyle = foreground
      context.lineWidth = 1.5
      context.beginPath()
      context.moveTo(x, 0)
      context.lineTo(x, height)
      context.stroke()
      context.fillStyle = foreground
      context.beginPath()
      context.moveTo(x - 5, 0)
      context.lineTo(x + 5, 0)
      context.lineTo(x, 7)
      context.closePath()
      context.fill()
    }
  }, [canvasSize, clusters, durationMs, renderHits, themeRevision, timeMs, viewEndMs, viewStartMs, windowMs])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || durationMs <= 0) return
    const onWheel = (event: WheelEvent) => {
      const rect = canvas.getBoundingClientRect()
      const labelWidth = rect.width < 700 ? 66 : 92
      const ratio = clamp((event.clientX - rect.left - labelWidth) / Math.max(1, rect.width - labelWidth - 12), 0, 1)
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault()
        const factor = Math.exp(-event.deltaY * 0.003)
        onViewChange(zoomWindowAroundPoint({ durationMs, viewStartMs, windowMs, anchorRatio: ratio, factor }))
      } else {
        const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY
        const nextStart = clamp(viewStartMs + delta * (windowMs / Math.max(240, rect.width)), 0, Math.max(0, durationMs - windowMs))
        if (Math.abs(nextStart - viewStartMs) >= 0.5) {
          event.preventDefault()
          onViewChange({ viewStartMs: nextStart, windowMs })
        }
      }
    }
    canvas.addEventListener("wheel", onWheel, { passive: false })
    return () => canvas.removeEventListener("wheel", onWheel)
  }, [durationMs, onViewChange, viewStartMs, windowMs])

  const pointerTime = useCallback((event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const labelWidth = rect.width < 700 ? 66 : 92
    const ratio = clamp((event.clientX - rect.left - labelWidth) / Math.max(1, rect.width - labelWidth - 12), 0, 1)
    return clamp(viewStartMs + ratio * windowMs, 0, durationMs)
  }, [durationMs, viewStartMs, windowMs])

  const hitAt = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const x = event.clientX - rect.left
    const y = event.clientY - rect.top
    return hitsRef.current.find((hit) => Math.abs(hit.x - x) <= 8 && Math.abs(hit.y - y) <= 11)
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLCanvasElement>) => {
    if (event.key === " ") { event.preventDefault(); onTogglePlaying(); return }
    if (event.key === "Home") { event.preventDefault(); onSeek(0); return }
    if (event.key === "End") { event.preventDefault(); onSeek(durationMs); return }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault()
      const direction = event.key === "ArrowLeft" ? -1 : 1
      onSeek(timeMs + direction * (event.shiftKey ? 1_000 : 100))
      return
    }
    if (event.key === "+" || event.key === "=") {
      event.preventDefault()
      onViewChange(zoomWindowAroundPoint({ durationMs, viewStartMs, windowMs, anchorRatio: 0.5, factor: 2 }))
    }
    if (event.key === "-") {
      event.preventDefault()
      onViewChange(zoomWindowAroundPoint({ durationMs, viewStartMs, windowMs, anchorRatio: 0.5, factor: 1 / 2 }))
    }
  }

  return (
    <div className="rpv-editor-canvas-wrap">
      <canvas
        ref={canvasRef}
        className="rpv-editor-canvas"
        tabIndex={0}
        aria-label="Шкала времени реплея. Ctrl и колесо изменяют масштаб; стрелки двигают время на 100 миллисекунд."
        onKeyDown={handleKeyDown}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId)
          const hit = hitAt(event)
          if (hit) {
            const time = pointerTime(event)
            const candidates = selectedPlayer
              ? hit.events.filter((item) => item.player_name === selectedPlayer)
              : hit.events
            const selected = (candidates.length ? candidates : hit.events).reduce((best, item) =>
              Math.abs(item.offset_ms - time) < Math.abs(best.offset_ms - time) ? item : best,
            )
            onSelectEvent(selected)
            onSeek(selected.offset_ms)
            return
          }
          dragRef.current = true
          onPreviewSeek(pointerTime(event))
        }}
        onPointerMove={(event) => {
          if (dragRef.current) {
            onPreviewSeek(pointerTime(event))
            return
          }
          const hit = hitAt(event)
          if (!hit) { setHover(null); return }
          const first = hit.events[0]
          const rect = event.currentTarget.getBoundingClientRect()
          setHover({
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
            label: hit.events.reduce((sum, item) => sum + (item.aggregate_count ?? 1), 0) > 1
              ? `${hit.events.reduce((sum, item) => sum + (item.aggregate_count ?? 1), 0)} событий · ${formatTimecode(first.offset_ms)}`
              : `${eventActorLabel(first)} · ${actionLabel(first.action_type)}`,
          })
        }}
        onPointerUp={() => {
          if (dragRef.current) onCommitSeek()
          dragRef.current = false
        }}
        onPointerCancel={() => {
          if (dragRef.current) onCommitSeek()
          dragRef.current = false
        }}
        onLostPointerCapture={() => {
          if (dragRef.current) onCommitSeek()
          dragRef.current = false
        }}
        onPointerLeave={() => { setHover(null) }}
      />
      {hover && (
        <div className="rpv-editor-tooltip" style={{ left: hover.x, top: hover.y }}>
          {hover.label}
        </div>
      )}
    </div>
  )
}

function ItemPreview({ item }: { item?: TimelineItem | null }) {
  if (item?.identifier) {
    return <ItemIcon identifier={item.identifier} hasIcon={item.has_icon} title={`${item.name} · ${item.identifier}`} size={34} />
  }
  return (
    <div className="rpv-editor-item-fallback" title={item?.name || "Предмет не определён"}>
      <Package aria-hidden="true" />
    </div>
  )
}

interface VisibleEventsPanelProps {
  actionFilter: string
  actionTypes: Array<{ name: string; eventCount: number }>
  detailMode: boolean
  filteredEventCount: number
  filteredRowCount: number
  isError: boolean
  isLoading: boolean
  listWindow: TimelineEvent[]
  loadedCount: number
  onActionFilterChange: (value: string) => void
  onRetry: () => void
  onSearchChange: (value: string) => void
  onSelectEvent: (event: TimelineEvent) => void
  rangeEndMs: number
  rangeStartMs: number
  search: string
  selectedEventId?: string
  selectedPlayer: string | null
  showHeading?: boolean
  totalIsCapped: boolean
  unavailableReason?: string | null
  variant?: "inline" | "sheet"
}

const VisibleEventsPanel = memo(function VisibleEventsPanel({
  actionFilter,
  actionTypes,
  detailMode,
  filteredEventCount,
  filteredRowCount,
  isError,
  isLoading,
  listWindow,
  loadedCount,
  onActionFilterChange,
  onRetry,
  onSearchChange,
  onSelectEvent,
  rangeEndMs,
  rangeStartMs,
  search,
  selectedEventId,
  selectedPlayer,
  showHeading = true,
  totalIsCapped,
  unavailableReason,
  variant = "inline",
}: VisibleEventsPanelProps) {
  return (
    <div className={`rpv-editor-events rpv-editor-events--${variant}`}>
      <div className={`rpv-editor-events-head ${showHeading ? "" : "filters-only"}`}>
        {showHeading && (
          <div>
            <strong>СОБЫТИЯ ВИДИМОГО УЧАСТКА</strong>
            <span>
              {formatTimecode(rangeStartMs)} — {formatTimecode(rangeEndMs)}
              {` · ${filteredEventCount.toLocaleString("ru-RU")}`}
            </span>
          </div>
        )}
        <div className="rpv-editor-filters">
          <div className="relative">
            <Search className="rpv-editor-search-icon" />
            <Input
              aria-label="Поиск событий видимого участка"
              value={search}
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder="Поиск в участке"
            />
          </div>
          <Select value={actionFilter} onValueChange={onActionFilterChange}>
            <SelectTrigger aria-label="Фильтр по типу действия"><Filter /><SelectValue /></SelectTrigger>
            <SelectContent className="max-h-72">
              <SelectItem value="all">Все действия</SelectItem>
              {actionTypes.map((action) => (
                <SelectItem key={action.name} value={action.name}>
                  {actionLabel(action.name)} · {action.eventCount.toLocaleString("ru-RU")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {isLoading && <div className="space-y-1 p-3">{Array.from({ length: 7 }).map((_, index) => <Skeleton key={index} className="h-9" />)}</div>}
      {isError && (
        <div className="rpv-editor-error">
          Не удалось получить серверные события. Данные самой демозаписи остаются доступны.
          <Button variant="outline" size="sm" onClick={onRetry}>Повторить</Button>
        </div>
      )}
      {!isLoading && (
        <div className="rpv-editor-event-list">
          {listWindow.map((event) => {
            const Icon = laneIcon(event.lane)
            const dimmed = Boolean(selectedPlayer && event.player_name && event.player_name !== selectedPlayer)
            const selected = selectedEventId === event.id
            return (
              <button
                type="button"
                key={event.id}
                aria-current={selected ? "true" : undefined}
                className={`${selected ? "selected" : ""} ${dimmed ? "dimmed" : ""}`}
                onClick={() => onSelectEvent(event)}
              >
                <time>{event.precision_ms ? "≈ " : ""}{formatTimecode(event.offset_ms)}</time>
                {event.item ? <ItemPreview item={event.item} /> : <Icon className="rpv-editor-event-icon" />}
                <span className="rpv-editor-event-copy">
                  <b>{eventActorLabel(event)}</b>
                  <small>{actionLabel(event.action_type)} · {event.details}</small>
                </span>
              </button>
            )
          })}
          {!listWindow.length && <div className="rpv-editor-empty">На видимом участке событий нет</div>}
        </div>
      )}
      {!detailMode && (
        <div className="rpv-editor-cap">Серверные маркеры сгруппированы. Увеличьте масштаб до участка короче 20 минут, чтобы загрузить точные строки.</div>
      )}
      {filteredRowCount > listWindow.length && (
        <div className="rpv-editor-cap">Показаны ближайшие {listWindow.length} из {filteredRowCount} строк. Увеличьте масштаб для точного разбора.</div>
      )}
      {totalIsCapped && (
        <div className="rpv-editor-warning">
          В участке больше {loadedCount.toLocaleString("ru-RU")} точных строк. Увеличьте масштаб, чтобы дозагрузить остальные.
        </div>
      )}
      {unavailableReason && (
        <div className="rpv-editor-warning">
          <span>{unavailableReason}</span>
          <Button variant="outline" size="sm" onClick={onRetry}>Повторить</Button>
        </div>
      )}
      {loadedCount > 0 && variant === "sheet" && (
        <div className="rpv-editor-cap">Загружено точных строк: {loadedCount.toLocaleString("ru-RU")}</div>
      )}
    </div>
  )
})

export function ReplayTimelineWorkbench({ replayId, initialDurationMs = 0 }: {
  replayId: string
  initialDurationMs?: number | null
}) {
  const [durationMs, setDurationMs] = useState(Math.max(0, initialDurationMs || 0))
  const [tickRate, setTickRate] = useState(0)
  const [timeMs, setTimeMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [demoPlayers, setDemoPlayers] = useState<ReplayBridgePlayer[]>([])
  const [demoEvents, setDemoEvents] = useState<TimelineEvent[]>([])
  const [selectedPlayer, setSelectedPlayer] = useState<string | null>(null)
  const [selectedEvent, setSelectedEvent] = useState<TimelineEvent | null>(null)
  const [viewStartMs, setViewStartMs] = useState(0)
  const [windowMs, setWindowMs] = useState(Math.max(1, initialDurationMs || 1))
  const [search, setSearch] = useState("")
  const [actionFilter, setActionFilter] = useState("all")
  const [eventsSheetOpen, setEventsSheetOpen] = useState(false)

  const seekTo = useCallback((value: number) => {
    const next = clamp(Math.round(value), 0, durationMs || Number.MAX_SAFE_INTEGER)
    getReplayBridge()?.seekTo(next)
    setTimeMs(next)
  }, [durationMs])

  const previewSeekTo = useCallback((value: number) => {
    const next = clamp(Math.round(value), 0, durationMs || Number.MAX_SAFE_INTEGER)
    const bridge = getReplayBridge()
    if (bridge?.previewSeek) bridge.previewSeek(next)
    else bridge?.seekTo(next)
    setTimeMs(next)
  }, [durationMs])

  const commitPreviewSeek = useCallback(() => {
    const bridge = getReplayBridge()
    if (bridge?.commitSeek) bridge.commitSeek()
    else bridge?.seekTo(timeMs)
  }, [timeMs])

  const setPlayer = useCallback((name: string | null) => {
    setSelectedPlayer(name)
    getReplayBridge()?.selectPlayer?.(name)
  }, [])

  useEffect(() => {
    setDurationMs(Math.max(0, initialDurationMs || 0))
    setWindowMs(Math.max(1, initialDurationMs || 1))
    setViewStartMs(0)
    setTimeMs(0)
    setPlaying(false)
    setTickRate(0)
    setDemoPlayers([])
    setDemoEvents([])
    setSelectedPlayer(null)
    setSelectedEvent(null)
    setEventsSheetOpen(false)
    const expectedReplayUrl = `/api/v1/admin/replays/${replayId}/download`
    const existingBridge = getReplayBridge()
    const existingData = existingBridge?.getTimelineData?.()
    const existingDuration = existingData?.durationMs ?? existingBridge?.getDurationMs() ?? 0
    if (existingDuration > 0 && existingData?.replayUrl === expectedReplayUrl) {
      setDurationMs(existingDuration)
      setWindowMs(existingDuration)
      setTickRate(existingData?.tickRate ?? 0)
      setDemoPlayers(existingData?.players ?? [])
      setDemoEvents((existingData?.timelineEvents ?? []).filter(isInvestigationTimelineEvent))
    }
    const onReady = (raw: Event) => {
      const detail = (raw as CustomEvent<ReplayReadyDetail>).detail
      if (!detail) return
      if (detail.replayUrl && detail.replayUrl !== expectedReplayUrl) return
      setDurationMs(detail.durationMs)
      setWindowMs(detail.durationMs)
      setViewStartMs(0)
      setTickRate(detail.tickRate)
      setDemoPlayers(detail.players ?? [])
      setDemoEvents((detail.timelineEvents ?? []).filter(isInvestigationTimelineEvent))
    }
    const onTime = (raw: Event) => {
      const detail = (raw as CustomEvent<{ timeMs: number; durationMs: number; playing: boolean }>).detail
      if (!detail) return
      setTimeMs(detail.timeMs)
      setPlaying(detail.playing)
      if (detail.durationMs > 0) setDurationMs((current) => current || detail.durationMs)
    }
    const onPlayerPicked = (raw: Event) => {
      const detail = (raw as CustomEvent<{ name: string | null }>).detail
      if (detail) setSelectedPlayer(detail.name || null)
    }
    window.addEventListener("baroreplay:ready", onReady)
    window.addEventListener("baroreplay:time", onTime)
    window.addEventListener("baroreplay:player-picked", onPlayerPicked)
    return () => {
      window.removeEventListener("baroreplay:ready", onReady)
      window.removeEventListener("baroreplay:time", onTime)
      window.removeEventListener("baroreplay:player-picked", onPlayerPicked)
    }
  }, [initialDurationMs, replayId])

  const visibleEndMs = Math.min(durationMs, viewStartMs + windowMs)
  const detailMode = durationMs > 0 && windowMs <= DETAIL_WINDOW_MAX_MS
  const requestedFromMs = Math.max(
    0,
    Math.floor((viewStartMs - DETAIL_WINDOW_PADDING_MS) / 1_000) * 1_000,
  )
  const requestedToMs = Math.min(
    durationMs,
    Math.ceil((visibleEndMs + DETAIL_WINDOW_PADDING_MS) / 1_000) * 1_000,
  )
  const debouncedRange = useDebouncedValue(
    detailMode ? `${requestedFromMs}:${requestedToMs}` : "overview",
    180,
  )
  const [detailFromMs, detailToMs] = debouncedRange === "overview"
    ? [0, 0]
    : debouncedRange.split(":").map(Number)

  const overviewQuery = useQuery({
    queryKey: ["replay-timeline-overview", replayId, Math.round(durationMs)],
    enabled: durationMs > 0,
    queryFn: ({ signal }) => api.get<TimelineResponse>(
      `/api/v1/admin/replays/${replayId}/logs?limit=100&overview_only=true&duration_ms=${Math.round(durationMs)}`,
      { signal },
    ),
    // "unavailable" is a recoverable storage/index state returned as HTTP
    // 200 so the demo remains usable. Do not cache that semantic failure as a
    // permanently successful overview; retry until the index is back.
    // A freshly uploaded demo can appear before its closed text-log file has
    // reached the SFTP index. A valid empty overview is therefore temporary,
    // not immutable: retry it just like an unavailable index. Non-empty round
    // data stays cached for the lifetime of the player.
    staleTime: (query) => (
      query.state.data?.status === "unavailable" || query.state.data?.total === 0
        ? 0
        : Infinity
    ),
    refetchInterval: (query) => (
      query.state.data?.status === "unavailable" || query.state.data?.total === 0
        ? UNAVAILABLE_RETRY_MS
        : false
    ),
    gcTime: 5 * 60 * 1_000,
    retry: 1,
  })

  const playerOverviewQuery = useQuery({
    queryKey: ["replay-timeline-player-overview", replayId, Math.round(durationMs), selectedPlayer],
    enabled: durationMs > 0 && Boolean(selectedPlayer) && !detailMode,
    queryFn: ({ signal }) => api.get<TimelineResponse>(
      `/api/v1/admin/replays/${replayId}/logs?limit=100&overview_only=true`
      + `&duration_ms=${Math.round(durationMs)}&player_scope=actor&player=${encodeURIComponent(selectedPlayer ?? "")}`,
      { signal },
    ),
    staleTime: (query) => query.state.data?.status === "unavailable" ? 0 : 5 * 60 * 1_000,
    refetchInterval: (query) => query.state.data?.status === "unavailable" ? UNAVAILABLE_RETRY_MS : false,
    gcTime: 2 * 60 * 1_000,
    retry: 1,
  })

  const detailQuery = useQuery({
    queryKey: ["replay-timeline-window", replayId, Math.round(durationMs), detailFromMs, detailToMs],
    enabled: durationMs > 0 && detailMode && debouncedRange !== "overview" && detailToMs > detailFromMs,
    queryFn: ({ signal }) => api.get<TimelineResponse>(
      `/api/v1/admin/replays/${replayId}/logs?limit=10000&duration_ms=${Math.round(durationMs)}`
      + `&from_ms=${detailFromMs}&to_ms=${detailToMs}`,
      { signal },
    ),
    staleTime: (query) => query.state.data?.status === "unavailable" ? 0 : 5 * 60 * 1_000,
    refetchInterval: (query) => query.state.data?.status === "unavailable" ? UNAVAILABLE_RETRY_MS : false,
    gcTime: 2 * 60 * 1_000,
    retry: 1,
  })

  const data = detailMode ? detailQuery.data : undefined
  const overviewData = overviewQuery.data
  const isLoading = detailMode ? detailQuery.isLoading : overviewQuery.isLoading
  const isError = detailMode ? detailQuery.isError : overviewQuery.isError
  const refetchOverview = overviewQuery.refetch
  const refetchPlayerOverview = playerOverviewQuery.refetch
  const refetchDetail = detailQuery.refetch
  const refetchTimeline = useCallback(() => {
    void refetchOverview()
    if (detailMode) void refetchDetail()
    if (!detailMode && selectedPlayer) void refetchPlayerOverview()
  }, [detailMode, refetchDetail, refetchOverview, refetchPlayerOverview, selectedPlayer])
  const unavailableReason = data?.unavailable_reason
    ?? overviewData?.unavailable_reason
    ?? playerOverviewQuery.data?.unavailable_reason

  const events = useMemo<TimelineEvent[]>(() => {
    const serverEvents = (data?.events ?? []).map((event, index) => ({
      ...event,
      id: `server:${event.event_id ?? `${event.timestamp ?? event.offset_ms}:${index}`}`,
      source: "server_log" as const,
      details: event.details ?? "",
      lane: event.lane ?? fallbackLane(event.action_type),
      precision_ms: event.precision_ms ?? 1_000,
    }))
    const communicationTimes = new Map<string, number[]>()
    for (const event of serverEvents) {
      if (event.lane !== "communication") continue
      const key = `${event.player_name ?? ""}\u0000${communicationText(event)}`
      const times = communicationTimes.get(key) ?? []
      times.push(event.offset_ms)
      communicationTimes.set(key, times)
    }
    const deduplicatedDemo = demoEvents.filter((event) => {
      if (event.source !== "demo_chat") return true
      const key = `${event.player_name ?? ""}\u0000${communicationText(event)}`
      return !(communicationTimes.get(key) ?? []).some((time) => Math.abs(time - event.offset_ms) <= 1_500)
    })
    return [...serverEvents, ...deduplicatedDemo]
      .filter((event) => event.offset_ms >= 0 && event.offset_ms <= durationMs)
      .sort((left, right) => left.offset_ms - right.offset_ms || left.id.localeCompare(right.id))
  }, [data?.events, demoEvents, durationMs])

  const overviewEvents = useMemo<TimelineEvent[]>(() => (
    (overviewData?.overview ?? []).map((event, index) => ({
      ...event,
      id: `overview:${event.event_id ?? index}`,
      source: "server_log" as const,
      details: event.details ?? "",
      lane: event.lane ?? fallbackLane(event.action_type),
      precision_ms: event.precision_ms ?? 1_000,
    }))
  ), [overviewData?.overview])

  const selectedPlayerOverviewEvents = useMemo<TimelineEvent[]>(() => (
    (playerOverviewQuery.data?.overview ?? []).map((event, index) => ({
      ...event,
      id: `player-overview:${selectedPlayer ?? "unknown"}:${event.event_id ?? index}`,
      source: "server_log" as const,
      player_name: selectedPlayer,
      player_overlay: true,
      details: event.details ?? "",
      lane: event.lane ?? fallbackLane(event.action_type),
      precision_ms: event.precision_ms ?? 1_000,
    }))
  ), [playerOverviewQuery.data?.overview, selectedPlayer])

  const canvasEvents = useMemo(() => (
    detailMode
      ? events
      : [...overviewEvents, ...selectedPlayerOverviewEvents, ...demoEvents]
        .filter((event) => event.offset_ms >= 0 && event.offset_ms <= durationMs)
        .sort((left, right) => left.offset_ms - right.offset_ms || left.id.localeCompare(right.id))
  ), [demoEvents, detailMode, durationMs, events, overviewEvents, selectedPlayerOverviewEvents])

  const actionTypes = useMemo(() => {
    const counts = new Map<string, number>()
    for (const action of overviewData?.action_types ?? []) counts.set(action.name, action.event_count)
    // The overview already contains every server-log count. Only demo-only
    // action kinds need to be added; otherwise zooming into a window would
    // inflate the dropdown totals with the same server rows twice.
    for (const event of demoEvents) counts.set(event.action_type, (counts.get(event.action_type) ?? 0) + 1)
    return [...counts].map(([name, eventCount]) => ({ name, eventCount }))
      .sort((left, right) => right.eventCount - left.eventCount || left.name.localeCompare(right.name))
  }, [demoEvents, overviewData?.action_types])

  const players = useMemo(() => {
    const counts = new Map<string, number>()
    for (const entry of overviewData?.players ?? data?.players ?? []) counts.set(entry.name, entry.event_count)
    for (const player of demoPlayers) if (player.name && !counts.has(player.name)) counts.set(player.name, 0)
    return [...counts].map(([name, eventCount]) => ({ name, eventCount }))
      .sort((left, right) => right.eventCount - left.eventCount || left.name.localeCompare(right.name, "ru"))
  }, [data?.players, demoPlayers, overviewData?.players])
  const listEvents = useMemo(() => (
    detailMode
      ? events
      : [...overviewEvents, ...demoEvents]
        .filter((event) => event.offset_ms >= 0 && event.offset_ms <= durationMs)
        .sort((left, right) => left.offset_ms - right.offset_ms || left.id.localeCompare(right.id))
  ), [demoEvents, detailMode, durationMs, events, overviewEvents])
  const filteredListEvents = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("ru")
    const inWindow = listEvents.slice(lowerBound(listEvents, viewStartMs), lowerBound(listEvents, visibleEndMs + 1))
    return inWindow.filter((event) => {
      if (actionFilter !== "all" && event.action_type !== actionFilter) return false
      if (!term) return true
      return `${event.player_name ?? ""} ${event.action_type} ${event.details} ${event.item?.name ?? ""}`
        .toLocaleLowerCase("ru").includes(term)
    })
  }, [actionFilter, listEvents, search, viewStartMs, visibleEndMs])
  const filteredEventCount = useMemo(
    () => filteredListEvents.reduce((sum, event) => sum + (event.aggregate_count ?? 1), 0),
    [filteredListEvents],
  )

  const activeIndex = useMemo(() => findEventAtOrBefore(filteredListEvents, timeMs), [filteredListEvents, timeMs])
  const listWindow = useMemo(() => {
    if (filteredListEvents.length <= 320) return filteredListEvents
    const center = Math.max(0, activeIndex)
    const start = clamp(center - 140, 0, filteredListEvents.length - 320)
    return filteredListEvents.slice(start, start + 320)
  }, [activeIndex, filteredListEvents])

  const changeView = useCallback((next: { viewStartMs: number; windowMs: number }) => {
    setWindowMs(next.windowMs)
    setViewStartMs(next.viewStartMs)
  }, [])

  const selectEvent = useCallback((event: TimelineEvent) => {
    setSelectedEvent(event)
    seekTo(event.offset_ms)
  }, [seekTo])

  const zoom = (factor: number) => changeView(zoomWindowAroundPoint({
    durationMs,
    viewStartMs,
    windowMs,
    anchorRatio: durationMs ? clamp((timeMs - viewStartMs) / windowMs, 0, 1) : 0.5,
    factor,
  }))

  const togglePlaying = () => {
    const next = !playing
    getReplayBridge()?.setPlaying(next)
    setPlaying(next)
  }

  return (
    <section className="rpv-editor" aria-label="Монтажная шкала реплея">
      <header className="rpv-editor-toolbar">
        <div className="rpv-editor-title">
          <span>РАЗБОР РАУНДА</span>
          <small>
            {overviewData
              ? `${overviewData.total.toLocaleString("ru-RU")} серверных · ${demoEvents.length.toLocaleString("ru-RU")} из демо`
              : `${demoEvents.length.toLocaleString("ru-RU")} событий из демо`}
            {detailMode ? ` · ${data?.loaded ?? 0} в видимом окне` : " · обзор"}
            {tickRate ? ` · ${tickRate} тиков/с` : ""}
          </small>
        </div>
        <div className="rpv-editor-transport">
          <Button variant="outline" size="icon-sm" onClick={() => seekTo(timeMs - 1_000)} title="Назад на секунду"><SkipBack /></Button>
          <Button variant="outline" size="icon-sm" onClick={togglePlaying} title={playing ? "Пауза" : "Воспроизвести"}>
            {playing ? <Pause /> : <Play />}
          </Button>
          <Button variant="outline" size="icon-sm" onClick={() => seekTo(timeMs + 1_000)} title="Вперёд на секунду"><SkipForward /></Button>
          <TimecodeInput valueMs={timeMs} durationMs={durationMs} onSeek={seekTo} />
        </div>
        <div className="rpv-editor-zoom">
          <Button variant="outline" size="icon-sm" onClick={() => zoom(1 / 2)} title="Уменьшить масштаб"><Minus /></Button>
          <span>{durationMs ? `${Math.max(1, Math.round(durationMs / windowMs)).toLocaleString("ru-RU")}×` : "1×"}</span>
          <Button variant="outline" size="icon-sm" onClick={() => zoom(2)} title="Увеличить масштаб"><Plus /></Button>
          <Button variant="outline" size="sm" onClick={() => changeView({ viewStartMs: 0, windowMs: durationMs })}>
            <Focus /> Весь раунд
          </Button>
          <Sheet open={eventsSheetOpen} onOpenChange={setEventsSheetOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" size="sm" title="Открыть события текущего участка">
                <PanelRightOpen /> События
                <span className="rpv-editor-events-count">{filteredEventCount.toLocaleString("ru-RU")}</span>
              </Button>
            </SheetTrigger>
            <SheetContent className="rpv-editor-events-sheet w-full gap-0 p-0 sm:max-w-2xl">
              <SheetHeader className="rpv-editor-sheet-head">
                <SheetTitle>События видимого участка</SheetTitle>
                <SheetDescription>
                  {formatTimecode(viewStartMs)} — {formatTimecode(visibleEndMs)} · {filteredEventCount.toLocaleString("ru-RU")} событий
                </SheetDescription>
              </SheetHeader>
              <VisibleEventsPanel
                actionFilter={actionFilter}
                actionTypes={actionTypes}
                detailMode={detailMode}
                filteredEventCount={filteredEventCount}
                filteredRowCount={filteredListEvents.length}
                isError={isError}
                isLoading={isLoading}
                listWindow={listWindow}
                loadedCount={data?.loaded ?? 0}
                onActionFilterChange={setActionFilter}
                onRetry={refetchTimeline}
                onSearchChange={setSearch}
                onSelectEvent={selectEvent}
                rangeEndMs={visibleEndMs}
                rangeStartMs={viewStartMs}
                search={search}
                selectedEventId={selectedEvent?.id}
                selectedPlayer={selectedPlayer}
                showHeading={false}
                totalIsCapped={Boolean(data?.total_is_capped)}
                unavailableReason={unavailableReason}
                variant="sheet"
              />
            </SheetContent>
          </Sheet>
        </div>
      </header>

      <div className="rpv-editor-hint">
        <span><kbd>Ctrl</kbd> + колесо: масштаб до 0,5 с</span>
        <span>колесо: прокрутка</span>
        <span><kbd>←</kbd><kbd>→</kbd>: 100 мс</span>
        <span><kbd>Shift</kbd> + стрелка: 1 с</span>
      </div>

      {durationMs > 0 ? (
        <TimelineCanvas
          events={canvasEvents}
          durationMs={durationMs}
          timeMs={timeMs}
          viewStartMs={viewStartMs}
          windowMs={windowMs}
          selectedPlayer={selectedPlayer}
          selectedEvent={selectedEvent}
          onSeek={seekTo}
          onPreviewSeek={previewSeekTo}
          onCommitSeek={commitPreviewSeek}
          onSelectEvent={setSelectedEvent}
          onViewChange={changeView}
          onTogglePlaying={togglePlaying}
        />
      ) : (
        <div className="rpv-editor-empty"><Crosshair /> Ожидаю индекс демозаписи…</div>
      )}

      <div className="rpv-editor-roster" aria-label="Фильтр по игроку">
        <button type="button" className={!selectedPlayer ? "active" : ""} onClick={() => setPlayer(null)}>
          ВСЕ ИГРОКИ <span>{players.length}</span>
        </button>
        {players.map((player) => (
          <button
            type="button"
            key={player.name}
            className={selectedPlayer === player.name ? "active" : ""}
            onClick={() => setPlayer(selectedPlayer === player.name ? null : player.name)}
          >
            <UserRound /> {player.name} <span>{player.eventCount}</span>
          </button>
        ))}
      </div>

      <div className="rpv-editor-lower">
        <VisibleEventsPanel
          actionFilter={actionFilter}
          actionTypes={actionTypes}
          detailMode={detailMode}
          filteredEventCount={filteredEventCount}
          filteredRowCount={filteredListEvents.length}
          isError={isError}
          isLoading={isLoading}
          listWindow={listWindow}
          loadedCount={data?.loaded ?? 0}
          onActionFilterChange={setActionFilter}
          onRetry={refetchTimeline}
          onSearchChange={setSearch}
          onSelectEvent={selectEvent}
          rangeEndMs={visibleEndMs}
          rangeStartMs={viewStartMs}
          search={search}
          selectedEventId={selectedEvent?.id}
          selectedPlayer={selectedPlayer}
          totalIsCapped={Boolean(data?.total_is_capped)}
        />

        <aside className="rpv-editor-inspector">
          <div className="rpv-editor-inspector-head">
            <strong>ИНСПЕКТОР</strong>
            {selectedEvent && <Badge variant="outline">{selectedEvent.source === "server_log" ? "ЛОГ" : "ДЕМО"}</Badge>}
          </div>
          {selectedEvent ? (
            <div className="rpv-editor-inspector-body">
              <div className="rpv-editor-inspector-time">{selectedEvent.precision_ms ? "≈ " : ""}{formatTimecode(selectedEvent.offset_ms)}</div>
              <div className="rpv-editor-inspector-kind">
                {selectedEvent.item && <ItemPreview item={selectedEvent.item} />}
                <div><span>{actionLabel(selectedEvent.action_type)}</span><b>{eventActorLabel(selectedEvent, "Системное событие")}</b></div>
              </div>
              <dl>
                <div><dt>Дорожка</dt><dd>{LANES.find((lane) => lane.id === selectedEvent.lane)?.label}</dd></div>
                {selectedEvent.item?.identifier && <div><dt>Identifier</dt><dd>{selectedEvent.item.identifier}</dd></div>}
                {selectedEvent.timestamp && <div><dt>Время сервера</dt><dd>{formatServerClock(selectedEvent.timestamp)}</dd></div>}
                {selectedEvent.source_file && <div><dt>Источник</dt><dd title={selectedEvent.source_file}>{selectedEvent.source_file}</dd></div>}
              </dl>
              <p>{selectedEvent.details || "Подробностей нет."}</p>
              <Button variant="outline" size="sm" onClick={() => seekTo(selectedEvent.offset_ms)}><Crosshair /> Перейти к событию</Button>
            </div>
          ) : (
            <div className="rpv-editor-inspector-empty">
              <Crosshair />
              <p>Нажмите на маркер или строку события, чтобы увидеть точное время и детали.</p>
            </div>
          )}
          {unavailableReason && (
            <div className="rpv-editor-warning">
              <span>{unavailableReason}</span>
              <Button variant="outline" size="sm" onClick={refetchTimeline}>Повторить</Button>
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}
