import test from "node:test"
import assert from "node:assert/strict"
import { groupLabelCategories, moveLabelCategory } from "../src/pages/label-categories/label-utils"

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
