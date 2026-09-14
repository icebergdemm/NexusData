import React, { useCallback, useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { ChevronDown, Pause, Play, Settings2, Volume2, VolumeX } from "lucide-react"
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import {
  buildVoiceLanes,
  filterVoiceLanes,
  type VoiceCategoryFilter,
  type VoiceChannelFilter,
  type VoiceLane,
  type VoiceSegmentInput,
  type VoiceSpeaker,
} from "./replay-voice-utils"

export interface ReplayVoiceTimelineEvent {
  offset_ms: number
  action_type?: string | null
  details?: string | null
}

export interface ReplayVoiceWorkspaceData {
  durationMs: number
  voiceSpeakers: VoiceSpeaker[]
  voiceSegments: VoiceSegmentInput[]
  timelineEvents?: ReplayVoiceTimelineEvent[]
}

interface ReplayVoiceBridge {
  getTimelineData?: () => Record<string, unknown>
  getTimeMs?: () => number
  isPlaying?: () => boolean
  seekTo?: (ms: number) => void
  setPlaying?: (playing: boolean) => void
  play?: () => Promise<void> | void
  pause?: () => void
  setVoiceEnabled?: (enabled: boolean) => void
  setMasterVolume?: (value: number) => void
  setSpeakerMuted?: (speakerId: number, muted: boolean) => void
  setSpeed?: (speed: number) => void
}

declare global {
  interface Window {
    __BaroReplayUI?: ReplayVoiceBridge
  }
}

type ReplayKind = "round" | "lobby" | "lobby_dump"
type VoiceFilter = VoiceCategoryFilter | "voice-chat" | "events"

const categoryOptions: { value: VoiceFilter; label: string }[] = [
  { value: "all", label: "Всё" },
  { value: "voice", label: "Только голос" },
  { value: "voice-chat", label: "Голос + чат" },
  { value: "events", label: "События" },
]

const styles = [
  ".rvw-shell{color:var(--foreground);font-size:13px}",
  ".rvw-head,.rvw-controls,.rvw-filterbar,.rvw-foot{display:flex;align-items:center;gap:8px;flex-wrap:wrap}",
  ".rvw-head{justify-content:space-between;padding:14px 16px 11px;border:1px solid var(--border);background:var(--card)}",
  ".rvw-title{font-weight:700;font-size:16px}.rvw-muted{color:var(--muted-foreground)}.rvw-tiny{font-size:11px}",
  ".rvw-tag,.rvw-chip,.rvw-select,.rvw-btn{border:1px solid var(--border);background:var(--background);color:var(--foreground);border-radius:5px;padding:6px 9px;font-size:11px}",
  ".rvw-chip{border-radius:999px;cursor:pointer}.rvw-chip[data-active=true]{border-color:var(--primary);background:color-mix(in srgb,var(--primary) 18%,transparent)}",
  ".rvw-controls{padding:10px 16px;border-inline:1px solid var(--border);background:var(--card)}",
  ".rvw-btn{display:inline-flex;align-items:center;gap:5px;cursor:pointer}.rvw-btn:hover,.rvw-select:hover{border-color:var(--primary)}.rvw-btn-primary{background:var(--primary);border-color:var(--primary);color:var(--primary-foreground)}",
  ".rvw-transport{flex:1;min-width:220px}.rvw-transport-line{position:relative;height:6px;border-radius:5px;background:var(--muted)}.rvw-transport-fill{height:100%;border-radius:5px;background:var(--primary)}",
  ".rvw-filterbar{padding:9px 16px;border:1px solid var(--border);border-top:0;background:color-mix(in srgb,var(--card) 85%,var(--background))}.rvw-spacer{flex:1}",
  ".rvw-content{display:grid;grid-template-columns:minmax(0,1fr) 245px;border:1px solid var(--border);border-top:0}.rvw-timeline{min-width:0;padding:14px 16px}",
  ".rvw-ruler{display:grid;grid-template-columns:repeat(7,1fr);margin-left:128px;color:var(--muted-foreground);font-size:9px}",
  ".rvw-lane{display:grid;grid-template-columns:116px minmax(0,1fr);gap:12px;align-items:center;min-height:42px;border-top:1px solid color-mix(in srgb,var(--border) 50%,transparent)}",
  ".rvw-name-wrap{min-width:0}.rvw-speaker-link{display:flex;flex-direction:column;min-width:0;color:inherit;text-decoration:none}.rvw-speaker-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rvw-speaker-link:hover .rvw-speaker-name{text-decoration:underline}.rvw-speaker-status{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted-foreground);font-size:9px}.rvw-unresolved{cursor:default}",
  ".rvw-track{position:relative;height:22px;border-radius:4px;background:repeating-linear-gradient(90deg,var(--background) 0,var(--background) calc(16.66% - 1px),color-mix(in srgb,var(--border) 55%,transparent) calc(16.66% - 1px),color-mix(in srgb,var(--border) 55%,transparent) 16.66%)}",
  ".rvw-segment{position:absolute;top:3px;height:16px;border:0;border-radius:4px;background:var(--primary);box-shadow:0 0 0 1px color-mix(in srgb,var(--primary) 55%,transparent);cursor:pointer}.rvw-segment-radio{background:var(--info,#22d3ee);box-shadow:0 0 0 1px color-mix(in srgb,var(--info,#22d3ee) 55%,transparent)}.rvw-segment:hover{filter:brightness(1.3)}.rvw-playhead{position:absolute;top:-5px;bottom:-5px;width:2px;background:var(--foreground);box-shadow:0 0 7px var(--foreground);pointer-events:none;z-index:2}",
  ".rvw-event-row{display:grid;grid-template-columns:116px minmax(0,1fr);gap:12px;align-items:center;min-height:24px}.rvw-event-track{height:8px}.rvw-event-marker{position:absolute;top:0;width:3px;height:100%;border-radius:2px;background:var(--warning,#f59e0b)}",
  ".rvw-side{padding:14px;border-left:1px solid var(--border);background:color-mix(in srgb,var(--card) 82%,var(--background))}.rvw-side-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-weight:700}",
  ".rvw-person{display:flex;align-items:center;gap:7px;width:100%;margin-bottom:5px;padding:7px;border:1px solid transparent;border-radius:5px;background:var(--background);color:var(--foreground);text-align:left;cursor:pointer}.rvw-person[data-selected=true]{border-color:var(--primary);background:color-mix(in srgb,var(--primary) 16%,var(--background))}.rvw-person:hover{border-color:var(--primary)}",
  ".rvw-dot{width:8px;height:8px;flex:none;border-radius:50%;background:var(--primary)}.rvw-dot-radio{background:var(--info,#22d3ee)}.rvw-person-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rvw-person-time{color:var(--muted-foreground);font-size:10px}.rvw-setting-button{border:0;background:none;color:inherit;cursor:pointer}.rvw-settings-wrap{position:relative}.rvw-settings-popover{position:absolute;right:0;top:calc(100% + 6px);z-index:4;display:flex;flex-direction:column;gap:8px;min-width:220px;padding:10px;border:1px solid var(--border);background:var(--card);box-shadow:0 8px 24px #0005;font-size:11px;color:var(--muted-foreground)}.rvw-settings-popover label{display:flex;align-items:center;gap:6px;color:var(--foreground)}",
  ".rvw-setting{display:flex;justify-content:space-between;align-items:center;gap:7px;padding:10px 0;border-top:1px solid var(--border);color:var(--muted-foreground);font-size:11px}.rvw-foot{justify-content:space-between;padding:9px 16px;border:1px solid var(--border);border-top:0}.rvw-legend{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted-foreground);font-size:10px}.rvw-legend i{display:inline-block;width:8px;height:8px;margin-right:4px;border-radius:2px;background:var(--primary)}.rvw-legend i.radio{background:var(--info,#22d3ee)}.rvw-empty{padding:28px 8px;text-align:center;color:var(--muted-foreground)}",
  "@media(max-width:760px){.rvw-content{grid-template-columns:1fr}.rvw-side{border-top:1px solid var(--border);border-left:0}.rvw-ruler{margin-left:96px}.rvw-lane,.rvw-event-row{grid-template-columns:84px minmax(0,1fr);gap:8px}}",
].join("")

function bridge(): ReplayVoiceBridge | undefined {
  return typeof window === "undefined" ? undefined : window.__BaroReplayUI
}

function mergeBridgeData(initialData: ReplayVoiceWorkspaceData, detail: Record<string, unknown> | null): ReplayVoiceWorkspaceData {
  if (!detail) return initialData
  const nextSpeakers = Array.isArray(detail.voiceSpeakers) ? detail.voiceSpeakers as VoiceSpeaker[] : []
  const nextSegments = Array.isArray(detail.voiceSegments) ? detail.voiceSegments as VoiceSegmentInput[] : []
  const byId = new Map(initialData.voiceSpeakers.map((speaker) => [speaker.speaker_id, speaker]))
  for (const speaker of nextSpeakers) {
    const previous = byId.get(speaker.speaker_id)
    byId.set(speaker.speaker_id, {
      ...previous,
      ...speaker,
      names: speaker.names?.length ? speaker.names : previous?.names,
      player_id: speaker.player_id ?? previous?.player_id ?? null,
      steam_id: speaker.steam_id ?? previous?.steam_id ?? null,
      resolution_status: speaker.resolution_status ?? previous?.resolution_status ?? null,
    })
  }
  return {
    durationMs: Number(detail.durationMs) || initialData.durationMs,
    voiceSpeakers: [...byId.values()],
    voiceSegments: nextSegments.length ? nextSegments : initialData.voiceSegments,
    timelineEvents: Array.isArray(detail.timelineEvents) ? detail.timelineEvents as ReplayVoiceTimelineEvent[] : initialData.timelineEvents,
  }
}

function formatTime(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1_000))
  const hours = Math.floor(seconds / 3_600)
  const minutes = Math.floor((seconds % 3_600) / 60)
  const rest = seconds % 60
  if (hours > 0) return String(hours) + ":" + String(minutes).padStart(2, "0") + ":" + String(rest).padStart(2, "0")
  return String(minutes).padStart(2, "0") + ":" + String(rest).padStart(2, "0")
}

