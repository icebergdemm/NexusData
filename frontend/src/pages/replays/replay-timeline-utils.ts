// Server text logs are second-precise, while demo events retain millisecond
// offsets.  A half-second floor keeps both useful without turning the ruler
// into an unreadable sub-frame view.
export const MIN_TIMELINE_WINDOW_MS = 500

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
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
