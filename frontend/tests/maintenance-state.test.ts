import assert from "node:assert/strict"
import test from "node:test"
import { maintenanceMessageFromHealth } from "../src/components/layout/maintenance-state"
import { ApiError, isMaintenanceError } from "../src/lib/api"

test("maintenance state is decoded from a non-OK health response", () => {
  assert.equal(
    maintenanceMessageFromHealth({
      status: "maintenance",
      db_healthy: true,
      api_enabled: true,
      web_enabled: true,
      maintenance_message: "Обновление базы",
    }),
    "Обновление базы",
  )
})

test("healthy response clears an earlier maintenance message", () => {
  assert.equal(maintenanceMessageFromHealth({
    status: "ok",
    db_healthy: true,
    api_enabled: true,
    web_enabled: true,
    maintenance_message: null,
  }), null)
})

test("maintenance API errors are not rendered as generic connection failures", () => {
  assert.equal(isMaintenanceError(new ApiError(503, "База данных недоступна", { maintenance: true })), true)
  assert.equal(isMaintenanceError(new ApiError(503, "Ошибка сервера", { maintenance: false })), false)
  assert.equal(isMaintenanceError(new Error("network failure")), false)
})
