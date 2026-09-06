import assert from "node:assert/strict"
import test from "node:test"

import {
  formatSessionDuration,
  formatSessionStart,
  getJobColor,
} from "../frontend/src/pages/moderation/formatters.ts"

const atMsk = (time) => Date.parse(`${time}+03:00`)

test("missing session start has no duration", () => {
  assert.equal(formatSessionDuration(null, atMsk("2026-09-04T12:25:00")), "-")
})

test("session duration shows exactly 25 minutes", () => {
  assert.equal(
    formatSessionDuration("2026-09-04T12:00:00", atMsk("2026-09-04T12:25:00")),
    "25 мин",
  )
})

test("session duration floors incomplete minutes", () => {
  assert.equal(
    formatSessionDuration("2026-09-04T12:00:00", atMsk("2026-09-04T12:25:59")),
    "25 мин",
  )
})

test("future session start is clamped to zero minutes", () => {
  assert.equal(
    formatSessionDuration("2026-09-04T12:30:00", atMsk("2026-09-04T12:25:00")),
    "0 мин",
  )
})

test("session start is displayed as a localized hour and minute", () => {
  const value = formatSessionStart("2026-09-04T11:06:00Z")

  assert.match(value, /^с \d{2}:06$/)
})

test("invalid session start has no tooltip text", () => {
  assert.equal(formatSessionStart("not-a-date"), "-")
})

test("job colors keep assistants white and players without a character blue", () => {
  assert.equal(getJobColor("medicaldoctor"), "#CF4F30")
  assert.equal(getJobColor("ENGINEER"), "#E0B86E")
  assert.equal(getJobColor("assistant"), undefined)
  assert.equal(getJobColor("customjob"), undefined)
  assert.equal(getJobColor(null), "#3b82f6")
})
