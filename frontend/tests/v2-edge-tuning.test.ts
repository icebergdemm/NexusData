import assert from "node:assert/strict"
import test from "node:test"
import { DEFAULT_EDGE_TUNING, normalizeEdgeTuning } from "../src/lib/v2-edge-tuning"

test("nothing stored gives dimmed frames with the pointer light on", () => {
  assert.deepEqual(normalizeEdgeTuning(null), DEFAULT_EDGE_TUNING)
  assert.deepEqual(normalizeEdgeTuning("garbage"), DEFAULT_EDGE_TUNING)
  assert.ok(DEFAULT_EDGE_TUNING.lines < 100)
  assert.equal(DEFAULT_EDGE_TUNING.spotlight, true)
})

test("values are clamped and malformed fields fall back one by one", () => {
  const tuning = normalizeEdgeTuning({ lines: 5, spotlight: "yes", spotlightSize: 9000, spotlightStrength: 55.4 })
  assert.equal(tuning.lines, 15)
  assert.equal(tuning.spotlight, DEFAULT_EDGE_TUNING.spotlight)
  assert.equal(tuning.spotlightSize, 600)
  assert.equal(tuning.spotlightStrength, 55)
})

test("the light can be switched off", () => {
  assert.equal(normalizeEdgeTuning({ spotlight: false }).spotlight, false)
})
