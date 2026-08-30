import assert from "node:assert/strict"
import test from "node:test"

import {
  MIN_TIMELINE_WINDOW_MS,
  PRECISION_HOLD_DELAY_MS,
  nearestTickInterval,
  parseTimecode,
  precisionScrubWindow,
  precisionTimeAtRatio,
  seekByMilliseconds,
  shouldActivatePrecisionScrub,
  formatTimecode,
  zoomWindowAroundPoint,
} from "../frontend/src/pages/replays/replay-timeline-utils.ts"


test("deep zoom keeps the pointed time under the cursor", () => {
  const before = {
    durationMs: 120_000,
    viewStartMs: 20_000,
    windowMs: 60_000,
    anchorRatio: 0.25,
    factor: 3,
  }
  const anchorBefore = before.viewStartMs + before.windowMs * before.anchorRatio

  const result = zoomWindowAroundPoint(before)
  const anchorAfter = result.viewStartMs + result.windowMs * before.anchorRatio

  assert.deepEqual(result, { viewStartMs: 30_000, windowMs: 20_000 })
  assert.equal(anchorAfter, anchorBefore)
})


test("deep zoom stops at the half-second editor window", () => {
  const result = zoomWindowAroundPoint({
    durationMs: 120_000,
    viewStartMs: 30_000,
    windowMs: 20_000,
    anchorRatio: 0.5,
    factor: 100,
  })

  assert.equal(result.windowMs, MIN_TIMELINE_WINDOW_MS)
  assert.equal(result.viewStartMs, 39_750)
})


test("zooming out to the full replay resets the viewport safely", () => {
  const result = zoomWindowAroundPoint({
    durationMs: 90_000,
    viewStartMs: 50_000,
    windowMs: 10_000,
    anchorRatio: 0.75,
    factor: 0.01,
  })

  assert.deepEqual(result, { viewStartMs: 0, windowMs: 90_000 })
})


test("zoom close to the replay edge clamps without overscrolling", () => {
  const result = zoomWindowAroundPoint({
    durationMs: 100_000,
    viewStartMs: 80_000,
    windowMs: 20_000,
    anchorRatio: 0.9,
    factor: 0.5,
  })

  assert.deepEqual(result, { viewStartMs: 60_000, windowMs: 40_000 })
})


test("deeper windows receive fine-grained ruler ticks", () => {
  assert.equal(nearestTickInterval(500, 1_000), 50)
  assert.equal(nearestTickInterval(5_000, 1_000), 500)
  assert.equal(nearestTickInterval(60_000, 1_000), 10_000)
})


test("five-second transport steps clamp at both replay boundaries", () => {
  assert.equal(seekByMilliseconds({ currentMs: 12_345, deltaMs: -5_000, durationMs: 30_000 }), 7_345)
  assert.equal(seekByMilliseconds({ currentMs: 2_000, deltaMs: -5_000, durationMs: 30_000 }), 0)
  assert.equal(seekByMilliseconds({ currentMs: 27_500, deltaMs: 5_000, durationMs: 30_000 }), 30_000)
  assert.equal(seekByMilliseconds({ currentMs: 12_345.6, deltaMs: 5_000, durationMs: 30_000 }), 17_346)
})


test("exact transport time accepts editor timecodes and round-trips milliseconds", () => {
  assert.equal(parseTimecode("00:09:44.566"), 584_566)
  assert.equal(parseTimecode("09:44.566"), 584_566)
  assert.equal(parseTimecode("584,566"), 584_566)
  assert.equal(formatTimecode(584_566), "00:09:44.566")
  assert.equal(formatTimecode(parseTimecode("12:34:56.789")), "12:34:56.789")
})


test("exact transport time rejects ambiguous or out-of-range clock fields", () => {
  assert.equal(parseTimecode("00:60:00"), null)
  assert.equal(parseTimecode("00:10:60"), null)
  assert.equal(parseTimecode("09::44"), null)
  assert.equal(parseTimecode("not-a-time"), null)
})


test("precision scrub activates only after one and a half stationary seconds", () => {
  const stationary = { pointerDown: true, cancelled: false, maxMovementPx: 0 }

  assert.equal(shouldActivatePrecisionScrub({ ...stationary, elapsedMs: PRECISION_HOLD_DELAY_MS - 1 }), false)
  assert.equal(shouldActivatePrecisionScrub({ ...stationary, elapsedMs: PRECISION_HOLD_DELAY_MS }), true)
  assert.equal(PRECISION_HOLD_DELAY_MS, 1_500)
})


test("movement, pointer release and cancellation prevent precision activation", () => {
  const elapsedMs = PRECISION_HOLD_DELAY_MS + 500

  assert.equal(shouldActivatePrecisionScrub({ elapsedMs, pointerDown: true, cancelled: false, maxMovementPx: 12 }), false)
  assert.equal(shouldActivatePrecisionScrub({ elapsedMs, pointerDown: false, cancelled: false, maxMovementPx: 0 }), false)
  assert.equal(shouldActivatePrecisionScrub({ elapsedMs, pointerDown: true, cancelled: true, maxMovementPx: 0 }), false)
  assert.equal(shouldActivatePrecisionScrub({ elapsedMs, pointerDown: true, cancelled: false, maxMovementPx: 9, movementTolerancePx: 10 }), true)
})


test("precision scrub maps the local window smoothly around the held time", () => {
  const window = precisionScrubWindow({ centerMs: 20_000, durationMs: 60_000, windowMs: 10_000 })

  assert.deepEqual(window, { startMs: 15_000, endMs: 25_000 })
  assert.equal(precisionTimeAtRatio({ ratio: 0, ...window, durationMs: 60_000 }), 15_000)
  assert.equal(precisionTimeAtRatio({ ratio: 0.5, ...window, durationMs: 60_000 }), 20_000)
  assert.equal(precisionTimeAtRatio({ ratio: 1, ...window, durationMs: 60_000 }), 25_000)
})


test("precision scrub keeps its local span at replay edges and clamps pointer ratios", () => {
  const nearStart = precisionScrubWindow({ centerMs: 1_000, durationMs: 60_000, windowMs: 10_000 })
  const nearEnd = precisionScrubWindow({ centerMs: 59_000, durationMs: 60_000, windowMs: 10_000 })

  assert.deepEqual(nearStart, { startMs: 0, endMs: 10_000 })
  assert.deepEqual(nearEnd, { startMs: 50_000, endMs: 60_000 })
  assert.equal(precisionTimeAtRatio({ ratio: -1, ...nearStart, durationMs: 60_000 }), 0)
  assert.equal(precisionTimeAtRatio({ ratio: 2, ...nearEnd, durationMs: 60_000 }), 60_000)
})
