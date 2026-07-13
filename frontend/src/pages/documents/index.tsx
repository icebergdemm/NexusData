import { useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  NotebookText, Plus, FolderPlus, Folder, FileText, Star, Users, Trash2,
  Globe2, MoreHorizontal, Copy, FolderInput, RotateCcw, XCircle,
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
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator,
  DropdownMenuSub, DropdownMenuSubTrigger, DropdownMenuSubContent, DropdownMenuPortal,
} from "@/components/ui/dropdown-menu"
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription,
  AlertDialogFooter, AlertDialogCancel, AlertDialogAction,
} from "@/components/ui/alert-dialog"
import { canEdit, useCurrentAdmin } from "@/lib/auth"
import { documentsApi, type DocScope, type DocumentRow, type DocFolder } from "@/pages/documents/api"

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

function initials(name: string | null | undefined) {
  return (name ?? "?").slice(0, 2).toUpperCase()
}

export function DocumentsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()

  const [scope, setScope] = useState<DocScope>("mine")
  const [folderId, setFolderId] = useState<number | null>(null)
  const [search, setSearch] = useState("")
  const [newFolderOpen, setNewFolderOpen] = useState(false)
  const [newFolderName, setNewFolderName] = useState("")
  const [deleteFolderId, setDeleteFolderId] = useState<number | null>(null)
  const [permanentDeleteId, setPermanentDeleteId] = useState<number | null>(null)

  const editAllowed = canEdit(admin, "documents")

  const { data: foldersData } = useQuery({
    queryKey: ["document-folders"],
    queryFn: () => documentsApi.folders.list(),
  })
  const folders = foldersData?.folders ?? []
  const rootFolders = folders.filter((f) => f.parent_id === null)

  const { data, isLoading } = useQuery({
    queryKey: ["documents", scope, scope === "mine" ? folderId : null, search],
    queryFn: () => documentsApi.list({ scope, folder_id: scope === "mine" ? folderId : undefined, search: search || undefined }),
  })
  const documents = data?.documents ?? []
  const invalidateDocs = () => queryClient.invalidateQueries({ queryKey: ["documents"] })

  const createDocMutation = useMutation({
    mutationFn: () => documentsApi.create({ folder_id: scope === "mine" ? folderId : null }),
    onSuccess: (res) => navigate(`/documents/${res.id}`),
    onError: (e: Error) => toast.error(e.message),
  })

  const createFolderMutation = useMutation({
    mutationFn: () => documentsApi.folders.create({ name: newFolderName.trim(), parent_id: folderId }),
    onSuccess: () => {
      toast.success("Папка создана")
      setNewFolderOpen(false)
      setNewFolderName("")
      queryClient.invalidateQueries({ queryKey: ["document-folders"] })
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

  return (
    <div>
      <PageHeader
        title="Документы"
        description="Общая база документов с совместным доступом и историей версий"
        icon={NotebookText}
        actions={editAllowed && (
          <>
            <Button variant="outline" size="sm" onClick={() => setNewFolderOpen(true)}><FolderPlus className="mr-1.5 size-4" /> Папка</Button>
            <Button size="sm" onClick={() => createDocMutation.mutate()} disabled={createDocMutation.isPending}>
              <Plus className="mr-1.5 size-4" /> Новый документ
            </Button>
          </>
        )}
      />

      <div className="grid grid-cols-[220px_1fr] gap-5">
        <aside className="space-y-3">
          <div className="space-y-0.5">
            {SCOPES.map((s) => (
              <button
                key={s.key}
                onClick={() => { setScope(s.key); setFolderId(null) }}
                className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors hover:bg-accent ${scope === s.key ? "bg-accent font-medium" : "text-muted-foreground"}`}
              >
                <s.icon className="size-4" /> {s.label}
              </button>
            ))}
            {admin?.is_superuser && (
              <button
                onClick={() => { setScope("all"); setFolderId(null) }}
                className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm transition-colors hover:bg-accent ${scope === "all" ? "bg-accent font-medium" : "text-muted-foreground"}`}
              >
                <Globe2 className="size-4" /> Все документы
              </button>
            )}
          </div>

          {scope === "mine" && (
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
        </aside>

        <main className="min-w-0">
          <SearchInput placeholder="Поиск по названию и содержимому…" value={search} onChange={(e) => setSearch(e.target.value)} className="mb-4 max-w-sm" />

          {isLoading ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-28" />)}
            </div>
          ) : documents.length === 0 ? (
            <Card><CardContent className="py-16 text-center text-muted-foreground">
              {scope === "trash" ? "Корзина пуста" : scope === "starred" ? "Нет избранных документов" : scope === "shared" ? "Пока нет документов, доступных вам" : "Здесь пока нет документов"}
            </CardContent></Card>
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
              {documents.map((doc: DocumentRow) => (
                <Card key={doc.id} className="group relative overflow-hidden py-0 transition-colors hover:border-primary/40">
                  <Link to={`/documents/${doc.id}`} className="block">
                    <CardContent className="p-4">
                      <div className="mb-2 flex items-center justify-between">
                        <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-lg">
                          {doc.icon || <FileText className="size-4 text-primary" />}
                        </div>
                        {doc.access_level && doc.access_level !== "owner" && (
                          <Badge variant="secondary" className="text-[10px]">{doc.access_level === "edit" ? "редактор" : doc.access_level === "comment" ? "комментатор" : "просмотр"}</Badge>
                        )}
                      </div>
                      <div className="truncate text-sm font-medium">{doc.title}</div>
                      <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                        <div className="flex size-4 items-center justify-center rounded-full bg-muted text-[8px] font-semibold">{initials(doc.last_edited_by?.name ?? doc.owner?.name)}</div>
                        <span className="truncate">{doc.owner?.name}</span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">
                        {doc.updated_at ? new Date(doc.updated_at).toLocaleString("ru-RU") : "—"}
                      </div>
                    </CardContent>
                  </Link>

                  <div className="absolute top-2 right-2 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    {scope !== "trash" && (
                      <Button size="icon-xs" variant="ghost" onClick={() => starMutation.mutate({ id: doc.id, next: !doc.is_starred })}>
                        <Star className={`size-3.5 ${doc.is_starred ? "fill-warning text-warning" : ""}`} />
                      </Button>
                    )}
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button size="icon-xs" variant="ghost"><MoreHorizontal className="size-3.5" /></Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {scope === "trash" ? (
                          <>
                            <DropdownMenuItem onClick={() => restoreMutation.mutate(doc.id)}><RotateCcw className="mr-1 size-4" /> Восстановить</DropdownMenuItem>
                            {doc.is_mine && (
                              <DropdownMenuItem variant="destructive" onClick={() => setPermanentDeleteId(doc.id)}><Trash2 className="mr-1 size-4" /> Удалить навсегда</DropdownMenuItem>
                            )}
                          </>
                        ) : (
                          <>
                            <DropdownMenuItem onClick={() => duplicateMutation.mutate(doc.id)}><Copy className="mr-1 size-4" /> Дублировать</DropdownMenuItem>
                            {doc.is_mine && folders.length > 0 && (
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
    </div>
  )
}
