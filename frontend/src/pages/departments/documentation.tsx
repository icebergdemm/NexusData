import { useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { FileText, Plus, NotebookText, Upload, Folder, FolderPlus, Settings2, XCircle, Trash2, ArrowLeft, Tags } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { documentsApi } from "@/pages/documents/api"
import { ImportDocumentDialog } from "@/pages/documents/ImportDocumentDialog"
import { departmentsApi, type DeptDocumentFolder, type DepartmentDetail } from "./api"
import { FolderAccessFields } from "./FolderAccessFields"

function DeptFolderNode({ folder, folders, depth, activeId, canManage, onSelect, onDelete, onPermissions }: {
  folder: DeptDocumentFolder; folders: DeptDocumentFolder[]; depth: number
  activeId: number | null; canManage: boolean
  onSelect: (id: number) => void; onDelete: (id: number) => void; onPermissions: (folder: DeptDocumentFolder) => void
}) {
  const children = folders.filter((f) => f.parent_id === folder.id)
  return (
    <>
      <div
        className={`group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm hover:bg-accent ${activeId === folder.id ? "bg-accent font-medium" : ""}`}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        <button className="flex flex-1 items-center gap-1.5 truncate text-left" onClick={() => onSelect(folder.id)}>
          <Folder className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{folder.name}</span>
        </button>
        {canManage && (
          <div className="hidden items-center gap-0.5 group-hover:flex">
            <button className="text-muted-foreground hover:text-foreground" onClick={() => onPermissions(folder)} title="Права доступа">
              <Settings2 className="size-3.5" />
            </button>
            <button className="text-muted-foreground hover:text-destructive" onClick={() => onDelete(folder.id)} title="Удалить папку">
              <XCircle className="size-3.5" />
            </button>
          </div>
        )}
      </div>
      {children.map((c) => (
        <DeptFolderNode key={c.id} folder={c} folders={folders} depth={depth + 1} activeId={activeId} canManage={canManage} onSelect={onSelect} onDelete={onDelete} onPermissions={onPermissions} />
      ))}
    </>
  )
}

function FolderPermissionsDialog({ open, onOpenChange, department, folder }: {
  open: boolean; onOpenChange: (open: boolean) => void; department: DepartmentDetail; folder: DeptDocumentFolder | null
}) {
  if (!folder) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Доступ к папке «{folder.name}»</DialogTitle>
          <DialogDescription>Дополнительный доступ к содержимому папки поверх общих прав на документацию отдела.</DialogDescription>
        </DialogHeader>

        <FolderAccessFields open={open} departmentId={department.id} folder={folder} accessLevels={department.access_levels} />

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Закрыть</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TagManagerDialog({ open, onOpenChange, department }: {
  open: boolean; onOpenChange: (open: boolean) => void; department: DepartmentDetail
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState("")
  const [color, setColor] = useState("#5A646E")

  const { data } = useQuery({
    queryKey: ["dept-document-tags", department.id],
    queryFn: () => departmentsApi.documentTags.list(department.id),
    enabled: open,
  })

  const createMutation = useMutation({
    mutationFn: () => departmentsApi.documentTags.create(department.id, { name: name.trim(), color }),
    onSuccess: () => {
      toast.success("Тег создан")
      setName("")
      queryClient.invalidateQueries({ queryKey: ["dept-document-tags", department.id] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (tagId: number) => departmentsApi.documentTags.delete(department.id, tagId),
    onSuccess: () => {
      toast.success("Тег удалён")
      queryClient.invalidateQueries({ queryKey: ["dept-document-tags", department.id] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Теги документации</DialogTitle>
          <DialogDescription>Свой набор тегов для этого отдела — доступны для присвоения любому документу в его документации.</DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5">
          {!data?.tags.length && <p className="text-xs text-muted-foreground">Тегов пока нет.</p>}
          {data?.tags.map((t) => (
            <div key={t.id} className="flex items-center justify-between rounded-md border px-2.5 py-1.5 text-sm">
              <Badge style={{ backgroundColor: `${t.color}22`, color: t.color }}>{t.name}</Badge>
              <Button size="icon-xs" variant="ghost" className="text-destructive" onClick={() => deleteMutation.mutate(t.id)}>
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>

        <div className="flex items-end gap-2 border-t pt-3">
          <div className="flex-1">
            <Label className="mb-1.5 block text-xs">Название</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && createMutation.mutate()} />
          </div>
          <div>
            <Label className="mb-1.5 block text-xs">Цвет</Label>
            <Input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="h-9 w-14 p-1" />
          </div>
          <Button size="sm" disabled={!name.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>Добавить</Button>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Закрыть</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function DocumentationTab({ department, canEdit, isSuperuser }: { department: DepartmentDetail; canEdit: boolean; isSuperuser: boolean }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [folderId, setFolderId] = useState<number | null>(null)
  const [newFolderOpen, setNewFolderOpen] = useState(false)
  const [newFolderName, setNewFolderName] = useState("")
  const [deleteFolderId, setDeleteFolderId] = useState<number | null>(null)
  const [permsFolder, setPermsFolder] = useState<DeptDocumentFolder | null>(null)
  const [tagManagerOpen, setTagManagerOpen] = useState(false)
  const [droppedFile, setDroppedFile] = useState<File | null>(null)
  const [isDraggingOver, setIsDraggingOver] = useState(false)
  const canView = department.my_access.documentation?.view
  const canManageFolders = canEdit && (isSuperuser || department.is_head)

  const dragCounter = useRef(0)
  function handleDragEnter(e: React.DragEvent) {
    if (!canEdit || !e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
    dragCounter.current += 1
    setIsDraggingOver(true)
  }
  function handleDragOver(e: React.DragEvent) {
    if (!canEdit || !e.dataTransfer.types.includes("Files")) return
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
    if (!canEdit || !file) return
    setDroppedFile(file)
    setImportOpen(true)
  }

  const { data: foldersData } = useQuery({
    queryKey: ["department-document-folders", department.id],
    queryFn: () => departmentsApi.documentFolders.list(department.id),
    enabled: canView,
  })
  const folders = foldersData?.folders ?? []
  const rootFolders = folders.filter((f) => f.parent_id === null)
  const currentFolder = folderId !== null ? folders.find((f) => f.id === folderId) ?? null : null
  const childFolders = folders.filter((f) => f.parent_id === folderId)

  const { data, isLoading } = useQuery({
    queryKey: ["department-documents", department.id, folderId],
    queryFn: () => documentsApi.byDepartment(department.id, { kind: "documentation", folderId: folderId ?? undefined }),
    enabled: canView,
  })

  const createMutation = useMutation({
    mutationFn: () => documentsApi.create({ title: "Новая страница", department_id: department.id, department_folder_id: folderId ?? undefined }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["department-documents", department.id] })
      navigate(`/documents/${res.id}`, { state: { justCreated: true, returnTo: { pathname: `/departments/${department.id}`, state: { tab: "documentation" } } } })
    },
    onError: (e: Error) => toast.error(e.message),
    onSettled: () => setCreating(false),
  })

  const createFolderMutation = useMutation({
    mutationFn: () => departmentsApi.documentFolders.create(department.id, { name: newFolderName.trim(), parent_id: folderId }),
    onSuccess: () => {
      toast.success("Папка создана")
      setNewFolderOpen(false)
      setNewFolderName("")
      queryClient.invalidateQueries({ queryKey: ["department-document-folders", department.id] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteFolderMutation = useMutation({
    mutationFn: (id: number) => departmentsApi.documentFolders.delete(department.id, id),
    onSuccess: () => {
      toast.success("Папка удалена")
      if (folderId === deleteFolderId) setFolderId(null)
      setDeleteFolderId(null)
      queryClient.invalidateQueries({ queryKey: ["department-document-folders", department.id] })
      queryClient.invalidateQueries({ queryKey: ["department-documents", department.id] })
    },
  })

  if (!canView) {
    return <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">Недостаточно прав для просмотра документации отдела.</CardContent></Card>
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[200px_1fr]">
      <aside className="space-y-1">
        <div className="flex items-center justify-between px-1">
          <span className="text-xs font-semibold text-muted-foreground">Папки</span>
          {canEdit && (
            <div className="flex items-center gap-0.5">
              <Button size="icon-xs" variant="ghost" onClick={() => setTagManagerOpen(true)} title="Теги документации">
                <Tags className="size-3.5" />
              </Button>
              <Button size="icon-xs" variant="ghost" onClick={() => setNewFolderOpen(true)} title="Новая папка">
                <FolderPlus className="size-3.5" />
              </Button>
            </div>
          )}
        </div>
        <button
          className={`flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent ${folderId === null ? "bg-accent font-medium" : ""}`}
          onClick={() => setFolderId(null)}
        >
          <NotebookText className="size-3.5 shrink-0 text-muted-foreground" /> Корень
        </button>
        {rootFolders.map((f) => (
          <DeptFolderNode
            key={f.id} folder={f} folders={folders} depth={0} activeId={folderId} canManage={canManageFolders}
            onSelect={setFolderId} onDelete={setDeleteFolderId} onPermissions={setPermsFolder}
          />
        ))}
      </aside>

      <div
        className="relative space-y-3"
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
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
            {currentFolder ? (
              <>
                <Button size="icon-xs" variant="ghost" onClick={() => setFolderId(currentFolder.parent_id)} title="Назад">
                  <ArrowLeft className="size-3.5" />
                </Button>
                <Folder className="size-3.5" /> <span className="font-medium text-foreground">{currentFolder.name}</span>
              </>
            ) : (
              <span className="font-medium text-foreground">Корень документации</span>
            )}
          </div>
          {canEdit && (
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => setImportOpen(true)}>
                <Upload className="size-4" /> Импорт
              </Button>
              <Button size="sm" disabled={creating} onClick={() => { setCreating(true); createMutation.mutate() }}>
                <Plus className="size-4" /> Новая страница
              </Button>
            </div>
          )}
        </div>

        {!!childFolders.length && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {childFolders.map((f) => (
              <Card
                key={f.id}
                className="cursor-pointer overflow-hidden py-0 transition-colors hover:border-primary/50"
                onClick={() => setFolderId(f.id)}
              >
                <CardContent className="flex items-center gap-2 p-3">
                  <Folder className="size-4 shrink-0 text-muted-foreground" />
                  <span className="truncate text-sm font-medium">{f.name}</span>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : !data?.documents.length ? (
          !childFolders.length && (
            <Card>
              <CardContent className="flex flex-col items-center gap-2 py-12 text-center text-sm text-muted-foreground">
                <NotebookText className="size-6 opacity-40" />
                {folderId ? "В этой папке пока пусто." : "В документации отдела пока пусто."}
              </CardContent>
            </Card>
          )
        ) : (
          <div className="flex flex-col gap-2">
            {data.documents.map((d) => (
              <Card
                key={d.id}
                className="cursor-pointer overflow-hidden py-0 transition-colors hover:border-primary/50"
                onClick={() => navigate(`/documents/${d.id}`, { state: { returnTo: { pathname: `/departments/${department.id}`, state: { tab: "documentation" } } } })}
              >
                <CardContent className="flex items-center gap-3 p-3">
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-lg">
                    <PlatformIcon value={d.icon} fallback={FileText} className="size-4 text-primary" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{d.title}</div>
                    <div className="text-xs text-muted-foreground">
                      {d.last_edited_by?.name ? `Изменил ${d.last_edited_by.name}` : "Ещё не редактировался"}
                      {d.is_mine && d.shared_summary && ` · ${d.shared_summary}`}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <ImportDocumentDialog
        open={importOpen}
        onOpenChange={(o) => { setImportOpen(o); if (!o) setDroppedFile(null) }}
        departmentId={department.id}
        departmentFolderId={folderId ?? undefined}
        initialFile={droppedFile}
        onImported={(id) => {
          queryClient.invalidateQueries({ queryKey: ["department-documents", department.id] })
          navigate(`/documents/${id}`, { state: { justCreated: true, returnTo: { pathname: `/departments/${department.id}`, state: { tab: "documentation" } } } })
        }}
      />

      <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader><DialogTitle>Новая папка</DialogTitle></DialogHeader>
          <div>
            <Label className="mb-1.5 block">Название</Label>
            <Input value={newFolderName} onChange={(e) => setNewFolderName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && newFolderName.trim() && createFolderMutation.mutate()} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setNewFolderOpen(false)}>Отмена</Button>
            <Button disabled={!newFolderName.trim() || createFolderMutation.isPending} onClick={() => createFolderMutation.mutate()}>Создать</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <FolderPermissionsDialog open={!!permsFolder} onOpenChange={(o) => !o && setPermsFolder(null)} department={department} folder={permsFolder} />

      <TagManagerDialog open={tagManagerOpen} onOpenChange={setTagManagerOpen} department={department} />

      <ConfirmDialog
        open={deleteFolderId !== null}
        onOpenChange={(o) => !o && setDeleteFolderId(null)}
        title="Удалить папку?"
        description="Страницы и вложенные папки переместятся на уровень выше."
        onConfirm={() => deleteFolderId !== null && deleteFolderMutation.mutate(deleteFolderId)}
      />
    </div>
  )
}
