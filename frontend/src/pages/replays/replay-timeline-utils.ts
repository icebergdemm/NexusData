// Server text logs are second-precise, while demo events retain millisecond
// offsets.  A half-second floor keeps both useful without turning the ruler
// into an unreadable sub-frame view.
export const MIN_TIMELINE_WINDOW_MS = 500
export const PRECISION_HOLD_DELAY_MS = 1_500
export const EXACT_TIMELINE_WINDOW_MS = 20 * 60 * 1_000
export const EXACT_TIMELINE_EVENT_LIMIT = 10_000

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/**
 * Raw demo damage/shot frames drive the visual renderer but are not audit
 * actions. Older cached player scripts may still expose them through the
 * bridge, so the React investigation view applies the same boundary
 * defensively instead of trusting one particular static asset revision.
 */
export function isInvestigationTimelineEvent(event: {
  source?: string | null
  action_type?: string | null
}): boolean {
  if (event.source !== "demo_event") return true
  return event.action_type !== "damage" && event.action_type !== "shot"
}

export interface DamageItemEvidenceCandidate<TItem> {
  offsetMs: number
  actionType: string
  details: string
  targetName?: string | null
  item: TItem
}

/**
 * Correlate demo-only PvP damage with a structured item from the text log.
 * The v4 demo frame has no weapon field, so this intentionally accepts only
 * one unambiguous nearby item for the same actor.  Multiple different items
 * are safer to report as unknown than to attribute the hit incorrectly.
 */
export function selectDamageItemEvidence<TItem>(args: {
  damageTimeMs: number
  targetName?: string | null
  candidates: DamageItemEvidenceCandidate<TItem>[]
  itemKey: (item: TItem) => string
}): DamageItemEvidenceCandidate<TItem> | null {
  const normalise = (value?: string | null) => (value ?? "").trim().toLocaleLowerCase("ru")
  const target = normalise(args.targetName)
  const matches = args.candidates.filter((candidate) => {
    const delta = candidate.offsetMs - args.damageTimeMs
    if (delta < -3_000 || delta > 1_000) return false
    if (candidate.actionType !== "attack" && candidate.actionType !== "used") return false
    if (candidate.actionType !== "used" || !target) return true
    const candidateTarget = normalise(candidate.targetName)
    return candidateTarget === target || (!candidateTarget && normalise(candidate.details).includes(target))
  })
  if (!matches.length) return null

  const itemKeys = new Set(matches.map((candidate) => normalise(args.itemKey(candidate.item))).filter(Boolean))
  if (itemKeys.size !== 1) return null
  return matches.reduce((nearest, candidate) => (
    Math.abs(candidate.offsetMs - args.damageTimeMs) < Math.abs(nearest.offsetMs - args.damageTimeMs)
      ? candidate
      : nearest
  ))
}

/** Load original log rows whenever their volume is safe for the browser. */
export function shouldLoadExactTimeline(args: {
  durationMs: number
  windowMs: number
  overviewStatus?: string | null
  overviewTotal?: number | null
}): boolean {
  if (args.durationMs <= 0 || args.windowMs <= 0) return false
  if (args.windowMs <= EXACT_TIMELINE_WINDOW_MS) return true
  return args.overviewStatus === "ok"
    && Number.isFinite(args.overviewTotal)
    && (args.overviewTotal ?? 0) > 0
    && (args.overviewTotal ?? 0) <= EXACT_TIMELINE_EVENT_LIMIT
}

export function seekByMilliseconds(args: {
  currentMs: number
  deltaMs: number
  durationMs: number
}): number {
  return Math.round(clamp(args.currentMs + args.deltaMs, 0, Math.max(0, args.durationMs)))
}

export function shouldActivatePrecisionScrub(args: {
  elapsedMs: number
  pointerDown: boolean
  cancelled: boolean
  maxMovementPx: number
  movementTolerancePx?: number
}): boolean {
  return args.pointerDown && !args.cancelled && args.elapsedMs >= PRECISION_HOLD_DELAY_MS && args.maxMovementPx <= (args.movementTolerancePx ?? 6)
}

