// @ts-nocheck - документы выведены из эксплуатации, оригинальный код оставлен как мёртвый для быстрого возврата
import { useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  NotebookText, Plus, FolderPlus, Folder, FileText, Star, Users, Trash2,
  Globe2, MoreHorizontal, Copy, FolderInput, RotateCcw, XCircle,
  Building2, ChevronDown, ChevronRight, LayoutGrid, List as ListIcon, Settings2,
} from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator,
  DropdownMenuSub, DropdownMenuSubTrigger, DropdownMenuSubContent, DropdownMenuPortal,
} from "@/components/ui/dropdown-menu"
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription,
  AlertDialogFooter, AlertDialogCancel, AlertDialogAction,
} from "@/components/ui/alert-dialog"
import { canEdit, useCurrentAdmin, type MyDepartmentRef } from "@/lib/auth"
import { documentsApi, type DocScope, type DocumentRow, type DocFolder } from "@/pages/documents/api"
import { TemplatePickerDialog } from "@/pages/documents/TemplatePickerDialog"
import { departmentsApi, type DeptDocumentFolder } from "@/pages/departments/api"
import { FolderAccessFields } from "@/pages/departments/FolderAccessFields"
import { PlatformIcon } from "@/components/layout/PlatformIcon"

const SCOPES: { key: DocScope; label: string; icon: typeof NotebookText }[] = [
  { key: "mine", label: "Мои документы", icon: FileText },
  { key: "shared", label: "Доступные мне", icon: Users },
  { key: "starred", label: "Избранное", icon: Star },
  { key: "trash", label: "Корзина", icon: Trash2 },
]

function FolderNode({ folder, folders, depth, activeId, onSelect, onDelete }: {
  folder: DocFolder; folders: DocFolder[]; depth: number
  activeId: number | null; onSelect: (id: number) => void; onDelete: (id: number) => void
}) {
  const children = folders.filter((f) => f.parent_id === folder.id)
  return (
    <>
      <div
        className={`group flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm hover:bg-accent ${activeId === folder.id ? "bg-accent font-medium" : ""}`}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        <button className="flex flex-1 items-center gap-1.5 truncate text-left" onClick={() => onSelect(folder.id)}>
          <Folder className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{folder.name}</span>
        </button>
        <button className="hidden text-muted-foreground hover:text-destructive group-hover:block" onClick={() => onDelete(folder.id)}>
          <XCircle className="size-3.5" />
        </button>
      </div>
      {children.map((c) => (
        <FolderNode key={c.id} folder={c} folders={folders} depth={depth + 1} activeId={activeId} onSelect={onSelect} onDelete={onDelete} />
      ))}
    </>
  )
}

function DeptFolderNode({ folder, folders, depth, activeId, canManage, onSelect, onDelete, onSettings }: {
  folder: DeptDocumentFolder; folders: DeptDocumentFolder[]; depth: number
  activeId: number | null; canManage: boolean; onSelect: (id: number) => void; onDelete: (id: number) => void
  onSettings: (folder: DeptDocumentFolder) => void
}) {
  const children = folders.filter((f) => f.parent_id === folder.id)
  return (
    <>
      <div
        className={`group flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm hover:bg-accent ${activeId === folder.id ? "bg-accent font-medium" : ""}`}
        style={{ paddingLeft: `${28 + depth * 14}px` }}
      >
        <button className="flex flex-1 items-center gap-1.5 truncate text-left" onClick={() => onSelect(folder.id)}>
          <Folder className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{folder.name}</span>
        </button>
        {canManage && (
          <>
            <button className="hidden text-muted-foreground hover:text-foreground group-hover:block" onClick={() => onSettings(folder)} title="Настройки папки">
              <Settings2 className="size-3.5" />
            </button>
            <button className="hidden text-muted-foreground hover:text-destructive group-hover:block" onClick={() => onDelete(folder.id)}>
              <XCircle className="size-3.5" />
            </button>
          </>
        )}
      </div>
      {children.map((c) => (
        <DeptFolderNode key={c.id} folder={c} folders={folders} depth={depth + 1} activeId={activeId} canManage={canManage} onSelect={onSelect} onDelete={onDelete} onSettings={onSettings} />
      ))}
    </>
  )
}

