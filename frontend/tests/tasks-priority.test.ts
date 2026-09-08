import { strict as assert } from "node:assert"
import { test } from "node:test"
import { PRIORITY_COLORS, PRIORITY_LABELS } from "../src/pages/tasks/api"

test("ultra priority keeps the requested one-line label and color", () => {
  assert.equal(PRIORITY_LABELS.ultra, "УЛЬТРА СУПЕР МЕГА ОЧЕНЬ СРОЧНЫЙ СПАТЬ НЕЛЬЗЯ РАБОТАТЬ НАДО")
  assert.match(PRIORITY_COLORS.ultra, /^#(?:[0-9A-Fa-f]{6})$/)
})
