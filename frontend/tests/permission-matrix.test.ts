import { strict as assert } from "node:assert"
import { test } from "node:test"
import { cyclePermissionState } from "../src/pages/users/PermissionMatrixEditor"

test("tri-state permission cycles unset, allow, deny, unset", () => {
  assert.equal(cyclePermissionState(undefined), true)
  assert.equal(cyclePermissionState(true), false)
  assert.equal(cyclePermissionState(false), undefined)
})
