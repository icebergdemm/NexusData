import { useCallback, useEffect, useRef, useState } from "react"
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react"
import { Crosshair, X } from "lucide-react"

import {
  PRECISION_HOLD_DELAY_MS,
  clamp,
  formatTimecode,
  parseTimecode,
  precisionScrubWindow,
  precisionTimeAtRatio,
  shouldActivatePrecisionScrub,
} from "./replay-timeline-utils"

interface ReplayBridge {
  seekTo: (milliseconds: number) => void
  stepBy: (milliseconds: number) => void
  getTimeMs: () => number
  getDurationMs: () => number
  setPlaying: (playing: boolean) => void
  isPlaying: () => boolean
  setSeekWindow: (startMs: number, endMs: number) => void
  clearSeekWindow: () => void
}

interface ReplayTimeDetail {
  timeMs: number
  durationMs: number
  playing: boolean
}

interface ReplayReadyDetail {
  durationMs: number
}

interface PrecisionWindow {
  startMs: number
  endMs: number
  anchorMs: number
}

interface HoldSession {
  pointerId: number
  pointerType: string
  startedAt: number
  startX: number
  startY: number
  maxMovementPx: number
  anchorMs: number
  activated: boolean
  timer: ReturnType<typeof setTimeout>
  dispose: () => void
}

function replayBridge(): ReplayBridge | undefined {
  return (window as unknown as { __BaroReplayUI?: ReplayBridge }).__BaroReplayUI
}

function compactTimecode(milliseconds: number): string {
  return formatTimecode(milliseconds).replace(/^00:/, "")
}