function percentage(value: number, startMs: number, spanMs: number): number {
  return Math.max(0, Math.min(100, ((value - startMs) / Math.max(1, spanMs)) * 100))
}

function SpeakerName({ lane }: { lane: VoiceLane }) {
  const content = <><span className="rvw-speaker-name">{lane.name}</span><span className="rvw-speaker-status">{lane.playerId ? "Профиль доступен" : "Профиль не найден"}</span></>
  return lane.playerId
    ? <PlayerHoverLink playerId={lane.playerId} className="rvw-speaker-link">{content}</PlayerHoverLink>
    : <span className="rvw-speaker-link rvw-unresolved">{content}</span>
}

function Segment({ segment, viewStartMs, viewSpanMs, onClick }: { segment: { startMs: number; endMs: number; radio: boolean }; viewStartMs: number; viewSpanMs: number; onClick: () => void }) {
  const left = percentage(segment.startMs, viewStartMs, viewSpanMs)
  const right = percentage(segment.endMs, viewStartMs, viewSpanMs)
  if (right <= left) return null
  return <button type="button" className={"rvw-segment" + (segment.radio ? " rvw-segment-radio" : "")} style={{ left: left + "%", width: Math.max(0.35, right - left) + "%" }} title={formatTime(segment.startMs) + "–" + formatTime(segment.endMs) + " · " + (segment.radio ? "радио" : "локальная речь")} aria-label={"Перейти к голосовому сегменту " + formatTime(segment.startMs)} onClick={onClick} />
}

