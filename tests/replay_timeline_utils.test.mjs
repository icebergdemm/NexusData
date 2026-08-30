import assert from "node:assert/strict"
import test from "node:test"

import {
  MIN_TIMELINE_WINDOW_MS,
  nearestTickInterval,
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
