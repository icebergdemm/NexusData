import test from "node:test"
import assert from "node:assert/strict"
import { formatRestartCountdown, restartStatusLabel } from "../src/pages/administration/server-restart-utils"

test("countdown formats the remaining time as hh:mm:ss", () => {
  const now = Date.parse("2026-09-08T02:30:00Z")
  assert.equal(formatRestartCountdown("2026-09-08T04:01:09Z", now), "01:31:09")
})

test("countdown clamps expired or missing timestamps to zero", () => {
  const now = Date.parse("2026-09-08T02:30:00Z")
  assert.equal(formatRestartCountdown("2026-09-08T02:29:59Z", now), "00:00:00")
  assert.equal(formatRestartCountdown(null, now), "00:00:00")
})

test("restart status has explicit operator-facing labels", () => {
  assert.equal(restartStatusLabel("waiting_conditions"), "Ожидание условий")
  assert.equal(restartStatusLabel("warning_sent"), "Предупреждение отправлено")
  assert.equal(restartStatusLabel("error"), "Ошибка")
  assert.equal(restartStatusLabel("disabled"), "Выключен")
})
