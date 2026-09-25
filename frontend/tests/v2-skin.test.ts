import assert from "node:assert/strict"
import test from "node:test"
import { DEFAULT_V2_SKIN, V2_SKINS, normalizeV2Skin, v2SkinHasLightDark } from "../src/lib/v2-skin"

test("the hard-edge design is the default, the mission design is the alternative", () => {
  assert.deepEqual([...V2_SKINS], ["edge", "mission"])
  assert.equal(DEFAULT_V2_SKIN, "edge")
})

test("a stored design is kept only when it is known", () => {
  assert.equal(normalizeV2Skin("mission"), "mission")
  assert.equal(normalizeV2Skin("edge"), "edge")
  for (const stale of ["", "MISSION", "neon", null, undefined, 42]) {
    assert.equal(normalizeV2Skin(stale), DEFAULT_V2_SKIN)
  }
})

test("only the hard-edge design offers light and dark", () => {
  assert.equal(v2SkinHasLightDark("edge"), true)
  assert.equal(v2SkinHasLightDark("mission"), false)
})
