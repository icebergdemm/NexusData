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

export function groupLabelCategories(categories: LabelCategory[], folders: LabelFolder[]): LabelCategoryGroup[] {
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
    if (group?.length) result.push({ label: folder.name, folderId: folder.id, categories: group })
    groups.delete(folder.id)
  }
  const ungrouped = groups.get(null)
  if (ungrouped?.length) result.push({ label: "Без папки", folderId: null, categories: ungrouped })
  return result
}

export function moveLabelCategory(ids: number[], categoryId: number, targetIndex: number): number[] {
  const fromIndex = ids.indexOf(categoryId)
  if (fromIndex < 0 || targetIndex < 0 || targetIndex >= ids.length || fromIndex === targetIndex) return [...ids]
  const next = [...ids]
  next.splice(fromIndex, 1)
  next.splice(targetIndex, 0, categoryId)
  return next
}
