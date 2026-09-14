import React, { useCallback, useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { Pause, Play, Volume2, VolumeX } from "lucide-react"
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import {
  buildVoiceLanes,
  filterVoiceLanes,
  type VoiceChannelFilter,
  type VoiceLane,
  type VoiceSegmentInput,
  type VoiceSpeaker,
} from "./replay-voice-utils"

export interface ReplayVoiceWorkspaceData {
  durationMs: number
  voiceSpeakers: VoiceSpeaker[]
  voiceSegments: VoiceSegmentInput[]
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
type TimelineRect = { left: number; width: number }
type TimelineWheelInput = {
  startMs: number
  endMs: number
  durationMs: number
  anchorRatio: number
  deltaY: number
  pan: boolean
}

const styles = [
  ".rvw-shell{color:var(--foreground);font-size:13px}",
  ".rvw-head,.rvw-toolbar,.rvw-foot{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.rvw-head{justify-content:space-between;padding:14px 16px 11px;border:1px solid var(--border);background:var(--card)}",
  ".rvw-title{font-size:16px;font-weight:700}.rvw-muted{color:var(--muted-foreground)}.rvw-tiny{font-size:11px}.rvw-tag{border:1px solid var(--border);border-radius:5px;padding:5px 8px;font-size:10px;color:var(--muted-foreground)}",
  ".rvw-toolbar{padding:10px 16px;border:1px solid var(--border);border-top:0;background:color-mix(in srgb,var(--card) 88%,var(--background))}.rvw-spacer{flex:1}",
  ".rvw-btn,.rvw-select{border:1px solid var(--border);border-radius:5px;background:var(--background);color:var(--foreground);padding:6px 9px;font-size:11px}.rvw-btn{display:inline-flex;align-items:center;gap:5px;cursor:pointer}.rvw-btn:hover,.rvw-select:hover{border-color:var(--primary)}.rvw-btn-primary{background:var(--primary);border-color:var(--primary);color:var(--primary-foreground)}",
  ".rvw-transport{position:relative;flex:1;min-width:200px;height:24px;touch-action:none;cursor:pointer}.rvw-transport-line{position:absolute;inset:8px 0;height:8px;border-radius:999px;background:var(--muted);overflow:hidden}.rvw-transport-fill{height:100%;border-radius:inherit;background:var(--primary)}.rvw-transport-thumb{position:absolute;top:4px;width:16px;height:16px;border:2px solid var(--background);border-radius:50%;background:var(--primary);box-shadow:0 1px 5px #0008;transform:translateX(-50%);pointer-events:none}",
  ".rvw-timeline{min-width:0;padding:14px 16px;border:1px solid var(--border);border-top:0;background:var(--background);user-select:none}.rvw-timeline-meta{display:flex;justify-content:space-between;gap:10px;margin-bottom:9px}.rvw-ruler{display:grid;grid-template-columns:repeat(7,1fr);margin-left:150px;color:var(--muted-foreground);font-size:9px}.rvw-ruler span:last-child{text-align:right}",
  ".rvw-lane{display:grid;grid-template-columns:138px minmax(0,1fr) 32px;gap:12px;align-items:center;min-height:46px;border-top:1px solid color-mix(in srgb,var(--border) 55%,transparent)}.rvw-lane[data-speaking=true]{background:color-mix(in srgb,var(--primary) 8%,transparent)}.rvw-lane[data-speaking=true] .rvw-speaker-name{color:var(--primary)}",
  ".rvw-name-wrap{min-width:0;padding-left:2px}.rvw-speaker-link{display:flex;flex-direction:column;min-width:0;color:inherit;text-decoration:none}.rvw-speaker-link:hover .rvw-speaker-name{text-decoration:underline}.rvw-speaker-name,.rvw-speaker-status{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rvw-speaker-status{font-size:9px;color:var(--muted-foreground)}.rvw-unresolved{cursor:default}",
  ".rvw-track{position:relative;height:24px;border-radius:5px;background:repeating-linear-gradient(90deg,var(--background) 0,var(--background) calc(16.66% - 1px),color-mix(in srgb,var(--border) 55%,transparent) calc(16.66% - 1px),color-mix(in srgb,var(--border) 55%,transparent) 16.66%);touch-action:none;cursor:crosshair}.rvw-segment{position:absolute;top:3px;height:18px;border:0;border-radius:4px;background:var(--primary);box-shadow:0 0 0 1px color-mix(in srgb,var(--primary) 60%,transparent);cursor:pointer}.rvw-segment-radio{background:var(--info,#22d3ee);box-shadow:0 0 0 1px color-mix(in srgb,var(--info,#22d3ee) 60%,transparent)}.rvw-segment:hover{filter:brightness(1.25)}.rvw-playhead{position:absolute;top:-5px;bottom:-5px;width:2px;background:var(--foreground);box-shadow:0 0 7px var(--foreground);pointer-events:none;z-index:2}",
  ".rvw-empty{padding:28px 8px;text-align:center;color:var(--muted-foreground)}.rvw-foot{justify-content:space-between;padding:9px 16px;border:1px solid var(--border);border-top:0;color:var(--muted-foreground)}.rvw-legend{display:flex;gap:10px;flex-wrap:wrap;font-size:10px}.rvw-legend i{display:inline-block;width:8px;height:8px;margin-right:4px;border-radius:2px;background:var(--primary)}.rvw-legend i.radio{background:var(--info,#22d3ee)}",
  "@media(max-width:760px){.rvw-ruler{margin-left:96px}.rvw-lane{grid-template-columns:84px minmax(0,1fr) 32px;gap:8px}.rvw-toolbar .rvw-spacer{display:none}.rvw-transport{order:3;flex-basis:100%}}",
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

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

export function playbackPositionFromPointer(clientX: number, rect: TimelineRect, durationMs: number): number {
  const ratio = clamp((clientX - rect.left) / Math.max(1, rect.width), 0, 1)
  return Math.round(ratio * Math.max(0, durationMs))
}

export function timelineWindowAfterWheel(input: TimelineWheelInput): { startMs: number; endMs: number } {
  const durationMs = Math.max(1, input.durationMs)
  const spanMs = clamp(input.endMs - input.startMs, 1, durationMs)
  const maxStart = (span: number) => Math.max(0, durationMs - span)
  if (input.pan) {
    const startMs = clamp(Math.round(input.startMs + spanMs * input.deltaY / 500), 0, maxStart(spanMs))
    return { startMs, endMs: startMs + spanMs }
  }
  const nextSpan = clamp(Math.round(spanMs * (input.deltaY > 0 ? 1.2 : 1 / 1.2)), 1, durationMs)
  const focus = input.startMs + spanMs * clamp(input.anchorRatio, 0, 1)
  const startMs = clamp(Math.round(focus - nextSpan * clamp(input.anchorRatio, 0, 1)), 0, maxStart(nextSpan))
  return { startMs, endMs: startMs + nextSpan }
}

function SpeakerName({ lane }: { lane: VoiceLane }) {
  const content = <><span className="rvw-speaker-name">{lane.name}</span><span className="rvw-speaker-status">{lane.playerId ? "Профиль доступен" : "Не сопоставлен"}</span></>
  return lane.playerId
    ? <PlayerHoverLink playerId={lane.playerId} className="rvw-speaker-link">{content}</PlayerHoverLink>
    : <span className="rvw-speaker-link rvw-unresolved">{content}</span>
}

function Segment({ segment, viewStartMs, viewSpanMs, onClick }: { segment: { startMs: number; endMs: number; radio: boolean }; viewStartMs: number; viewSpanMs: number; onClick: () => void }) {
  const left = percentage(segment.startMs, viewStartMs, viewSpanMs)
  const right = percentage(segment.endMs, viewStartMs, viewSpanMs)
  if (right < left) return null
  return <button type="button" className={"rvw-segment" + (segment.radio ? " rvw-segment-radio" : "")} style={{ left: left + "%", width: Math.max(0.4, right - left) + "%" }} title={formatTime(segment.startMs) + "–" + formatTime(segment.endMs) + " · " + (segment.radio ? "радио" : "локальная речь")} aria-label={"Перейти к голосовому сегменту " + formatTime(segment.startMs)} onClick={onClick} />
}

export function ReplayVoiceWorkspace({ replayKind, initialData }: { replayKind: ReplayKind; initialData: ReplayVoiceWorkspaceData }) {
  const [bridgeData, setBridgeData] = useState<Record<string, unknown> | null>(null)
  const [timeMs, setTimeMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [channel, setChannel] = useState<VoiceChannelFilter>("all")
  const [selectedSpeaker, setSelectedSpeaker] = useState<number | null>(null)
  const [viewStartMs, setViewStartMs] = useState(0)
  const [viewEndMs, setViewEndMs] = useState(Math.max(1, initialData.durationMs))
  const [volume, setVolume] = useState(100)
  const [speed, setSpeed] = useState(1)
  const [mutedSpeakers, setMutedSpeakers] = useState<Set<number>>(new Set())
  const [dragging, setDragging] = useState(false)

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
    category: "voice",
    channel,
    activeOnly: false,
    speakerIds: selectedSpeaker === null ? new Set() : new Set([selectedSpeaker]),
  }), [channel, lanes, selectedSpeaker])
  const viewSpanMs = Math.max(1, viewEndMs - viewStartMs)
  const playhead = percentage(timeMs, viewStartMs, viewSpanMs)

  const seek = useCallback((value: number) => {
    const next = clamp(Math.round(value), 0, durationMs)
    bridge()?.seekTo?.(next)
    setTimeMs(next)
  }, [durationMs])
  const seekFromPointer = useCallback((event: React.PointerEvent<HTMLElement>, startMs: number, spanMs: number) => {
    const rect = event.currentTarget.getBoundingClientRect()
    seek(startMs + playbackPositionFromPointer(event.clientX, rect, spanMs))
  }, [seek])
  const beginSeek = useCallback((event: React.PointerEvent<HTMLElement>, startMs: number, spanMs: number) => {
    event.currentTarget.setPointerCapture?.(event.pointerId)
    setDragging(true)
    seekFromPointer(event, startMs, spanMs)
  }, [seekFromPointer])
  const moveSeek = useCallback((event: React.PointerEvent<HTMLElement>, startMs: number, spanMs: number) => {
    if (dragging) seekFromPointer(event, startMs, spanMs)
  }, [dragging, seekFromPointer])
  const stopSeek = useCallback((event: React.PointerEvent<HTMLElement>) => {
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    setDragging(false)
  }, [])
  const onTimelineWheel = useCallback((event: React.WheelEvent<HTMLElement>) => {
    event.preventDefault()
    const rect = event.currentTarget.getBoundingClientRect()
    const windowAfterWheel = timelineWindowAfterWheel({
      startMs: viewStartMs,
      endMs: viewEndMs,
      durationMs,
      anchorRatio: clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1),
      deltaY: event.deltaY,
      pan: event.shiftKey,
    })
    setViewStartMs(windowAfterWheel.startMs)
    setViewEndMs(windowAfterWheel.endMs)
  }, [durationMs, viewEndMs, viewStartMs])
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
  const toggleMute = (speakerId: number) => setMutedSpeakers((current) => {
    const next = new Set(current)
    const muted = !next.has(speakerId)
    if (muted) next.add(speakerId)
    else next.delete(speakerId)
    bridge()?.setSpeakerMuted?.(speakerId, muted)
    return next
  })

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
  }, [playing, seek, timeMs, togglePlaying])

  return <div className="rvw-shell">
    {React.createElement("style", null, styles)}
    <div className="rvw-head">
      <div><div className="rvw-title">{replayKind === "round" ? "Голосовая лента раунда" : "Голосовая лента лобби"}</div><div className="rvw-muted rvw-tiny">{lanes.length} участников · {playing ? "воспроизведение" : "пауза"}</div></div>
      <span className="rvw-tag">Активность речи по пакетам</span>
    </div>
    <div className="rvw-toolbar">
      <button type="button" className="rvw-btn rvw-btn-primary" onClick={togglePlaying}>{playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}{playing ? "Пауза" : "Воспроизвести"}</button>
      <span className="rvw-tiny rvw-muted">{formatTime(timeMs)} / {formatTime(durationMs)}</span>
      <div className="rvw-transport" role="slider" aria-label="Позиция записи" aria-valuemin={0} aria-valuemax={durationMs} aria-valuenow={timeMs} onPointerDown={(event) => beginSeek(event, 0, durationMs)} onPointerMove={(event) => moveSeek(event, 0, durationMs)} onPointerUp={stopSeek}>
        <div className="rvw-transport-line"><div className="rvw-transport-fill" style={{ width: percentage(timeMs, 0, durationMs) + "%" }} /></div><span className="rvw-transport-thumb" style={{ left: percentage(timeMs, 0, durationMs) + "%" }} />
      </div>
      <button type="button" className="rvw-btn" onClick={() => seek(timeMs - 5_000)}>−5с</button><button type="button" className="rvw-btn" onClick={() => seek(timeMs + 5_000)}>+5с</button>
      <span className="rvw-spacer" />
      <label className="rvw-tiny rvw-muted">Канал <select className="rvw-select" aria-label="Канал" value={channel} onChange={(event) => setChannel(event.target.value as VoiceChannelFilter)}><option value="all">Все</option><option value="local">Локальный</option><option value="radio">Радио</option></select></label>
      <label className="rvw-tiny rvw-muted">Участники <select className="rvw-select" aria-label="Участники" value={selectedSpeaker ?? "all"} onChange={(event) => setSelectedSpeaker(event.target.value === "all" ? null : Number(event.target.value))}><option value="all">Все</option>{lanes.map((lane) => <option key={lane.speakerId} value={lane.speakerId}>{lane.name}</option>)}</select></label>
      <label className="rvw-tiny rvw-muted">Скорость <select className="rvw-select" aria-label="Скорость" value={speed} onChange={(event) => { const value = Number(event.target.value); setSpeed(value); bridge()?.setSpeed?.(value) }}><option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select></label>
      <label className="rvw-tiny rvw-muted">Громкость <input aria-label="Громкость" type="range" min="0" max="100" value={volume} onChange={(event) => { const value = Number(event.target.value); setVolume(value); bridge()?.setMasterVolume?.(value / 100) }} /></label>
    </div>
    <section className="rvw-timeline" onWheel={onTimelineWheel}>
      <div className="rvw-timeline-meta"><span className="rvw-muted rvw-tiny">Клик или drag по шкале — перейти к моменту</span><span className="rvw-muted rvw-tiny">Колесо — масштаб · Shift+колесо — прокрутка</span></div>
      <div className="rvw-ruler">{[0, .1666, .3333, .5, .6666, .8333, 1].map((ratio) => <span key={ratio}>{formatTime(viewStartMs + viewSpanMs * ratio)}</span>)}</div>
      {filteredLanes.map((lane) => {
        const speaking = lane.segments.some((segment) => segment.startMs <= timeMs && timeMs <= segment.endMs)
        return <div className="rvw-lane" data-speaking={speaking} key={lane.speakerId}>
          <div className="rvw-name-wrap"><SpeakerName lane={lane} /></div>
          <div className="rvw-track" onPointerDown={(event) => beginSeek(event, viewStartMs, viewSpanMs)} onPointerMove={(event) => moveSeek(event, viewStartMs, viewSpanMs)} onPointerUp={stopSeek}>
            {lane.segments.map((segment) => <Segment key={segment.startMs + "-" + segment.endMs + "-" + segment.radio} segment={segment} viewStartMs={viewStartMs} viewSpanMs={viewSpanMs} onClick={() => seek(segment.startMs)} />)}<span className="rvw-playhead" style={{ left: playhead + "%" }} />
          </div>
          <button type="button" className="rvw-btn" title={mutedSpeakers.has(lane.speakerId) ? "Вернуть звук" : "Заглушить игрока"} onClick={() => toggleMute(lane.speakerId)}>{mutedSpeakers.has(lane.speakerId) ? <VolumeX className="size-3.5" /> : <Volume2 className="size-3.5" />}</button>
        </div>
      })}
      {filteredLanes.length === 0 && <div className="rvw-empty">Для выбранного канала или участника речи нет.</div>}
    </section>
    <div className="rvw-foot"><div className="rvw-legend"><span><i />локальная речь</span><span><i className="radio" />радио</span></div><span className="rvw-tiny">Space — play/pause · ←/→ — 5 секунд</span><Link className="rvw-tiny rvw-muted" to="/replays">К списку реплеев</Link></div>
  </div>
}