export function ReplayVoiceWorkspace({ replayKind, initialData }: { replayKind: ReplayKind; initialData: ReplayVoiceWorkspaceData }) {
  const [bridgeData, setBridgeData] = useState<Record<string, unknown> | null>(null)
  const [timeMs, setTimeMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [category, setCategory] = useState<VoiceFilter>(replayKind === "round" ? "voice" : "all")
  const [channel, setChannel] = useState<VoiceChannelFilter>("all")
  const [activeOnly, setActiveOnly] = useState(false)
  const [selectedSpeakers, setSelectedSpeakers] = useState<Set<number>>(new Set())
  const [viewStartMs, setViewStartMs] = useState(0)
  const [viewEndMs, setViewEndMs] = useState(Math.max(1, initialData.durationMs))
  const [volume, setVolume] = useState(100)
  const [speed, setSpeed] = useState(1)
  const [mutedSpeakers, setMutedSpeakers] = useState<Set<number>>(new Set())
  const [settingsOpen, setSettingsOpen] = useState(false)

  useEffect(() => {
    const onReady = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail
      setBridgeData(detail)
      const nextDuration = Number(detail.durationMs) || initialData.durationMs
      setViewEndMs((current) => current === initialData.durationMs ? nextDuration : current)
    }
    const onTime = (event: Event) => {
      const detail = (event as CustomEvent<{ timeMs?: number; playing?: boolean }>).detail
      if (typeof detail.timeMs === "number") setTimeMs(detail.timeMs)
      if (typeof detail.playing === "boolean") setPlaying(detail.playing)
    }
    window.addEventListener("baroreplay:ready", onReady)
    window.addEventListener("baroreplay:time", onTime)
    const currentBridge = bridge()
    const currentData = currentBridge?.getTimelineData?.()
    if (currentData) onReady(new CustomEvent("baroreplay:ready", { detail: currentData }))
    if (typeof currentBridge?.getTimeMs === "function") setTimeMs(currentBridge.getTimeMs())
    if (typeof currentBridge?.isPlaying === "function") setPlaying(currentBridge.isPlaying())
    return () => {
      window.removeEventListener("baroreplay:ready", onReady)
      window.removeEventListener("baroreplay:time", onTime)
    }
  }, [initialData.durationMs])

  const data = useMemo(() => mergeBridgeData(initialData, bridgeData), [initialData, bridgeData])
  const durationMs = Math.max(1, data.durationMs)
  const lanes = useMemo(() => buildVoiceLanes(data.voiceSpeakers, data.voiceSegments, durationMs), [data, durationMs])
  const filteredLanes = useMemo(() => filterVoiceLanes(lanes, {
    category: category === "voice-chat" || category === "events" ? "voice" : category,
    channel,
    activeOnly,
    speakerIds: selectedSpeakers,
  }), [lanes, category, channel, activeOnly, selectedSpeakers])
  const visibleEvents = category === "voice" ? [] : data.timelineEvents ?? []
  const showVoice = category !== "events"
  const viewSpanMs = Math.max(1, viewEndMs - viewStartMs)
  const playhead = percentage(timeMs, viewStartMs, viewSpanMs)
  const seek = useCallback((value: number) => {
    const next = Math.max(0, Math.min(durationMs, Math.round(value)))
    bridge()?.seekTo?.(next)
    setTimeMs(next)
  }, [durationMs])
  const togglePlaying = useCallback(() => {
    const next = !playing
    if (next) {
      bridge()?.setVoiceEnabled?.(true)
      void bridge()?.play?.()
      if (!bridge()?.play) bridge()?.setPlaying?.(true)
    } else {
      bridge()?.pause?.()
      if (!bridge()?.pause) bridge()?.setPlaying?.(false)
    }
    setPlaying(next)
  }, [playing])
  const toggleSpeaker = (speakerId: number) => setSelectedSpeakers((current) => {
    const next = new Set(current)
    if (next.has(speakerId)) next.delete(speakerId)
    else next.add(speakerId)
    return next
  })
  const toggleMute = (speakerId: number) => setMutedSpeakers((current) => {
    const next = new Set(current)
    const muted = !next.has(speakerId)
    if (muted) next.add(speakerId)
    else next.delete(speakerId)
    bridge()?.setSpeakerMuted?.(speakerId, muted)
    return next
  })
  const selectZoom = (value: string) => {
    const span = value === "all" ? durationMs : Number(value)
    if (span >= durationMs) {
      setViewStartMs(0)
      setViewEndMs(durationMs)
      return
    }
    const center = Math.max(span / 2, Math.min(durationMs - span / 2, timeMs))
    setViewStartMs(Math.max(0, center - span / 2))
    setViewEndMs(Math.min(durationMs, center + span / 2))
  }

  useEffect(() => {
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      const target = event.target
      if (target instanceof HTMLElement && ["INPUT", "SELECT", "BUTTON"].includes(target.tagName)) return
      if (event.code === "Space") {
        event.preventDefault()
        togglePlaying()
      } else if (event.key === "ArrowLeft") {
        event.preventDefault()
        seek(timeMs - 5_000)
      } else if (event.key === "ArrowRight") {
        event.preventDefault()
        seek(timeMs + 5_000)
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [durationMs, playing, seek, timeMs, togglePlaying])

  return <div className="rvw-shell">
    {React.createElement("style", null, styles)}
    <div className="rvw-head">
      <div><div className="rvw-title">{replayKind === "round" ? "Голосовая лента раунда" : "Голосовая лента лобби"}</div><div className="rvw-muted rvw-tiny">{lanes.length} участников · {playing ? "воспроизведение" : "пауза"}</div></div>
      <span className="rvw-tag">Без транскрипции · только запись</span>
    </div>
    <div className="rvw-controls">
      <button type="button" className="rvw-btn rvw-btn-primary" onClick={togglePlaying}>{playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />} {playing ? "Пауза" : "Воспроизвести"}</button>
      <span className="rvw-tiny rvw-muted">{formatTime(timeMs)} / {formatTime(durationMs)}</span>
      <div className="rvw-transport"><div className="rvw-transport-line"><div className="rvw-transport-fill" style={{ width: percentage(timeMs, 0, durationMs) + "%" }} /></div></div>
      {[0.5, 1, 2, 4].map((value) => <button key={value} type="button" className="rvw-btn" data-active={speed === value} onClick={() => { setSpeed(value); bridge()?.setSpeed?.(value) }}>{value}×</button>)}
      <button type="button" className="rvw-btn" onClick={() => seek(timeMs - 5_000)}>−5с</button><button type="button" className="rvw-btn" onClick={() => seek(timeMs + 5_000)}>+5с</button>
    </div>
    <div className="rvw-filterbar">
      <span className="rvw-muted rvw-tiny">Показывать:</span>
      {categoryOptions.map((option) => <button key={option.value} type="button" className="rvw-chip" data-active={category === option.value} onClick={() => setCategory(option.value)}>{option.label}</button>)}
      <span className="rvw-spacer" />
      <button type="button" className="rvw-chip" data-active={activeOnly} onClick={() => setActiveOnly((value) => !value)}>Только активные</button>
      <button type="button" className="rvw-select" onClick={() => setChannel((value) => value === "all" ? "local" : value === "local" ? "radio" : "all")}>Канал: {channel === "all" ? "Все" : channel === "local" ? "Локальный" : "Радио"} <ChevronDown className="inline size-3" /></button>
      <span className="rvw-settings-wrap"><button type="button" className="rvw-select" aria-expanded={settingsOpen} onClick={() => setSettingsOpen((value) => !value)}><Settings2 className="inline size-3" /> Настройки</button>{settingsOpen && <span className="rvw-settings-popover"><label><input type="checkbox" checked={activeOnly} onChange={(event) => setActiveOnly(event.target.checked)} /> Скрывать молчащих</label><span>Space — воспроизведение · ←/→ — перемотка на 5 секунд</span></span>}</span>
    </div>
    <div className="rvw-content">
      <section className="rvw-timeline">
        <div className="rvw-head" style={{ padding: "0 0 9px", border: 0, background: "transparent" }}><span className="rvw-muted rvw-tiny">Лента активности</span><span className="rvw-muted rvw-tiny">Клик по сегменту — перейти к началу</span></div>
        <div className="rvw-ruler">{[0, .1666, .3333, .5, .6666, .8333, 1].map((ratio) => <span key={ratio}>{formatTime(viewStartMs + viewSpanMs * ratio)}</span>)}</div>
        {showVoice && filteredLanes.map((lane) => <div className="rvw-lane" key={lane.speakerId}>
          <div className="rvw-name-wrap"><SpeakerName lane={lane} /></div>
          <div className="rvw-track">{lane.segments.map((segment) => <Segment key={segment.startMs + "-" + segment.endMs + "-" + segment.radio} segment={segment} viewStartMs={viewStartMs} viewSpanMs={viewSpanMs} onClick={() => { seek(segment.startMs); setSelectedSpeakers(new Set([lane.speakerId])) }} />)}<span className="rvw-playhead" style={{ left: playhead + "%" }} /></div>
        </div>)}
        {(category === "all" || category === "voice-chat") && <div className="rvw-event-row"><span className="rvw-muted rvw-tiny">чат</span><div className="rvw-track rvw-event-track">{visibleEvents.filter((event) => event.action_type === "chat").map((event, index) => <span className="rvw-event-marker" key={"chat-" + index} style={{ left: percentage(event.offset_ms, viewStartMs, viewSpanMs) + "%" }} title={event.details || "Сообщение в чате"} />)}</div></div>}
        {(category === "all" || category === "events") && <div className="rvw-event-row"><span className="rvw-muted rvw-tiny">события</span><div className="rvw-track rvw-event-track">{visibleEvents.filter((event) => event.action_type !== "chat").map((event, index) => <span className="rvw-event-marker" key={"event-" + index} style={{ left: percentage(event.offset_ms, viewStartMs, viewSpanMs) + "%" }} title={event.details || event.action_type || "Событие"} />)}</div></div>}
        {showVoice && filteredLanes.length === 0 && visibleEvents.length === 0 ? <div className="rvw-empty">Для выбранных фильтров активности нет.</div> : null}
      </section>
      <aside className="rvw-side">
        <div className="rvw-side-title"><span>Игроки</span><span className="rvw-muted rvw-tiny">{selectedSpeakers.size || lanes.length} выбрано</span></div>
        {lanes.map((lane) => <div key={lane.speakerId} className="rvw-person" data-selected={selectedSpeakers.size === 0 || selectedSpeakers.has(lane.speakerId)} onClick={() => toggleSpeaker(lane.speakerId)} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleSpeaker(lane.speakerId) } }}>
          <span className={"rvw-dot" + (lane.segments.some((segment) => segment.radio) ? " rvw-dot-radio" : "")} /><span className="rvw-person-name"><SpeakerName lane={lane} /></span><span className="rvw-person-time">{formatTime(lane.totalMs)}</span>
          <button type="button" className="rvw-setting-button" title={mutedSpeakers.has(lane.speakerId) ? "Вернуть звук" : "Заглушить игрока"} onClick={(event) => { event.stopPropagation(); toggleMute(lane.speakerId) }}>{mutedSpeakers.has(lane.speakerId) ? <VolumeX className="size-3.5" /> : <Volume2 className="size-3.5" />}</button>
        </div>)}
        <div className="rvw-setting"><span>Громкость всех</span><input type="range" min="0" max="100" value={volume} onChange={(event) => { const value = Number(event.target.value); setVolume(value); bridge()?.setMasterVolume?.(value / 100) }} /></div>
        <div className="rvw-setting"><span>Масштаб</span><select className="rvw-select" value={viewSpanMs >= durationMs ? "all" : String(viewSpanMs)} onChange={(event) => selectZoom(event.target.value)}><option value="all">Вся запись</option><option value="30000">30 секунд</option><option value="120000">2 минуты</option><option value="600000">10 минут</option></select></div>
        <div className="rvw-setting"><span>Канал голоса</span><span className="rvw-muted">{channel === "all" ? "Все" : channel === "local" ? "Локальный" : "Радио"}</span></div>
      </aside>
    </div>
    <div className="rvw-foot"><div className="rvw-legend"><span><i />локальная речь</span><span><i className="radio" />радио</span></div><span className="rvw-muted rvw-tiny">Space — play/pause · ←/→ — 5 секунд</span><Link className="rvw-muted rvw-tiny" to="/replays">К списку реплеев</Link></div>
  </div>
}
