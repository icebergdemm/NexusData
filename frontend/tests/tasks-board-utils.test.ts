import { strict as assert } from "node:assert"
import { test } from "node:test"
import { getPublicTaskStatusBadge, shouldScrollTaskColumn } from "../src/pages/tasks/task-board-utils"
import { STATUS_COLORS, STATUS_LABELS } from "../src/pages/tasks/api"

test("public requests show the testing status with its own label and color", () => {
  assert.deepEqual(getPublicTaskStatusBadge("testing"), {
    label: STATUS_LABELS.testing,
    color: STATUS_COLORS.testing,
  })
})

test("public requests combine queue and in-progress into the development label", () => {
  assert.deepEqual(getPublicTaskStatusBadge("in_dev_queue"), {
    label: "Передано в разработку",
    color: "#3B82F6",
  })
  assert.deepEqual(getPublicTaskStatusBadge("in_progress"), {
    label: "Передано в разработку",
    color: "#3B82F6",
  })
})

test("only system board columns with more than ten tasks get a scroll area", () => {
  assert.equal(shouldScrollTaskColumn(true, 0), false)
  assert.equal(shouldScrollTaskColumn(true, 10), false)
  assert.equal(shouldScrollTaskColumn(true, 11), true)
  assert.equal(shouldScrollTaskColumn(false, 11), false)
})
