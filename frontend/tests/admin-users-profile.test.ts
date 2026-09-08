import { strict as assert } from "node:assert"
import { test } from "node:test"
import * as permissionMatrix from "../src/pages/users/PermissionMatrixEditor"

const model = permissionMatrix as typeof permissionMatrix & {
  permissionStatusLabel?: (value: boolean) => string
  setPermissionOverride?: (
    value: Record<string, Record<string, boolean | undefined>>,
    page: string,
    action: string,
    next: boolean | undefined,
  ) => Record<string, Record<string, boolean | undefined>>
}

test("permission profile exposes only user-facing allow and deny labels", () => {
  assert.equal(model.permissionStatusLabel?.(true), "Разрешено")
  assert.equal(model.permissionStatusLabel?.(false), "Запрещено")
})

test("permission override preserves explicit deny and removes only the selected key on reset", () => {
  const initial = { ratings: { view: true, edit: false }, players: { view: true } }
  const setOverride = model.setPermissionOverride!

  assert.deepEqual(setOverride(initial, "ratings", "view", false), {
    ratings: { view: false, edit: false },
    players: { view: true },
  })
  assert.deepEqual(setOverride(initial, "ratings", "view", undefined), {
    ratings: { edit: false },
    players: { view: true },
  })
  assert.deepEqual(setOverride(initial, "players", "view", undefined), {
    ratings: { view: true, edit: false },
  })
})