export function precisionScrubWindow(args: {
  centerMs: number
  durationMs: number
  windowMs: number
}): { startMs: number; endMs: number } {
  const durationMs = Math.max(0, Math.round(args.durationMs))
  const windowMs = clamp(Math.round(args.windowMs), 1, Math.max(1, durationMs))
  const centerMs = clamp(Math.round(args.centerMs), 0, durationMs)
  const startMs = clamp(centerMs - windowMs / 2, 0, Math.max(0, durationMs - windowMs))
  return { startMs: Math.round(startMs), endMs: Math.round(Math.min(durationMs, startMs + windowMs)) }
}

export function precisionTimeAtRatio(args: {
  ratio: number
  startMs: number
  endMs: number
  durationMs: number
}): number {
  const ratio = clamp(args.ratio, 0, 1)
  const value = args.startMs + (args.endMs - args.startMs) * ratio
  return Math.round(clamp(value, 0, Math.max(0, args.durationMs)))
}
/** Editor-style timecode with millisecond precision. */
export function formatTimecode(milliseconds: number): string {
  const ms = Math.max(0, Math.round(milliseconds))
  const hours = Math.floor(ms / 3_600_000)
  const minutes = Math.floor((ms % 3_600_000) / 60_000)
  const seconds = Math.floor((ms % 60_000) / 1_000)
  const fraction = ms % 1_000
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(fraction).padStart(3, "0")}`
}

/** Accepts HH:MM:SS.mmm, MM:SS.mmm or a bare number of seconds. */
export function parseTimecode(value: string): number | null {
  const raw = value.trim().replace(",", ".")
  if (!raw) return null
  const chunks = raw.split(":")
  if (chunks.length > 3 || chunks.some((part) => part.trim() === "")) return null
  const numbers = chunks.map(Number)
  if (numbers.some((part) => !Number.isFinite(part) || part < 0)) return null

  let hours = 0
  let minutes = 0
  let seconds = 0
  if (numbers.length === 3) {
    ;[hours, minutes, seconds] = numbers
    if (!Number.isInteger(hours) || !Number.isInteger(minutes) || minutes >= 60 || seconds >= 60) return null
  } else if (numbers.length === 2) {
    ;[minutes, seconds] = numbers
    if (!Number.isInteger(minutes) || seconds >= 60) return null
  } else {
    ;[seconds] = numbers
  }
  return Math.round((hours * 3600 + minutes * 60 + seconds) * 1000)
}

export function nearestTickInterval(visibleMs: number, pixelWidth: number): number {
  const targetMs = Math.max(1, visibleMs / Math.max(1, pixelWidth / 88))
  const intervals = [
    50, 100, 250, 500, 1_000, 2_000, 5_000, 10_000, 15_000, 30_000,
    60_000, 120_000, 300_000, 600_000, 900_000, 1_800_000, 3_600_000,
  ]
  return intervals.find((interval) => interval >= targetMs) ?? intervals[intervals.length - 1]
}

export function findEventAtOrBefore<T extends { offset_ms: number }>(events: T[], timeMs: number): number {
  let low = 0
  let high = events.length - 1
  let found = -1
  while (low <= high) {
    const middle = (low + high) >> 1
    if (events[middle].offset_ms <= timeMs) {
      found = middle
      low = middle + 1
    } else {
      high = middle - 1
    }
  }
  return found
}

export function zoomWindowAroundPoint(args: {
  durationMs: number
  viewStartMs: number
  windowMs: number
  anchorRatio: number
  factor: number
}): { viewStartMs: number; windowMs: number } {
  const durationMs = Math.max(1, args.durationMs)
  const previousWindow = clamp(args.windowMs, MIN_TIMELINE_WINDOW_MS, durationMs)
  const windowMs = clamp(previousWindow / args.factor, MIN_TIMELINE_WINDOW_MS, durationMs)
  const ratio = clamp(args.anchorRatio, 0, 1)
  const anchorTime = args.viewStartMs + previousWindow * ratio
  const viewStartMs = clamp(anchorTime - windowMs * ratio, 0, Math.max(0, durationMs - windowMs))
  return { viewStartMs, windowMs }
}
