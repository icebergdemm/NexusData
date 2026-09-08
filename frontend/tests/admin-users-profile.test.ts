import { strict as assert } from "node:assert"
import { test } from "node:test"
import * as permissionMatrix from "../src/pages/users/PermissionMatrixEditor"

const model = permissionMatrix as typeof permissionMatrix & {
  permissionStatusLabel?: (value: boolean) => string
  hasPermissionConflict?: (sources: Array<{ value: boolean; applies?: boolean }>) => boolean
  resolvePermissionValue?: (sources: Array<{ value: boolean; applies?: boolean }>, policyMode: string) => boolean
  formatPermissionSource?: (source: { kind: string; [key: string]: unknown }) => string
  permissionSourceHref?: (source: { kind: string; [key: string]: unknown }) => string | undefined
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

test("permission conflict ignores non-operative department denies", () => {
  const hasConflict = model.hasPermissionConflict!

  assert.equal(hasConflict([{ value: true }, { value: false }]), true)
  assert.equal(hasConflict([{ value: true }, { value: false, applies: false }]), false)
})

test("deny_v2 resolves the current effective badge from live sources", () => {
  const resolve = model.resolvePermissionValue!

  assert.equal(resolve([{ value: true }], "deny_v2"), true)
  assert.equal(resolve([{ value: true }, { value: false }], "deny_v2"), false)
  assert.equal(resolve([{ value: false, applies: false }, { value: true }], "deny_v2"), true)
})

test("permission details describe hidden source rows", () => {
  const format = model.formatPermissionSource!

  assert.equal(format({ kind: "individual", value: true }), "Индивидуальное правило")
  assert.equal(format({ kind: "group", value: true, group_name: "Модераторы" }), "Группа: Модераторы")
  assert.equal(format({ kind: "group", value: false, group_name: "Модераторы", applies: false }), "Группа: Модераторы")
  assert.equal(format({ kind: "superuser", value: true }), "Superuser: полный обход")
})

test("permission source links target the exact editable group or department level", () => {
  const href = model.permissionSourceHref!

  assert.equal(href({ kind: "group", group_id: 7, group_name: "Модераторы" }), "/users?tab=groups&group_id=7#group-7")
  assert.equal(href({ kind: "department_access_level", department_id: 12, level_id: 4 }), "/departments/12#level-4")
  assert.equal(href({ kind: "department_site", department_id: 12 }), "/departments/12")
  assert.equal(href({ kind: "individual", value: true }), undefined)
  assert.equal(href({ kind: "group", group_id: null }), undefined)
})
