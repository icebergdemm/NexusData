import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, Check, ChevronDown, Folder, FolderPlus, GripVertical, Lock, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { canDo, useCurrentAdmin } from "@/lib/auth"
import { getLabelDropIndex, groupLabelCategories, moveLabelCategoryToGroup, type LabelCategory, type LabelDropPosition, type LabelFolder } from "./label-utils"

interface LabelCategoriesResponse { categories: LabelCategory[]; folders: LabelFolder[] }
type LabelDraft = Omit<LabelCategory, "id" | "sort_order"> & { folder_id: number | null }
interface DropTarget { folderId: number | null; categoryId?: number; position: LabelDropPosition }

const emptyDraft: LabelDraft = { name: "", color: "#888888", icon: "🏷", is_secret: false, folder_id: null, description: "" }

export function LabelCategoriesPage() {
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()
  const [form, setForm] = useState<LabelDraft>(emptyDraft)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState<LabelDraft>(emptyDraft)
  const [folderName, setFolderName] = useState("")
  const [editingFolderId, setEditingFolderId] = useState<number | null>(null)
  const [editingFolderName, setEditingFolderName] = useState("")
  const [search, setSearch] = useState("")
  const [dragId, setDragId] = useState<number | null>(null)
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null)
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({})
  const dragPreviewRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => () => {
    dragPreviewRef.current?.remove()
    dragPreviewRef.current = null
  }, [])

  const canSecret = !!admin && canDo(admin, "players", "manage_secret_labels")
  const canCreatePublic = !!admin && (canDo(admin, "players", "create_label_category") || canDo(admin, "players", "manage_label_categories"))
  const canEditPublic = !!admin && (canDo(admin, "players", "edit_label_category") || canDo(admin, "players", "manage_label_categories"))
  const canDeletePublic = !!admin && (canDo(admin, "players", "delete_label_category") || canDo(admin, "players", "manage_label_categories"))
  const canCreate = canCreatePublic || canSecret
  const canOrganize = canCreate || canEditPublic || canDeletePublic

  useEffect(() => {
    if (canSecret && !canCreatePublic) {
      setForm((current) => current.is_secret ? current : { ...current, is_secret: true })
    }
  }, [canCreatePublic, canSecret])

  const { data, error, isError, isLoading, refetch } = useQuery({
    queryKey: ["label-categories"],
    queryFn: () => api.get<LabelCategoriesResponse>("/api/v1/admin/label-categories"),
  })
  const categories = data?.categories ?? []
  const folders = data?.folders ?? []
  const visibleCategories = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return categories
    return categories.filter((category) => {
      const folder = folders.find((item) => item.id === category.folder_id)
      return `${category.name} ${category.description ?? ""} ${folder?.name ?? ""}`.toLocaleLowerCase().includes(query)
    })
  }, [categories, folders, search])
  const categoryGroups = useMemo(
    () => groupLabelCategories(visibleCategories, folders, !search.trim()),
    [visibleCategories, folders, search],
  )
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["label-categories"] })
  const clearDragPreview = () => {
    dragPreviewRef.current?.remove()
    dragPreviewRef.current = null
  }
  const clearDragState = () => {
    setDragId(null)
    setDropTarget(null)
    clearDragPreview()
  }
  const mutationError = (e: Error) => { toast.error(e.message); clearDragState(); refetch() }

  const create = useMutation({ mutationFn: () => api.post("/api/v1/admin/label-categories", form), onSuccess: () => { toast.success("Метка создана"); setForm({ ...emptyDraft, is_secret: canSecret && !canCreatePublic }); invalidate() }, onError: mutationError })
  const update = useMutation({ mutationFn: ({ id, ...body }: { id: number } & LabelDraft) => api.post(`/api/v1/admin/label-categories/${id}`, body), onSuccess: () => { toast.success("Метка обновлена"); setEditingId(null); invalidate() }, onError: mutationError })
  const remove = useMutation({ mutationFn: (id: number) => api.delete(`/api/v1/admin/label-categories/${id}`), onSuccess: () => { toast.success("Метка удалена"); invalidate() }, onError: mutationError })
  const reorder = useMutation({ mutationFn: (placement: { ordered_ids: number[]; folder_ids: Record<string, number | null> }) => api.post("/api/v1/admin/label-categories/reorder", placement), onSuccess: () => { clearDragState(); invalidate() }, onError: mutationError })
  const createFolder = useMutation({ mutationFn: () => api.post("/api/v1/admin/label-folders", { name: folderName.trim() }), onSuccess: () => { toast.success("Папка создана"); setFolderName(""); invalidate() }, onError: mutationError })
  const updateFolder = useMutation({ mutationFn: ({ id, name }: { id: number; name: string }) => api.post(`/api/v1/admin/label-folders/${id}`, { name }), onSuccess: () => { toast.success("Папка обновлена"); setEditingFolderId(null); invalidate() }, onError: mutationError })
  const removeFolder = useMutation({ mutationFn: (id: number) => api.delete(`/api/v1/admin/label-folders/${id}`), onSuccess: () => { toast.success("Папка удалена"); invalidate() }, onError: mutationError })

  const startEdit = (category: LabelCategory) => {
    setEditingId(category.id)
    setEditDraft({ name: category.name, color: category.color, icon: category.icon, is_secret: category.is_secret, folder_id: category.folder_id, description: category.description ?? "" })
  }
  const handleDragStart = (event: DragEvent<HTMLElement>, category: LabelCategory) => {
    if (!categoryCanEdit(category) || reorder.isPending) {
      event.preventDefault()
      return
    }
    clearDragPreview()
    const folder = folders.find((item) => item.id === category.folder_id)
    const preview = createLabelDragPreview(category, folder?.name ?? "Без папки")
    dragPreviewRef.current = preview
    event.dataTransfer.effectAllowed = "move"
    event.dataTransfer.setData("text/plain", String(category.id))
    event.dataTransfer.setDragImage(preview, 18, 22)
    setDragId(category.id)
    setDropTarget(null)
  }
  const handleDragEnd = () => clearDragState()
  const handleDragLeave = (event: DragEvent<HTMLElement>) => {
    const related = event.relatedTarget as Node | null
    if (!related || !event.currentTarget.contains(related)) setDropTarget(null)
  }
  const handleGroupDragOver = (event: DragEvent<HTMLElement>, folderId: number | null) => {
    if (dragId === null || reorder.isPending) return
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"
    setDropTarget({ folderId, position: "after" })
  }
  const handleRowDragOver = (event: DragEvent<HTMLElement>, folderId: number | null, categoryId: number) => {
    if (dragId === null || dragId === categoryId || reorder.isPending) return
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"
    const bounds = event.currentTarget.getBoundingClientRect()
    const position: LabelDropPosition = event.clientY < bounds.top + bounds.height / 2 ? "before" : "after"
    setDropTarget({ folderId, categoryId, position })
  }
  const handleDrop = (event: DragEvent<HTMLElement>, targetFolderId: number | null, targetId?: number) => {
    event.preventDefault()
    if (dragId === null || dragId === targetId || reorder.isPending) return
    const group = groupLabelCategories(categories, folders, true).find((item) => item.folderId === targetFolderId)
    if (!group) return
    const position = targetId === undefined ? "after" : dropTarget?.position ?? "after"
    const targetIndex = getLabelDropIndex(group.categories, dragId, targetId, position)
    if (targetIndex < 0) return
    const placement = moveLabelCategoryToGroup(categories, folders, dragId, targetFolderId, targetIndex)
    clearDragState()
    reorder.mutate(placement)
  }
  const toggleFolder = (folderId: number | null) => {
    const key = folderId === null ? "none" : String(folderId)
    setExpandedFolders((current) => ({ ...current, [key]: !(current[key] ?? true) }))
  }
  const isFolderExpanded = (folderId: number | null) => expandedFolders[folderId === null ? "none" : String(folderId)] ?? true
  const categoryCanEdit = (category: LabelCategory) => category.is_secret ? canSecret : canEditPublic
  const categoryCanDelete = (category: LabelCategory) => category.is_secret ? canSecret : canDeletePublic

  return <div className="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-3"><div><h1 className="text-2xl font-semibold">Метки игроков</h1><p className="text-sm text-muted-foreground">Перетаскивайте метку в другую папку или на нужное место внутри группы.</p></div><Input className="w-64" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск меток и папок" /></div>

    {canCreate && <Card><CardHeader><CardTitle className="text-base">Новая метка</CardTitle></CardHeader><CardContent className="grid gap-3 md:grid-cols-[minmax(12rem,1fr)_5rem_5rem_12rem_minmax(12rem,1fr)_auto] md:items-end">
      <Field label="Название"><Input value={form.name} onChange={(e) => setForm((v) => ({ ...v, name: e.target.value }))} /></Field><Field label="Иконка"><Input value={form.icon} onChange={(e) => setForm((v) => ({ ...v, icon: e.target.value }))} maxLength={8} className="text-center text-lg" /></Field><Field label="Цвет"><Input type="color" value={form.color} onChange={(e) => setForm((v) => ({ ...v, color: e.target.value }))} className="w-full p-1" /></Field><Field label="Папка"><FolderSelect value={form.folder_id} folders={folders} onChange={(folder_id) => setForm((v) => ({ ...v, folder_id }))} /></Field><Field label="Описание"><Input value={form.description ?? ""} onChange={(e) => setForm((v) => ({ ...v, description: e.target.value }))} maxLength={500} /></Field><div className="flex items-center gap-3">{canSecret && <SecretSwitch checked={form.is_secret} onCheckedChange={(is_secret) => setForm((v) => ({ ...v, is_secret }))} />}<Button disabled={!form.name.trim() || create.isPending} onClick={() => create.mutate()}><Plus className="size-4" />Создать</Button></div>
    </CardContent></Card>}

    {canOrganize && <Card><CardHeader><CardTitle className="text-base">Папки</CardTitle></CardHeader><CardContent className="space-y-2"><div className="flex max-w-xl gap-2"><Input value={folderName} onChange={(e) => setFolderName(e.target.value)} placeholder="Название новой папки" /><Button disabled={!folderName.trim() || createFolder.isPending} onClick={() => createFolder.mutate()}><FolderPlus className="size-4" />Создать</Button></div><div className="flex flex-wrap gap-2">{folders.map((folder) => editingFolderId === folder.id ? <div key={folder.id} className="flex items-center gap-1"><Input className="h-8 w-40" value={editingFolderName} onChange={(e) => setEditingFolderName(e.target.value)} /><Button size="sm" onClick={() => updateFolder.mutate({ id: folder.id, name: editingFolderName.trim() })} disabled={!editingFolderName.trim()}><Check className="size-3.5" /></Button><Button size="sm" variant="outline" onClick={() => setEditingFolderId(null)}><X className="size-3.5" /></Button></div> : <Badge key={folder.id} variant="secondary" className="gap-1.5 px-2.5 py-1"><Folder className="size-3.5" />{folder.name}<button className="ml-1 opacity-70 hover:opacity-100" onClick={() => { setEditingFolderId(folder.id); setEditingFolderName(folder.name) }} aria-label={`Изменить папку ${folder.name}`}><Pencil className="size-3" /></button><button className="opacity-70 hover:opacity-100" onClick={() => window.confirm(`Удалить папку «${folder.name}»? Метки останутся без папки.`) && removeFolder.mutate(folder.id)} aria-label={`Удалить папку ${folder.name}`}><Trash2 className="size-3" /></button></Badge>)}{!folders.length && <span className="text-sm text-muted-foreground">Папок пока нет.</span>}</div></CardContent></Card>}

    <Card><CardContent className="pt-4"><Table><TableHeader><TableRow><TableHead className="w-20">Порядок</TableHead><TableHead>Метка</TableHead><TableHead>Папка</TableHead><TableHead>Описание</TableHead><TableHead>Доступ</TableHead><TableHead className="text-right">Действия</TableHead></TableRow></TableHeader><TableBody>
      {categoryGroups.flatMap((group) => {
        const expanded = isFolderExpanded(group.folderId)
        const isGroupDropTarget = dropTarget?.folderId === group.folderId
        const groupRow = <TableRow key={`group-${group.folderId ?? "none"}`} className={`bg-muted/30 transition-colors ${isGroupDropTarget ? "bg-primary/10 ring-1 ring-inset ring-primary/60" : ""}`} onDragOver={(event) => handleGroupDragOver(event, group.folderId)} onDragLeave={handleDragLeave} onDrop={(event) => handleDrop(event, group.folderId)}>
          <TableCell colSpan={6} className="p-0"><button type="button" className="flex w-full items-center gap-2 px-2 py-2 text-left text-sm font-medium" onClick={() => toggleFolder(group.folderId)}><ChevronDown className={`size-4 transition-transform ${expanded ? "" : "-rotate-90"}`} /><Folder className="size-4 text-muted-foreground" />{group.label}<span className="text-xs font-normal text-muted-foreground">({group.categories.length})</span>{isGroupDropTarget && dragId !== null && <span className="ml-auto text-xs font-normal text-muted-foreground">Отпустите сюда</span>}</button></TableCell>
        </TableRow>;
        if (!expanded) return [groupRow]
        return [groupRow, ...group.categories.map((category) => {
          const folder = folders.find((item) => item.id === category.folder_id)
          const editing = editingId === category.id
          const canDrag = !editing && categoryCanEdit(category) && !reorder.isPending
          const rowDropPosition = dropTarget?.folderId === group.folderId && dropTarget.categoryId === category.id ? dropTarget.position : null
          const rowClassName = [dragId === category.id ? "opacity-50" : "", rowDropPosition === "before" ? "border-t-2 border-primary" : "", rowDropPosition === "after" ? "border-b-2 border-primary" : ""].filter(Boolean).join(" ")
          return <TableRow key={category.id} onDragOver={(event) => handleRowDragOver(event, group.folderId, category.id)} onDragLeave={handleDragLeave} onDrop={(event) => handleDrop(event, group.folderId, category.id)} className={rowClassName}>
            <TableCell><div className="flex items-center gap-1 text-sm text-muted-foreground"><span className="w-5 text-right tabular-nums">{category.sort_order + 1}</span>{canDrag && <span draggable onDragStart={(event) => handleDragStart(event, category)} onDragEnd={handleDragEnd} className="cursor-grab active:cursor-grabbing" title="Перетащить метку"><GripVertical className="size-4" aria-label="Перетащить" /></span>}</div></TableCell>
            {editing ? <><TableCell><div className="flex min-w-64 flex-wrap gap-2"><Input className="w-14 text-center text-lg" value={editDraft.icon} onChange={(e) => setEditDraft((v) => ({ ...v, icon: e.target.value }))} maxLength={8} /><Input className="w-44" value={editDraft.name} onChange={(e) => setEditDraft((v) => ({ ...v, name: e.target.value }))} /><Input type="color" value={editDraft.color} onChange={(e) => setEditDraft((v) => ({ ...v, color: e.target.value }))} className="w-14 p-1" /></div></TableCell><TableCell><FolderSelect value={editDraft.folder_id} folders={folders} onChange={(folder_id) => setEditDraft((v) => ({ ...v, folder_id }))} /></TableCell><TableCell><Input value={editDraft.description ?? ""} onChange={(e) => setEditDraft((v) => ({ ...v, description: e.target.value }))} maxLength={500} /></TableCell><TableCell>{canSecret && <SecretSwitch checked={editDraft.is_secret} onCheckedChange={(is_secret) => setEditDraft((v) => ({ ...v, is_secret }))} />}</TableCell><TableCell className="text-right"><div className="flex justify-end gap-1.5"><Button size="sm" disabled={!editDraft.name.trim() || update.isPending} onClick={() => update.mutate({ id: category.id, ...editDraft })}><Check className="size-3.5" />Сохранить</Button><Button size="sm" variant="outline" onClick={() => setEditingId(null)}><X className="size-3.5" />Отмена</Button></div></TableCell></> : <><TableCell><Badge style={{ backgroundColor: category.color }} className="gap-1.5 text-white"><span aria-hidden="true">{category.icon || "🏷"}</span>{category.name}</Badge></TableCell><TableCell>{folder ? <span className="flex items-center gap-1 text-sm"><Folder className="size-3.5 text-muted-foreground" />{folder.name}</span> : <span className="text-sm text-muted-foreground">Без папки</span>}</TableCell><TableCell className="max-w-64 truncate text-sm text-muted-foreground" title={category.description ?? undefined}>{category.description || "—"}</TableCell><TableCell>{category.is_secret ? <Badge variant="outline" className="gap-1"><Lock className="size-3" />Секретная</Badge> : <span className="text-sm text-muted-foreground">Обычная</span>}</TableCell><TableCell className="text-right"><div className="flex justify-end gap-1.5">{categoryCanEdit(category) && <Button size="sm" variant="outline" onClick={() => startEdit(category)}><Pencil className="size-3.5" />Изменить</Button>}{categoryCanDelete(category) && <Button size="sm" variant="destructive" disabled={remove.isPending} onClick={() => window.confirm(`Удалить метку «${category.name}»? Все её назначения будут сняты.`) && remove.mutate(category.id)}><Trash2 className="size-3.5" />Удалить</Button>}</div></TableCell></>}
          </TableRow>
        })]
      })}
      {isLoading && <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground">Загрузка меток…</TableCell></TableRow>}{isError && <TableRow><TableCell colSpan={6}><div className="flex items-center justify-center gap-2 py-4 text-destructive"><AlertTriangle className="size-4" /><span>{error instanceof Error ? error.message : "Не удалось загрузить метки"}</span><Button size="sm" variant="outline" onClick={() => refetch()}><RefreshCw className="size-3.5" />Повторить</Button></div></TableCell></TableRow>}{!isLoading && !isError && !visibleCategories.length && <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground">Меток нет</TableCell></TableRow>}
    </TableBody></Table></CardContent></Card>
  </div>
}

