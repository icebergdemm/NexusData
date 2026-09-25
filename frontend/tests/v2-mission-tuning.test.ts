import assert from "node:assert/strict"
import test from "node:test"
import { DEFAULT_MISSION_TUNING, normalizeMissionTuning } from "../src/lib/v2-mission-tuning"

test("nothing stored gives the default mission design", () => {
  assert.deepEqual(normalizeMissionTuning(null), DEFAULT_MISSION_TUNING)
  assert.deepEqual(normalizeMissionTuning("garbage"), DEFAULT_MISSION_TUNING)
})

test("valid values are kept", () => {
  const tuning = normalizeMissionTuning({
    accent: "#B388FF", line: "#7cf5c8", glow: 0, backdropGlow: 40, borders: 90,
    rings: false, ringsMotion: false, font: "sans",
  })
  assert.equal(tuning.accent, "#b388ff")
  assert.equal(tuning.line, "#7cf5c8")
  assert.equal(tuning.glow, 0)
  assert.equal(tuning.rings, false)
  assert.equal(tuning.font, "sans")
})

test("malformed values fall back field by field", () => {
  const tuning = normalizeMissionTuning({
    accent: "red; background: url(x)", line: "#12345", glow: 999, backdropGlow: -5,
    borders: 0, rings: "yes", font: "comic",
  })
  assert.equal(tuning.accent, DEFAULT_MISSION_TUNING.accent)
  assert.equal(tuning.line, DEFAULT_MISSION_TUNING.line)
  assert.equal(tuning.glow, 100)
  assert.equal(tuning.backdropGlow, 0)
  assert.equal(tuning.borders, 10)
  assert.equal(tuning.rings, DEFAULT_MISSION_TUNING.rings)
  assert.equal(tuning.font, DEFAULT_MISSION_TUNING.font)
})