/** One "Отделы" sidebar entry (item 7) — expanding a department fetches (at
 * the page level) its DepartmentDocumentFolder tree; selecting the
 * department itself or one of its folders switches the main grid into
 * department-directory mode. Only the "documentation" module is organized
 * into folders — orders/news keep their own department-tab views. */
function DeptDirectoryNode({ dept, expanded, activeFolderId, folders, canManage, onHeaderClick, onSelectFolder, onDeleteFolder, onSettingsFolder }: {
  dept: MyDepartmentRef
  expanded: boolean
  activeFolderId: number | null
  folders: DeptDocumentFolder[]
  canManage: boolean
  onHeaderClick: () => void
  onSelectFolder: (folderId: number | null) => void
  onDeleteFolder: (folderId: number) => void
  onSettingsFolder: (folder: DeptDocumentFolder) => void
}) {
  const rootFolders = folders.filter((f) => f.parent_id === null)
  return (
    <div>
      <button
        onClick={onHeaderClick}
        className={`flex w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm hover:bg-accent ${expanded && activeFolderId === null ? "bg-accent font-medium" : ""}`}
        style={{ borderLeft: `2px solid ${dept.color || "transparent"}` }}
      >
        {expanded ? <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />}
        <span className="shrink-0 leading-none"><PlatformIcon value={dept.icon || "🏢"} fallback={Building2} /></span>
        <span className="truncate">{dept.name}</span>
      </button>
      {expanded && (
        <div>
          <button
            onClick={() => onSelectFolder(null)}
            className={`flex w-full items-center gap-1.5 truncate rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-accent ${activeFolderId === null ? "bg-accent font-medium" : "text-muted-foreground"}`}
            style={{ paddingLeft: "28px" }}
          >
            <Folder className="size-3.5 shrink-0" /> Корень
          </button>
          {rootFolders.map((f) => (
            <DeptFolderNode key={f.id} folder={f} folders={folders} depth={1} activeId={activeFolderId} canManage={canManage} onSelect={onSelectFolder} onDelete={onDeleteFolder} onSettings={onSettingsFolder} />
          ))}
        </div>
      )}
    </div>
  )
}

function deptFolderPath(folders: DeptDocumentFolder[], folderId: number | null): DeptDocumentFolder[] {
  const path: DeptDocumentFolder[] = []
  let current = folderId != null ? folders.find((f) => f.id === folderId) : undefined
  while (current) {
    path.unshift(current)
    const parentId: number | null = current.parent_id
    current = parentId != null ? folders.find((f) => f.id === parentId) : undefined
  }
  return path
}

function initials(name: string | null | undefined) {
  return (name ?? "?").slice(0, 2).toUpperCase()
}

function DocsDisabledCard() {
  return (
    <Card className="py-0">
      <CardContent className="py-16 text-center">
        <p className="text-lg font-semibold">Упс... Похоже администратор выключил этот кусок кода</p>
        <p className="mt-2 text-sm text-muted-foreground">Документы временно выведены из эксплуатации.</p>
      </CardContent>
    </Card>
  )
}