function createLabelDragPreview(category: LabelCategory, folderName: string): HTMLDivElement {
  const preview = document.createElement("div")
  preview.className = "pointer-events-none fixed left-[-10000px] top-0 z-[9999] flex min-w-[220px] max-w-[320px] items-center gap-2 rounded-lg border border-primary/70 bg-card px-3 py-2 text-sm text-card-foreground shadow-2xl"
  preview.setAttribute("aria-hidden", "true")

  const icon = document.createElement("span")
  icon.className = "flex size-7 shrink-0 items-center justify-center rounded-md text-base text-white"
  icon.style.backgroundColor = category.color
  icon.textContent = category.icon || "🏷"

  const text = document.createElement("div")
  text.className = "min-w-0"
  const name = document.createElement("div")
  name.className = "truncate font-medium"
  name.textContent = category.name
  const folder = document.createElement("div")
  folder.className = "truncate text-xs text-muted-foreground"
  folder.textContent = folderName
  text.append(name, folder)
  preview.append(icon, text)
  document.body.appendChild(preview)
  return preview
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <div><label className="mb-1 block text-xs text-muted-foreground">{label}</label>{children}</div> }
function FolderSelect({ value, folders, onChange }: { value: number | null; folders: LabelFolder[]; onChange: (value: number | null) => void }) { return <Select value={value === null ? "none" : String(value)} onValueChange={(next) => onChange(next === "none" ? null : Number(next))}><SelectTrigger><SelectValue placeholder="Без папки" /></SelectTrigger><SelectContent><SelectItem value="none">Без папки</SelectItem>{folders.map((folder) => <SelectItem key={folder.id} value={String(folder.id)}>{folder.name}</SelectItem>)}</SelectContent></Select> }
function SecretSwitch({ checked, onCheckedChange }: { checked: boolean; onCheckedChange: (checked: boolean) => void }) { return <label className="flex items-center gap-2 text-xs text-muted-foreground"><Switch checked={checked} onCheckedChange={onCheckedChange} size="sm" /><span className="flex items-center gap-1"><Lock className="size-3" />Секретная</span></label> }
