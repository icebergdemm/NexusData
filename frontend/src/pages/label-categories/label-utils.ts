export interface LabelCategory {
  id: number
  name: string
  color: string
  icon: string
  is_secret: boolean
  folder_id: number | null
  sort_order: number
  description?: string | null
}

export interface LabelFolder {
  id: number
  name: string
  sort_order: number
}

export interface LabelCategoryGroup {
  label: string
  folderId: number | null
  categories: LabelCategory[]
}

export interface LabelCategoryPlacement {
  ordered_ids: number[]
  folder_ids: Record<string, number | null>
}

export function groupLabelCategories(categories: LabelCategory[], folders: LabelFolder[], includeEmptyFolders = false): LabelCategoryGroup[] {
  const groups = new Map<number | null, LabelCategory[]>()
  for (const category of [...categories].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name))) {
    const current = groups.get(category.folder_id) ?? []
    current.push(category)
    groups.set(category.folder_id, current)
  }

  const orderedFolders = [...folders].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name))
  const result: LabelCategoryGroup[] = []
  for (const folder of orderedFolders) {
    const group = groups.get(folder.id)
    if (group?.length || includeEmptyFolders) result.push({ label: folder.name, folderId: folder.id, categories: group ?? [] })
    groups.delete(folder.id)
  }
  const ungrouped = groups.get(null)
  if (ungrouped?.length || includeEmptyFolders) result.push({ label: "Без папки", folderId: null, categories: ungrouped ?? [] })
  return result
}

export function moveLabelCategoryToGroup(
  categories: LabelCategory[],
  folders: LabelFolder[],
  categoryId: number,
  targetFolderId: number | null,
  targetIndex: number,
): LabelCategoryPlacement {
  const groups = groupLabelCategories(categories, folders, true).map((group) => ({
    ...group,
    categories: group.categories.map((category) => ({ ...category })),
  }))
  const sourceGroup = groups.find((group) => group.categories.some((category) => category.id === categoryId))
  const targetGroup = groups.find((group) => group.folderId === targetFolderId)
  if (!sourceGroup || !targetGroup) {
    return {
      ordered_ids: categories.map((category) => category.id),
      folder_ids: Object.fromEntries(categories.map((category) => [String(category.id), category.folder_id])),
    }
  }

  const categoryIndex = sourceGroup.categories.findIndex((category) => category.id === categoryId)
  const [category] = sourceGroup.categories.splice(categoryIndex, 1)
  category.folder_id = targetFolderId
  const clampedIndex = Math.max(0, Math.min(targetIndex, targetGroup.categories.length))
  targetGroup.categories.splice(clampedIndex, 0, category)

  const ordered = groups.flatMap((group) => group.categories)
  return {
    ordered_ids: ordered.map((item) => item.id),
    folder_ids: Object.fromEntries(ordered.map((item) => [String(item.id), item.folder_id])),
  }
}

export function moveLabelCategory(ids: number[], categoryId: number, targetIndex: number): number[] {
  const fromIndex = ids.indexOf(categoryId)
  if (fromIndex < 0 || targetIndex < 0 || targetIndex >= ids.length || fromIndex === targetIndex) return [...ids]
  const next = [...ids]
  next.splice(fromIndex, 1)
  next.splice(targetIndex, 0, categoryId)
  return next
}
