import test from "node:test"
import assert from "node:assert/strict"
import { getLabelDropIndex, groupLabelCategories, moveLabelCategory, moveLabelCategoryToGroup } from "../src/pages/label-categories/label-utils"

const categories = [
  { id: 1, name: "Первая", color: "#111111", icon: "1", is_secret: false, folder_id: 10, sort_order: 0 },
  { id: 2, name: "Без папки", color: "#222222", icon: "2", is_secret: false, folder_id: null, sort_order: 1 },
  { id: 3, name: "Вторая", color: "#333333", icon: "3", is_secret: true, folder_id: 10, sort_order: 2 },
]

test("groups labels by folder while preserving global importance order", () => {
  const grouped = groupLabelCategories(categories, [{ id: 10, name: "Основные", sort_order: 0 }])

  assert.deepEqual(grouped.map((group) => group.label), ["Основные", "Без папки"])
  assert.deepEqual(grouped[0].categories.map((category) => category.id), [1, 3])
})

test("moves a label in the global order", () => {
  assert.deepEqual(moveLabelCategory([1, 2, 3], 3, 0), [3, 1, 2])
})

test("moves a label into another folder and returns the new global placement", () => {
  const result = moveLabelCategoryToGroup(categories, [
    { id: 10, name: "Основные", sort_order: 0 },
    { id: 20, name: "Вторичные", sort_order: 1 },
  ], 1, 20, 0)

  assert.deepEqual(result.ordered_ids, [3, 1, 2])
  assert.deepEqual(result.folder_ids, { "1": 20, "2": null, "3": 10 })
})

test("can keep an empty folder as a drop target", () => {
  const grouped = groupLabelCategories(categories, [
    { id: 10, name: "Основные", sort_order: 0 },
    { id: 20, name: "Вторичные", sort_order: 1 },
  ], true)

  assert.deepEqual(grouped.map((group) => group.label), ["Основные", "Вторичные", "Без папки"])
  assert.deepEqual(grouped[1].categories, [])
})

test("keeps an empty ungrouped bucket as a drop target", () => {
  const grouped = groupLabelCategories([
    { ...categories[0], folder_id: 10 },
  ], [{ id: 10, name: "Основные", sort_order: 0 }], true)

  assert.deepEqual(grouped.map((group) => group.label), ["Основные", "Без папки"])
  assert.deepEqual(grouped[1].categories, [])
})

test("calculates insertion before and after a target row", () => {
  const group = groupLabelCategories(categories, [{ id: 10, name: "Основные", sort_order: 0 }])[0]

  assert.equal(getLabelDropIndex(group.categories, 1, 3, "before"), 0)
  assert.equal(getLabelDropIndex(group.categories, 1, 3, "after"), 1)
  assert.equal(getLabelDropIndex(group.categories, 3, 1, "after"), 1)
})

test("appends to a group when the drop target is its header", () => {
  const group = groupLabelCategories(categories, [{ id: 10, name: "Основные", sort_order: 0 }])[0]

  assert.equal(getLabelDropIndex(group.categories, 1, undefined, "after"), 1)
})