export function DocumentsPage() {
  // Документы выведены из эксплуатации — показываем заглушку вместо всего функционала
  return (
    <div>
      <PageHeader title="Документы" description="Общая база документов с совместным доступом и историей версий" icon={NotebookText} />
      <DocsDisabledCard />
    </div>
  )
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()

  const [scope, setScope] = useState<DocScope>("mine")
  const [folderId, setFolderId] = useState<number | null>(null)
  const [search, setSearch] = useState("")
  const [newFolderOpen, setNewFolderOpen] = useState(false)
  const [newFolderName, setNewFolderName] = useState("")
  const [deleteFolderId, setDeleteFolderId] = useState<number | null>(null)
  const [deleteDeptFolderId, setDeleteDeptFolderId] = useState<number | null>(null)
  const [permanentDeleteId, setPermanentDeleteId] = useState<number | null>(null)
  const [templatePickerOpen, setTemplatePickerOpen] = useState(false)
  const [droppedFile, setDroppedFile] = useState<File | null>(null)
  const [isDraggingOver, setIsDraggingOver] = useState(false)
  const dragCounter = useRef(0)
  const [viewMode, setViewMode] = useState<"grid" | "list">(() => (localStorage.getItem("nd-documents-view") === "list" ? "list" : "grid"))
  const setAndPersistViewMode = (mode: "grid" | "list") => {
    setViewMode(mode)
    localStorage.setItem("nd-documents-view", mode)
  }

  // Item 7 — "Отделы" directory: selecting a department (or a folder inside
  // it) switches the main grid from the personal scope/folder view into
  // that department's auto-generated documentation directory.
  const [deptId, setDeptId] = useState<number | null>(null)
  const [deptFolderId, setDeptFolderId] = useState<number | null>(null)
  const inDeptView = deptId !== null

  const editAllowed = canEdit(admin, "documents")
  const myDepartments = admin?.my_departments ?? []
  // Card badges (item 6) need every department's name/color, not just ones
  // the current admin belongs to (e.g. scope="all" superuser view, or a
  // shared document from someone else's department) — my_departments wins
  // on overlap since it's already loaded and identical for shared fields.
  const { data: allDeptsData } = useQuery({ queryKey: ["departments-lite"], queryFn: () => departmentsApi.list() })
  const deptById: Record<number, { name: string; color: string | null }> = Object.fromEntries([
    ...(allDeptsData?.departments ?? []).map((d) => [d.id, { name: d.name, color: d.color }]),
    ...myDepartments.map((d) => [d.id, { name: d.name, color: d.color }]),
  ])

  const { data: foldersData } = useQuery({
    queryKey: ["document-folders"],
    queryFn: () => documentsApi.folders.list(),
  })
  const folders = foldersData?.folders ?? []
  const rootFolders = folders.filter((f) => f.parent_id === null)

  const { data: deptFoldersData } = useQuery({
    queryKey: ["dept-document-folders", deptId],
    queryFn: () => departmentsApi.documentFolders.list(deptId!),
    enabled: deptId != null,
  })
  const deptFolders = deptFoldersData?.folders ?? []

  const { data: activeDeptDetail } = useQuery({
    queryKey: ["department-my-access", deptId],
    queryFn: () => departmentsApi.get(deptId!),
    enabled: deptId != null,
  })
  const deptCanEdit = !!activeDeptDetail?.my_access.documentation?.edit

  const { data, isLoading } = useQuery({
    queryKey: inDeptView
      ? ["dept-documents", deptId, deptFolderId]
      : ["documents", scope, scope === "mine" ? folderId : null, search],
    queryFn: () => (inDeptView
      ? documentsApi.byDepartment(deptId!, { kind: "documentation", folderId: deptFolderId ?? undefined })
      : documentsApi.list({ scope, folder_id: scope === "mine" ? folderId : undefined, search: search || undefined })),
  })
  const documents = data?.documents ?? []
  const invalidateDocs = () => {
    queryClient.invalidateQueries({ queryKey: ["documents"] })
    queryClient.invalidateQueries({ queryKey: ["dept-documents"] })
  }

  const selectDeptFolder = (id: number, folderId: number | null) => { setDeptId(id); setDeptFolderId(folderId) }
  const toggleDept = (id: number) => {
    setDeptId((cur) => (cur === id ? null : id))
    setDeptFolderId(null)
  }

  const createDocMutation = useMutation({
    mutationFn: (templateId?: number) => (inDeptView
      ? documentsApi.create({ department_id: deptId!, department_folder_id: deptFolderId ?? undefined, template_id: templateId })
      : documentsApi.create({ folder_id: scope === "mine" ? folderId : null, template_id: templateId })),
    onSuccess: (res) => {
      setTemplatePickerOpen(false)
      navigate(`/documents/${res.id}`, inDeptView ? { state: { justCreated: true } } : undefined)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const createFolderMutation = useMutation({
    mutationFn: async () => {
      if (inDeptView) await departmentsApi.documentFolders.create(deptId!, { name: newFolderName.trim(), parent_id: deptFolderId })
      else await documentsApi.folders.create({ name: newFolderName.trim(), parent_id: folderId })
    },
    onSuccess: () => {
      toast.success("Папка создана")
      setNewFolderOpen(false)
      setNewFolderName("")
      if (inDeptView) queryClient.invalidateQueries({ queryKey: ["dept-document-folders", deptId] })
      else queryClient.invalidateQueries({ queryKey: ["document-folders"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteFolderMutation = useMutation({
    mutationFn: (id: number) => documentsApi.folders.delete(id),
    onSuccess: () => {
      toast.success("Папка удалена")
      setDeleteFolderId(null)
      if (folderId === deleteFolderId) setFolderId(null)
      queryClient.invalidateQueries({ queryKey: ["document-folders"] })
      invalidateDocs()
    },
  })

  const deleteDeptFolderMutation = useMutation({
    mutationFn: (id: number) => departmentsApi.documentFolders.delete(deptId!, id),
    onSuccess: () => {
      toast.success("Папка удалена")
      setDeleteDeptFolderId(null)
      if (deptFolderId === deleteDeptFolderId) setDeptFolderId(null)
      queryClient.invalidateQueries({ queryKey: ["dept-document-folders", deptId] })
      invalidateDocs()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const [folderSettingsTarget, setFolderSettingsTarget] = useState<DeptDocumentFolder | null>(null)
  const [folderSettingsChannelId, setFolderSettingsChannelId] = useState("")
  const [folderSettingsAutoApply, setFolderSettingsAutoApply] = useState(false)
  const openFolderSettings = (folder: DeptDocumentFolder) => {
    setFolderSettingsTarget(folder)
    setFolderSettingsChannelId(folder.discord_channel_id ?? "")
    setFolderSettingsAutoApply(folder.auto_apply_permissions)
  }
  const saveFolderSettingsMutation = useMutation({
    mutationFn: () => departmentsApi.documentFolders.rename(deptId!, folderSettingsTarget!.id, {
      discord_channel_id: folderSettingsChannelId.trim(),
      auto_apply_permissions: folderSettingsAutoApply,
    }),
    onSuccess: () => {
      toast.success("Настройки папки сохранены")
      setFolderSettingsTarget(null)
      queryClient.invalidateQueries({ queryKey: ["dept-document-folders", deptId] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const starMutation = useMutation({
    mutationFn: ({ id, next }: { id: number; next: boolean }) => (next ? documentsApi.star(id) : documentsApi.unstar(id)),
    onSuccess: invalidateDocs,
  })
  const duplicateMutation = useMutation({
    mutationFn: (id: number) => documentsApi.duplicate(id),
    onSuccess: (res) => { toast.success("Копия создана"); navigate(`/documents/${res.id}`) },
  })
  const trashMutation = useMutation({
    mutationFn: (id: number) => documentsApi.trash(id),
    onSuccess: () => { toast.success("Перемещено в корзину"); invalidateDocs() },
  })
  const restoreMutation = useMutation({
    mutationFn: (id: number) => documentsApi.restore(id),
    onSuccess: () => { toast.success("Документ восстановлен"); invalidateDocs() },
  })
  const permanentDeleteMutation = useMutation({
    mutationFn: (id: number) => documentsApi.permanentDelete(id),
    onSuccess: () => { toast.success("Документ удалён навсегда"); setPermanentDeleteId(null); invalidateDocs() },
  })
  const moveMutation = useMutation({
    mutationFn: ({ id, folder_id }: { id: number; folder_id: number | null }) => documentsApi.update(id, { folder_id }),
    onSuccess: () => { toast.success("Документ перемещён"); invalidateDocs() },
  })

  function renderDocActions(doc: DocumentRow) {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="icon-xs" variant="ghost"><MoreHorizontal className="size-3.5" /></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {!inDeptView && scope === "trash" ? (
            <>
              <DropdownMenuItem onClick={() => restoreMutation.mutate(doc.id)}><RotateCcw className="mr-1 size-4" /> Восстановить</DropdownMenuItem>
              {doc.is_mine && (
                <DropdownMenuItem variant="destructive" onClick={() => setPermanentDeleteId(doc.id)}><Trash2 className="mr-1 size-4" /> Удалить навсегда</DropdownMenuItem>
              )}
            </>
          ) : (
            <>
              <DropdownMenuItem onClick={() => duplicateMutation.mutate(doc.id)}><Copy className="mr-1 size-4" /> Дублировать</DropdownMenuItem>
              {!inDeptView && doc.is_mine && folders.length > 0 && (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger><FolderInput className="mr-1 size-4" /> Переместить в папку</DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent>
                      <DropdownMenuItem onClick={() => moveMutation.mutate({ id: doc.id, folder_id: null })}>Корень</DropdownMenuItem>
                      {folders.map((f) => (
                        <DropdownMenuItem key={f.id} onClick={() => moveMutation.mutate({ id: doc.id, folder_id: f.id })}>{f.name}</DropdownMenuItem>
                      ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuPortal>
                </DropdownMenuSub>
              )}
              {doc.is_mine && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onClick={() => trashMutation.mutate(doc.id)}><Trash2 className="mr-1 size-4" /> В корзину</DropdownMenuItem>
                </>
              )}
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    )
  }

  const canUploadHere = inDeptView ? deptCanEdit : editAllowed

  function handleDragEnter(e: React.DragEvent) {
    if (!canUploadHere || !e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
    dragCounter.current += 1
    setIsDraggingOver(true)
  }
  function handleDragOver(e: React.DragEvent) {
    if (!canUploadHere || !e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
  }
  function handleDragLeave() {
    dragCounter.current = Math.max(0, dragCounter.current - 1)
    if (dragCounter.current === 0) setIsDraggingOver(false)
  }
  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    dragCounter.current = 0
    setIsDraggingOver(false)
    const file = e.dataTransfer.files?.[0]
    if (!canUploadHere || !file) return
    setDroppedFile(file)
    setTemplatePickerOpen(true)
  }

  return (
    <div>
      <PageHeader
        title="Документы"
        description="Общая база документов с совместным доступом и историей версий"
        icon={NotebookText}
        actions={(inDeptView ? deptCanEdit : editAllowed) && (
          <>
            <Button variant="outline" size="sm" onClick={() => setNewFolderOpen(true)}><FolderPlus className="mr-1.5 size-4" /> Папка</Button>
            <Button size="sm" onClick={() => setTemplatePickerOpen(true)} disabled={createDocMutation.isPending}>
              <Plus className="mr-1.5 size-4" /> Новый документ
            </Button>
          </>
        )}
      />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[220px_1fr]">
        <aside className="space-y-3 lg:max-w-[220px]">
          <div className="space-y-0.5">
            {SCOPES.map((s) => (
              <button
                key={s.key}
                onClick={() => { setScope(s.key); setFolderId(null); setDeptId(null) }}
                className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors hover:bg-accent ${!inDeptView && scope === s.key ? "bg-accent font-medium" : "text-muted-foreground"}`}
              >
                <s.icon className="size-4" /> {s.label}
              </button>
            ))}
            {admin?.is_superuser && (
              <button
                onClick={() => { setScope("all"); setFolderId(null); setDeptId(null) }}
                className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors hover:bg-accent ${!inDeptView && scope === "all" ? "bg-accent font-medium" : "text-muted-foreground"}`}
              >
                <Globe2 className="size-4" /> Все документы
              </button>
            )}
          </div>

          {scope === "mine" && !inDeptView && (
            <div>
              <div className="mb-1 flex items-center justify-between px-2.5 text-xs font-semibold tracking-wide text-muted-foreground">
                ПАПКИ
              </div>
              <button
                onClick={() => setFolderId(null)}
                className={`flex w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm hover:bg-accent ${folderId === null ? "bg-accent font-medium" : "text-muted-foreground"}`}
              >
                <Folder className="size-3.5" /> Корень
              </button>
              {rootFolders.map((f) => (
                <FolderNode key={f.id} folder={f} folders={folders} depth={1} activeId={folderId} onSelect={setFolderId} onDelete={setDeleteFolderId} />
              ))}
            </div>
          )}

          {myDepartments.length > 0 && (
            <div>
              <div className="mb-1 mt-3 flex items-center justify-between px-2.5 text-xs font-semibold tracking-wide text-muted-foreground">
                ОТДЕЛЫ
              </div>
              {myDepartments.map((d) => (
                <DeptDirectoryNode
                  key={d.id}
                  dept={d}
                  expanded={deptId === d.id}
                  activeFolderId={deptId === d.id ? deptFolderId : null}
                  folders={deptId === d.id ? deptFolders : []}
                  canManage={deptId === d.id && deptCanEdit}
                  onHeaderClick={() => toggleDept(d.id)}
                  onSelectFolder={(fid) => selectDeptFolder(d.id, fid)}
                  onDeleteFolder={setDeleteDeptFolderId}
                  onSettingsFolder={openFolderSettings}
                />
              ))}
            </div>
          )}
        </aside>

        <main
          className="relative min-w-0"
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {isDraggingOver && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg border-2 border-dashed border-primary bg-primary/10">
              <span className="rounded-md bg-background px-3 py-1.5 text-sm font-medium shadow">Отпустите файл, чтобы загрузить</span>
            </div>
          )}
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            {inDeptView ? (
              <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Building2 className="size-4" />
                <button className="font-medium text-foreground hover:underline" onClick={() => setDeptFolderId(null)}>
                  {deptById[deptId!]?.name}
                </button>
                {deptFolderPath(deptFolders, deptFolderId).map((f) => (
                  <span key={f.id} className="flex items-center gap-1.5">
                    <span>/</span>
                    <button className="hover:underline" onClick={() => setDeptFolderId(f.id)}>{f.name}</button>
                  </span>
                ))}
              </div>
            ) : (
              <SearchInput placeholder="Поиск по названию и содержимому…" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-sm" />
            )}
            <div className="flex items-center gap-0.5 rounded-md border p-0.5">
              <Button size="icon-xs" variant={viewMode === "grid" ? "secondary" : "ghost"} onClick={() => setAndPersistViewMode("grid")} title="Плитка">
                <LayoutGrid className="size-3.5" />
              </Button>
              <Button size="icon-xs" variant={viewMode === "list" ? "secondary" : "ghost"} onClick={() => setAndPersistViewMode("list")} title="Список">
                <ListIcon className="size-3.5" />
              </Button>
            </div>
          </div>

          {isLoading ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-28" />)}
            </div>
          ) : documents.length === 0 ? (
            <Card className="py-0"><CardContent className="py-16 text-center text-muted-foreground">
              {inDeptView ? "В этой папке пока нет документов" : scope === "trash" ? "Корзина пуста" : scope === "starred" ? "Нет избранных документов" : scope === "shared" ? "Пока нет документов, доступных вам" : "Здесь пока нет документов"}
            </CardContent></Card>
          ) : viewMode === "list" ? (
            <Card className="overflow-hidden py-0">
              {documents.map((doc: DocumentRow, i) => (
                <div
                  key={doc.id}
                  className={`group flex items-center gap-3 px-3 py-2 transition-colors hover:bg-accent/50 ${i > 0 ? "border-t" : ""}`}
                  style={doc.department_id && deptById[doc.department_id]?.color ? { borderLeft: `3px solid ${deptById[doc.department_id]!.color}` } : undefined}
                >
                  <Link to={`/documents/${doc.id}`} className="flex min-w-0 flex-1 items-center gap-3">
                    <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-sm">
                      <PlatformIcon value={doc.icon} fallback={FileText} className="size-3.5 text-primary" />
                    </div>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{doc.title}</span>
                    {doc.department_id && !inDeptView && (
                      <Badge variant="outline" className="shrink-0 gap-1 text-[10px]">
                        <Building2 className="size-2.5" /> {deptById[doc.department_id]?.name ?? "Отдел"}
                      </Badge>
                    )}
                    {doc.access_level && doc.access_level !== "owner" && (
                      <Badge variant="secondary" className="shrink-0 text-[10px]">{doc.access_level === "edit" ? "редактор" : doc.access_level === "comment" ? "комментатор" : "просмотр"}</Badge>
                    )}
                    {doc.is_mine && doc.shared_summary && (
                      <Badge variant="outline" className="hidden shrink-0 gap-1 text-[10px] sm:inline-flex" title={doc.shared_summary}>
                        <Users className="size-2.5" /> {doc.shared_summary}
                      </Badge>
                    )}
                    <span className="hidden shrink-0 truncate text-xs text-muted-foreground sm:inline">{doc.owner?.name}</span>
                    <span className="hidden w-32 shrink-0 text-right text-[11px] text-muted-foreground md:inline">
                      {doc.updated_at ? new Date(doc.updated_at).toLocaleDateString("ru-RU") : "-"}
                    </span>
                  </Link>
                  <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    {(inDeptView || scope !== "trash") && (
                      <Button size="icon-xs" variant="ghost" onClick={() => starMutation.mutate({ id: doc.id, next: !doc.is_starred })}>
                        <Star className={`size-3.5 ${doc.is_starred ? "fill-warning text-warning" : ""}`} />
                      </Button>
                    )}
                    {renderDocActions(doc)}
                  </div>
                </div>
              ))}
            </Card>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
              {documents.map((doc: DocumentRow) => (
                <Card
                  key={doc.id}
                  className="group relative overflow-hidden py-0 transition-colors hover:border-primary/40"
                  style={doc.department_id && deptById[doc.department_id]?.color ? { borderLeft: `3px solid ${deptById[doc.department_id]!.color}` } : undefined}
                >
                  <Link to={`/documents/${doc.id}`} className="block">
                    <CardContent className="p-4">
                      <div className="mb-2 flex items-center justify-between">
                        <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-lg">
                          <PlatformIcon value={doc.icon} fallback={FileText} className="size-4 text-primary" />
                        </div>
                        {doc.access_level && doc.access_level !== "owner" && (
                          <Badge variant="secondary" className="text-[10px]">{doc.access_level === "edit" ? "редактор" : doc.access_level === "comment" ? "комментатор" : "просмотр"}</Badge>
                        )}
                      </div>
                      <div className="truncate text-sm font-medium">{doc.title}</div>
                      {doc.department_id && !inDeptView && (
                        <Badge variant="outline" className="mt-1 gap-1 text-[10px]">
                          <Building2 className="size-2.5" /> {deptById[doc.department_id]?.name ?? "Отдел"}
                        </Badge>
                      )}
                      {doc.is_mine && doc.shared_summary && (
                        <Badge variant="outline" className="mt-1 ml-1 gap-1 text-[10px]" title={doc.shared_summary}>
                          <Users className="size-2.5" /> {doc.shared_summary}
                        </Badge>
                      )}
                      <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                        <div className="flex size-4 items-center justify-center rounded-full bg-muted text-[8px] font-semibold">{initials(doc.last_edited_by?.name ?? doc.owner?.name)}</div>
                        <span className="truncate">{doc.owner?.name}</span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">
                        {doc.updated_at ? new Date(doc.updated_at).toLocaleString("ru-RU") : "-"}
                      </div>
                    </CardContent>
                  </Link>

                  <div className="absolute top-2 right-2 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    {(inDeptView || scope !== "trash") && (
                      <Button size="icon-xs" variant="ghost" onClick={() => starMutation.mutate({ id: doc.id, next: !doc.is_starred })}>
                        <Star className={`size-3.5 ${doc.is_starred ? "fill-warning text-warning" : ""}`} />
                      </Button>
                    )}
                    {renderDocActions(doc)}
                  </div>
                </Card>
              ))}
            </div>
          )}
        </main>
      </div>

      <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Новая папка</DialogTitle>
            <DialogDescription>Папки помогают организовать ваши личные документы. Общий доступ настраивается отдельно для каждого документа.</DialogDescription>
          </DialogHeader>
          <Input autoFocus value={newFolderName} onChange={(e) => setNewFolderName(e.target.value)} placeholder="Название папки" onKeyDown={(e) => e.key === "Enter" && newFolderName.trim() && createFolderMutation.mutate()} />
          <DialogFooter>
            <Button variant="outline" onClick={() => setNewFolderOpen(false)}>Отмена</Button>
            <Button disabled={!newFolderName.trim()} onClick={() => createFolderMutation.mutate()}>Создать</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleteFolderId != null} onOpenChange={(o) => !o && setDeleteFolderId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить папку?</AlertDialogTitle>
            <AlertDialogDescription>Документы и вложенные папки внутри неё переместятся на уровень выше. Сами документы не удаляются.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteFolderId != null && deleteFolderMutation.mutate(deleteFolderId)}>Удалить</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={deleteDeptFolderId != null} onOpenChange={(o) => !o && setDeleteDeptFolderId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить папку отдела?</AlertDialogTitle>
            <AlertDialogDescription>Документы и вложенные папки внутри неё переместятся на уровень выше. Сами документы не удаляются.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteDeptFolderId != null && deleteDeptFolderMutation.mutate(deleteDeptFolderId)}>Удалить</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={folderSettingsTarget != null} onOpenChange={(o) => !o && setFolderSettingsTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Настройки папки «{folderSettingsTarget?.name}»</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="mb-1.5 block">ID канала Discord для публикации документов из папки</Label>
              <Input value={folderSettingsChannelId} onChange={(e) => setFolderSettingsChannelId(e.target.value)} placeholder="123456789012345678" />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={folderSettingsAutoApply} onCheckedChange={(c) => setFolderSettingsAutoApply(c === true)} />
              Автоматически применять права папки для всех вложенных документов
            </label>
          </div>
          {folderSettingsTarget && deptId != null && (
            <FolderAccessFields
              open={folderSettingsTarget != null}
              departmentId={deptId}
              folder={folderSettingsTarget}
              accessLevels={activeDeptDetail?.access_levels ?? []}
            />
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setFolderSettingsTarget(null)}>Отмена</Button>
            <Button disabled={saveFolderSettingsMutation.isPending} onClick={() => saveFolderSettingsMutation.mutate()}>Сохранить</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={permanentDeleteId != null} onOpenChange={(o) => !o && setPermanentDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить документ навсегда?</AlertDialogTitle>
            <AlertDialogDescription>Это действие необратимо. Документ, его комментарии и история версий будут удалены безвозвратно.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction onClick={() => permanentDeleteId != null && permanentDeleteMutation.mutate(permanentDeleteId)}>Удалить навсегда</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <TemplatePickerDialog
        open={templatePickerOpen}
        onOpenChange={(o) => { setTemplatePickerOpen(o); if (!o) setDroppedFile(null) }}
        onPick={(templateId) => createDocMutation.mutate(templateId)}
        onImported={(id) => {
          setTemplatePickerOpen(false)
          invalidateDocs()
          navigate(`/documents/${id}`, inDeptView ? { state: { justCreated: true } } : undefined)
        }}
        canManage={editAllowed}
        folderId={!inDeptView && scope === "mine" && folderId != null ? folderId : undefined}
        departmentId={inDeptView ? deptId! : undefined}
        departmentFolderId={inDeptView && deptFolderId != null ? deptFolderId : undefined}
        initialFile={droppedFile}
      />
    </div>
  )
}