export function V2ReplayTransport({ initialDurationMs }: { initialDurationMs?: number | null }) {
  const [ready, setReady] = useState(false)
  const [timeMs, setTimeMs] = useState(0)
  const [durationMs, setDurationMs] = useState(Math.max(0, initialDurationMs ?? 0))
  const [playing, setPlaying] = useState(false)
  const [inputValue, setInputValue] = useState(() => formatTimecode(0))
  const [editing, setEditing] = useState(false)
  const [inputDirty, setInputDirty] = useState(false)
  const [invalid, setInvalid] = useState(false)
  const [holding, setHolding] = useState(false)
  const [holdRatio, setHoldRatio] = useState(0.5)
  const [precision, setPrecision] = useState<PrecisionWindow | null>(null)
  const precisionRef = useRef<PrecisionWindow | null>(null)
  const holdRef = useRef<HoldSession | null>(null)
  const cancelInputBlurRef = useRef(false)

  useEffect(() => {
    precisionRef.current = precision
  }, [precision])

  useEffect(() => {
    if (!editing) {
      setInputValue(formatTimecode(timeMs))
      setInputDirty(false)
    }
  }, [editing, timeMs])

  const cancelHold = useCallback(() => {
    const session = holdRef.current
    if (!session) return
    session.dispose()
  }, [])

  const closePrecision = useCallback((restoreAnchor = false) => {
    const bridge = replayBridge()
    const active = precisionRef.current
    bridge?.clearSeekWindow()
    if (restoreAnchor && active) bridge?.seekTo(active.anchorMs)
    precisionRef.current = null
    setPrecision(null)
  }, [])

  const openPrecision = useCallback((requestedCenterMs?: number) => {
    const bridge = replayBridge()
    if (!bridge) return false
    const duration = bridge.getDurationMs()
    if (duration <= 0) return false
    const center = clamp(requestedCenterMs ?? bridge.getTimeMs(), 0, duration)
    const bounds = precisionScrubWindow({ centerMs: center, durationMs: duration, windowMs: 30_000 })
    const next = { ...bounds, anchorMs: center }
    bridge.setPlaying(false)
    bridge.seekTo(center)
    bridge.setSeekWindow(bounds.startMs, bounds.endMs)
    precisionRef.current = next
    setPrecision(next)
    setPlaying(false)
    return true
  }, [])

  useEffect(() => {
    const sync = (detail: ReplayTimeDetail) => {
      const activePrecision = precisionRef.current
      if (activePrecision && (detail.playing || detail.timeMs < activePrecision.startMs || detail.timeMs > activePrecision.endMs)) {
        precisionRef.current = null
        setPrecision(null)
        replayBridge()?.clearSeekWindow()
      }
      setTimeMs(detail.timeMs)
      setDurationMs(detail.durationMs)
      setPlaying(detail.playing)
      setReady(detail.durationMs > 0)
    }
    const onTime = (event: Event) => sync((event as CustomEvent<ReplayTimeDetail>).detail)
    const onReady = (event: Event) => {
      const detail = (event as CustomEvent<ReplayReadyDetail>).detail
      const bridge = replayBridge()
      sync({
        timeMs: bridge?.getTimeMs() ?? 0,
        durationMs: detail.durationMs,
        playing: bridge?.isPlaying() ?? false,
      })
    }

    window.addEventListener("baroreplay:time", onTime)
    window.addEventListener("baroreplay:ready", onReady)
    const bridge = replayBridge()
    if (bridge?.getDurationMs()) {
      sync({ timeMs: bridge.getTimeMs(), durationMs: bridge.getDurationMs(), playing: bridge.isPlaying() })
    }
    return () => {
      window.removeEventListener("baroreplay:time", onTime)
      window.removeEventListener("baroreplay:ready", onReady)
      cancelHold()
      replayBridge()?.clearSeekWindow()
    }
  }, [cancelHold])

  const stepBy = (deltaMs: number) => {
    closePrecision(false)
    replayBridge()?.stepBy(deltaMs)
  }

  const seekTo = (nextMs: number) => {
    closePrecision(false)
    replayBridge()?.seekTo(clamp(nextMs, 0, durationMs))
  }

  const commitInput = () => {
    const parsed = parseTimecode(inputValue)
    if (parsed === null) {
      setInvalid(true)
      return
    }
    const next = clamp(parsed, 0, durationMs)
    setInvalid(false)
    setEditing(false)
    setInputDirty(false)
    setInputValue(formatTimecode(next))
    seekTo(next)
  }

  const beginHold = (event: PointerEvent<HTMLInputElement>) => {
    if (!ready || precisionRef.current || event.button > 0 || event.isPrimary === false) return
    if (holdRef.current) {
      cancelHold()
      return
    }

    const range = event.currentTarget
    const rect = range.getBoundingClientRect()
    const ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1)
    const startMs = Number(range.min) || 0
    const endMs = Number(range.max) || durationMs
    const anchorMs = precisionTimeAtRatio({ ratio, startMs, endMs, durationMs })
    const session = {} as HoldSession

    const dispose = () => {
      clearTimeout(session.timer)
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onRelease)
      window.removeEventListener("pointercancel", onRelease)
      window.removeEventListener("blur", onRelease)
      window.removeEventListener("pointerdown", onSecondPointer, true)
      document.removeEventListener("visibilitychange", onVisibilityChange)
      range.removeEventListener("lostpointercapture", onRelease)
      if (holdRef.current === session) holdRef.current = null
      setHolding(false)
    }
    const onMove = (pointerEvent: globalThis.PointerEvent) => {
      if (pointerEvent.pointerId !== session.pointerId) return
      if (session.activated) return
      const movement = Math.hypot(pointerEvent.clientX - session.startX, pointerEvent.clientY - session.startY)
      session.maxMovementPx = Math.max(session.maxMovementPx, movement)
      const tolerance = session.pointerType === "touch" ? 10 : 6
      if (session.maxMovementPx > tolerance) dispose()
    }
    const onRelease = (pointerEvent?: globalThis.PointerEvent | Event) => {
      if (pointerEvent && "pointerId" in pointerEvent && pointerEvent.pointerId !== session.pointerId) return
      const shouldClosePrecision = session.activated
      dispose()
      if (shouldClosePrecision) closePrecision(false)
    }
    const onSecondPointer = (pointerEvent: globalThis.PointerEvent) => {
      if (pointerEvent.pointerId !== session.pointerId) onRelease()
    }
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") onRelease()
    }

    session.pointerId = event.pointerId
    session.pointerType = event.pointerType
    session.startedAt = performance.now()
    session.startX = event.clientX
    session.startY = event.clientY
    session.maxMovementPx = 0
    session.anchorMs = anchorMs
    session.activated = false
    session.dispose = dispose
    session.timer = setTimeout(() => {
      const tolerance = session.pointerType === "touch" ? 10 : 6
      const activate = shouldActivatePrecisionScrub({
        elapsedMs: performance.now() - session.startedAt,
        pointerDown: holdRef.current === session,
        cancelled: false,
        maxMovementPx: session.maxMovementPx,
        movementTolerancePx: tolerance,
      })
      if (!activate) {
        dispose()
        return
      }
      session.activated = openPrecision(session.anchorMs)
      setHolding(false)
      if (!session.activated) dispose()
    }, PRECISION_HOLD_DELAY_MS)

    holdRef.current = session
    setHoldRatio(ratio)
    setHolding(true)
    window.addEventListener("pointermove", onMove, { passive: true })
    window.addEventListener("pointerup", onRelease, { passive: true })
    window.addEventListener("pointercancel", onRelease, { passive: true })
    window.addEventListener("blur", onRelease)
    window.addEventListener("pointerdown", onSecondPointer, true)
    document.addEventListener("visibilitychange", onVisibilityChange)
    range.addEventListener("lostpointercapture", onRelease)
  }

  const onRangeKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault()
      cancelHold()
      if (precisionRef.current) closePrecision(true)
      return
    }
    if (event.key === "Enter") {
      event.preventDefault()
      if (precisionRef.current) closePrecision(false)
      else openPrecision()
      return
    }
    if (event.key === " ") {
      event.preventDefault()
      const bridge = replayBridge()
      if (precisionRef.current) closePrecision(false)
      bridge?.setPlaying(!bridge.isPlaying())
      return
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault()
      seekTo(event.key === "Home" ? 0 : durationMs)
      return
    }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
    event.preventDefault()
    const direction = event.key === "ArrowLeft" ? -1 : 1
    const amount = event.ctrlKey || event.metaKey ? 100 : event.shiftKey ? 5_000 : 1_000
    const bridge = replayBridge()
    const active = precisionRef.current
    if (bridge && active) bridge.seekTo(clamp(bridge.getTimeMs() + direction * amount, active.startMs, active.endMs))
    else bridge?.stepBy(direction * amount)
  }

  return (
    <div className="rpv-v2-transport" data-precision={precision ? "true" : "false"}>
      <button type="button" className="rpv-btn rpv-step" disabled={!ready} onClick={() => stepBy(-5_000)} aria-label="Назад на 5 секунд">−5 с</button>
      <div
        className="rpv-seek-shell"
        data-holding={holding ? "true" : "false"}
        data-precision={precision ? "true" : "false"}
        style={{ "--rpv-hold-origin": `${holdRatio * 100}%` } as CSSProperties}
      >
        <input
          type="range"
          className="rpv-seek"
          id="rpvSeek"
          min={0}
          max={Math.max(1, durationMs)}
          step={1}
          defaultValue={0}
          disabled={!ready}
          aria-label="Позиция в реплее"
          aria-describedby="rpvSeekHelp"
          onPointerDown={beginHold}
          onContextMenu={(event) => event.preventDefault()}
          onKeyDown={onRangeKeyDown}
        />
        <div className="rpv-hold-progress" aria-hidden="true" />
        <div className="rpv-precision-panel" aria-hidden={!precision}>
          <div className="rpv-precision-copy">
            <span><Crosshair aria-hidden="true" /> Точный режим</span>
            <span>{precision ? `${compactTimecode(precision.startMs)} — ${compactTimecode(precision.endMs)}` : "Удерживайте шкалу 1,5 секунды"}</span>
          </div>
          <div className="rpv-precision-ruler" aria-hidden="true" />
          <button type="button" className="rpv-precision-close" onClick={() => closePrecision(false)} aria-label="Закрыть точный режим"><X /></button>
        </div>
      </div>
      <button type="button" className="rpv-btn rpv-step" disabled={!ready} onClick={() => stepBy(5_000)} aria-label="Вперёд на 5 секунд">+5 с</button>

      <form className="rpv-direct-time" onSubmit={(event) => { event.preventDefault(); commitInput() }}>
        <label htmlFor="rpvDirectTime">Точное время</label>
        <input
          id="rpvDirectTime"
          value={inputValue}
          disabled={!ready}
          spellCheck={false}
          inputMode="decimal"
          aria-invalid={invalid}
          aria-describedby={invalid ? "rpvDirectTimeError" : undefined}
          onFocus={() => {
            setEditing(true)
            setInputDirty(false)
            setInputValue(formatTimecode(timeMs))
          }}
          onChange={(event) => {
            setEditing(true)
            setInputDirty(true)
            setInvalid(false)
            setInputValue(event.target.value)
          }}
          onBlur={() => {
            if (cancelInputBlurRef.current) { cancelInputBlurRef.current = false; return }
            if (inputDirty) commitInput()
            else {
              setInvalid(false)
              setEditing(false)
              setInputValue(formatTimecode(timeMs))
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault()
              cancelInputBlurRef.current = true
              setInvalid(false)
              setEditing(false)
              setInputDirty(false)
              setInputValue(formatTimecode(timeMs))
              event.currentTarget.blur()
            }
          }}
        />
        <span aria-hidden="true">/ {compactTimecode(durationMs)}</span>
        {invalid && <span id="rpvDirectTimeError" role="alert">Формат: ЧЧ:ММ:СС.мс</span>}
      </form>

      <span id="rpvSeekHelp" className="sr-only">Стрелки меняют время на секунду, Shift со стрелкой на пять секунд, Control со стрелкой на сто миллисекунд. Удерживайте шкалу полторы секунды для точного режима.</span>
      <span className="sr-only" aria-live="polite">{holding ? "Удерживайте для точного режима" : precision ? "Точный режим включён" : ""}</span>
      <span className="sr-only">{playing ? "Реплей воспроизводится" : "Реплей на паузе"}</span>
    </div>
  )
}
