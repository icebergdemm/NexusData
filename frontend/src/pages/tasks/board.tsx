import { useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowLeft, Plus, Settings, Trash2, MessageSquare, CalendarClock,
  User, Building2, X, Pencil, KanbanSquare, Copy,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from "@/components/ui/sheet"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PlatformIcon, PlatformIconField } from "@/components/layout/PlatformIcon"
import { AppContextMenu } from "@/components/layout/AppContextMenu"
import { useCurrentAdmin } from "@/lib/auth"
import { formatBrowserDate } from "@/lib/format-date"
import {
  tasksApi, PRIORITY_LABELS, PRIORITY_COLORS,
  type TaskColumnRow, type TaskRow, type TaskPriority, type AssigneeCandidates,
} from "./api"

function isOverdue(task: TaskRow): boolean {
  return !!task.deadline && new Date(task.deadline).getTime() < Date.now()
}

function copyToClipboard(text: string, label: string) {
  navigator.clipboard.writeText(text).then(() => toast.success(`${label} скопирован`)).catch(() => toast.error("Не удалось скопировать"))
}

function fmtDeadline(iso: string): string {
  return formatBrowserDate(iso)
}

// ── Task card (draggable) ───────────────────────────────────────────────────

function TaskCard({ task, onOpen, onDragStart, onDeleted }: {
  task: TaskRow
  onOpen: () => void
  onDragStart: (e: React.DragEvent) => void
  onDeleted: () => void
}) {
  const overdue = isOverdue(task)

  const deleteMutation = useMutation({
    mutationFn: () => tasksApi.tasks.delete(task.id),
    onSuccess: onDeleted,
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <AppContextMenu
      actions={[
        { label: "Открыть", icon: Pencil, onSelect: onOpen },
        { label: "Скопировать название", icon: Copy, onSelect: () => copyToClipboard(task.title, "Название") },
        { label: "Удалить", icon: Trash2, destructive: true, separatorBefore: true, onSelect: () => deleteMutation.mutate() },
      ]}
    >
    <Card
      draggable
      onDragStart={onDragStart}
      onClick={onOpen}
      className="cursor-pointer gap-2 overflow-hidden py-0 transition-colors hover:border-primary/50"
    >
      <CardContent className="space-y-1.5 p-3">
        <div className="text-sm font-medium">{task.title}</div>
        <div className="flex flex-wrap items-center gap-1.5">
          {task.priority && (
            <Badge style={{ backgroundColor: `${PRIORITY_COLORS[task.priority]}22`, color: PRIORITY_COLORS[task.priority] }} className="text-[10px]">
              {PRIORITY_LABELS[task.priority]}
            </Badge>
          )}
          {task.tags.map((t) => <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>)}
        </div>
        {(task.assignee_admin_name || task.assignee_department_name) && (
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
            {task.assignee_admin_name && <span className="flex items-center gap-1"><User className="size-3" /> {task.assignee_admin_name}</span>}
            {task.assignee_department_name && <span className="flex items-center gap-1"><Building2 className="size-3" /> {task.assignee_department_name}</span>}
          </div>
        )}
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          {task.deadline ? (
            <span className={`flex items-center gap-1 ${overdue ? "text-destructive" : ""}`}>
              <CalendarClock className="size-3" /> {fmtDeadline(task.deadline)}
            </span>
          ) : <span />}
          {task.comment_count > 0 && (
            <span className="flex items-center gap-1"><MessageSquare className="size-3" /> {task.comment_count}</span>
          )}
        </div>
      </CardContent>
    </Card>
    </AppContextMenu>
  )
}

// ── Column ───────────────────────────────────────────────────────────────────

function TaskColumnView({ column, tasks, canCreate, canManageBoards, onOpenTask, onDropTask, onEditColumn, onDeleteColumn, onCreateTask, onTaskDeleted }: {
  column: TaskColumnRow
  tasks: TaskRow[]
  canCreate: boolean
  canManageBoards: boolean
  onOpenTask: (id: number) => void
  onDropTask: (columnId: number, taskId: number) => void
  onEditColumn: () => void
  onDeleteColumn: () => void
  onCreateTask: (columnId: number, title: string) => void
  onTaskDeleted: () => void
}) {
  const [dragOver, setDragOver] = useState(false)
  const [addingTask, setAddingTask] = useState(false)
  const [newTitle, setNewTitle] = useState("")

  const submitNewTask = () => {
    const title = newTitle.trim()
    if (!title) { setAddingTask(false); return }
    onCreateTask(column.id, title)
    setNewTitle("")
    setAddingTask(false)
  }

  return (
    <div
      className={`flex w-72 shrink-0 flex-col gap-2 rounded-lg border p-2 ${dragOver ? "border-primary bg-primary/5" : "border-transparent"}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragOver(false)
        const taskId = Number(e.dataTransfer.getData("text/plain"))
        if (taskId) onDropTask(column.id, taskId)
      }}
    >
      <div className="flex items-center justify-between gap-2 px-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: column.color }} />
          <span className="truncate text-sm font-semibold">{column.name}</span>
          <Badge variant="secondary" className="text-[10px]">{tasks.length}</Badge>
        </div>
        {canManageBoards && (
          <div className="flex shrink-0 items-center gap-0.5">
            <Button size="icon-xs" variant="ghost" onClick={onEditColumn} title="Изменить колонку"><Pencil className="size-3.5" /></Button>
            <Button size="icon-xs" variant="ghost" className="text-destructive" onClick={onDeleteColumn} title="Удалить колонку"><Trash2 className="size-3.5" /></Button>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2">
        {tasks.map((t) => (
          <TaskCard
            key={t.id}
            task={t}
            onOpen={() => onOpenTask(t.id)}
            onDragStart={(e) => e.dataTransfer.setData("text/plain", String(t.id))}
            onDeleted={onTaskDeleted}
          />
        ))}
      </div>

      {canCreate && (
        addingTask ? (
          <div className="flex flex-col gap-1.5 rounded-md border p-2">
            <Input
              autoFocus
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submitNewTask(); if (e.key === "Escape") { setAddingTask(false); setNewTitle("") } }}
              placeholder="Название задачи"
            />
            <div className="flex gap-1.5">
              <Button size="sm" onClick={submitNewTask}>Добавить</Button>
              <Button size="sm" variant="ghost" onClick={() => { setAddingTask(false); setNewTitle("") }}>Отмена</Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="ghost" className="justify-start text-muted-foreground" onClick={() => setAddingTask(true)}>
            <Plus className="size-4" /> Задача
          </Button>
        )
      )}
    </div>
  )
}

// ── Column dialogs ───────────────────────────────────────────────────────────

function ColumnFormDialog({ open, onOpenChange, initial, onSubmit, title }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial: { name: string; color: string }
  onSubmit: (values: { name: string; color: string }) => void
  title: string
}) {
  const [name, setName] = useState(initial.name)
  const [color, setColor] = useState(initial.color)
  useEffect(() => { if (open) { setName(initial.name); setColor(initial.color) } }, [open, initial])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">Название</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Волков" />
          </div>
          <div>
            <Label className="mb-1.5 block">Цвет</Label>
            <Input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="h-9 p-1" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!name.trim()} onClick={() => onSubmit({ name: name.trim(), color })}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Board settings dialog ───────────────────────────────────────────────────

function BoardSettingsDialog({ open, onOpenChange, boardId, initial, candidates }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  boardId: number
  initial: { name: string; description: string; icon: string; color: string; department_id: number | null }
  candidates?: AssigneeCandidates
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [form, setForm] = useState(initial)
  useEffect(() => { if (open) setForm(initial) }, [open, initial])

  const updateMutation = useMutation({
    mutationFn: () => tasksApi.boards.update(boardId, form),
    onSuccess: () => {
      toast.success("Доска обновлена")
      queryClient.invalidateQueries({ queryKey: ["task-board", boardId] })
      queryClient.invalidateQueries({ queryKey: ["task-boards"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const archiveMutation = useMutation({
    mutationFn: () => tasksApi.boards.archive(boardId),
    onSuccess: () => {
      toast.success("Доска архивирована")
      queryClient.invalidateQueries({ queryKey: ["task-boards"] })
      onOpenChange(false)
      navigate("/tasks")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>Настройки доски</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">Название</Label>
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="mb-1.5 block">Иконка</Label>
              <PlatformIconField value={form.icon} onChange={(icon) => setForm((f) => ({ ...f, icon }))} />
            </div>
            <div>
              <Label className="mb-1.5 block">Цвет</Label>
              <Input type="color" value={form.color} onChange={(e) => setForm((f) => ({ ...f, color: e.target.value }))} className="h-9 p-1" />
            </div>
          </div>
          <div>
            <Label className="mb-1.5 block">Описание</Label>
            <Textarea rows={2} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
          </div>
          <div>
            <Label className="mb-1.5 block">Отдел (тег)</Label>
            <Select
              value={form.department_id ? String(form.department_id) : "none"}
              onValueChange={(v) => setForm((f) => ({ ...f, department_id: v === "none" ? null : Number(v) }))}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Без привязки к отделу</SelectItem>
                {candidates?.departments.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    <span className="flex items-center gap-2"><PlatformIcon value={d.icon} />{d.name}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter className="justify-between sm:justify-between">
          <Button variant="ghost" className="text-destructive" onClick={() => archiveMutation.mutate()}>Архивировать доску</Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
            <Button disabled={!form.name.trim() || updateMutation.isPending} onClick={() => updateMutation.mutate()}>Сохранить</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Task detail sheet ────────────────────────────────────────────────────────

function TaskDetailSheet({ taskId, task, onClose, candidates, canEditAll, canDeleteAll, currentAdminId }: {
  taskId: number | null
  task: TaskRow | undefined
  onClose: () => void
  candidates?: AssigneeCandidates
  canEditAll: boolean
  canDeleteAll: boolean
  currentAdminId: number | undefined
}) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [tagInput, setTagInput] = useState("")
  const [commentText, setCommentText] = useState("")
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)

  useEffect(() => {
    if (task) { setTitle(task.title); setDescription(task.description ?? "") }
  }, [task])

  const canEditThis = !!task && (canEditAll || task.created_by_admin_id === currentAdminId || task.assignee_admin_id === currentAdminId)
  const canDeleteThis = !!task && (canDeleteAll || task.created_by_admin_id === currentAdminId || task.assignee_admin_id === currentAdminId)

  const invalidateBoard = () => { if (task) queryClient.invalidateQueries({ queryKey: ["task-board", task.board_id] }) }

  const updateMutation = useMutation({
    mutationFn: (body: Parameters<typeof tasksApi.tasks.update>[1]) => tasksApi.tasks.update(taskId as number, body),
    onSuccess: invalidateBoard,
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteMutation = useMutation({
    mutationFn: () => tasksApi.tasks.delete(taskId as number),
    onSuccess: () => { invalidateBoard(); onClose() },
    onError: (e: Error) => toast.error(e.message),
  })

  const { data: commentsData } = useQuery({
    queryKey: ["task-comments", taskId],
    queryFn: () => tasksApi.comments.list(taskId as number),
    enabled: !!taskId,
  })

  const addCommentMutation = useMutation({
    mutationFn: (content: string) => tasksApi.comments.add(taskId as number, content),
    onSuccess: () => {
      setCommentText("")
      queryClient.invalidateQueries({ queryKey: ["task-comments", taskId] })
      invalidateBoard()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteCommentMutation = useMutation({
    mutationFn: (commentId: number) => tasksApi.comments.delete(taskId as number, commentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["task-comments", taskId] })
      invalidateBoard()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!task) return null

  const addTag = () => {
    const tag = tagInput.trim()
    if (!tag || task.tags.includes(tag)) { setTagInput(""); return }
    updateMutation.mutate({ tags: [...task.tags, tag] })
    setTagInput("")
  }
  const removeTag = (tag: string) => updateMutation.mutate({ tags: task.tags.filter((t) => t !== tag) })

  return (
    <>
    <Sheet open={!!taskId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle asChild>
            <Input
              value={title}
              disabled={!canEditThis}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => title.trim() && title !== task.title && updateMutation.mutate({ title: title.trim() })}
              className="border-none px-0 text-lg font-semibold shadow-none focus-visible:ring-0"
            />
          </SheetTitle>
        </SheetHeader>

        <div className="flex-1 space-y-4 px-4 pb-4">
          <div className="grid grid-cols-[100px_1fr] items-center gap-2 text-sm">
            <span className="text-muted-foreground">Создано</span>
            <span>{task.created_at ? formatBrowserDate(task.created_at) : "-"}{task.created_by_name ? ` · ${task.created_by_name}` : ""}</span>

            <span className="text-muted-foreground">Приоритет</span>
            <Select
              value={task.priority ?? "none"}
              disabled={!canEditThis}
              onValueChange={(v) => updateMutation.mutate({ priority: v === "none" ? null : v as TaskPriority })}
            >
              <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Не задан</SelectItem>
                {(Object.keys(PRIORITY_LABELS) as TaskPriority[]).map((p) => <SelectItem key={p} value={p}>{PRIORITY_LABELS[p]}</SelectItem>)}
              </SelectContent>
            </Select>

            <span className="text-muted-foreground">Дедлайн</span>
            <Input
              type="datetime-local"
              disabled={!canEditThis}
              className="h-8"
              value={task.deadline ? task.deadline.slice(0, 16) : ""}
              onChange={(e) => updateMutation.mutate({ deadline: e.target.value || null })}
            />

            <span className="text-muted-foreground">Ответственный</span>
            <Select
              value={task.assignee_admin_id ? String(task.assignee_admin_id) : "none"}
              disabled={!canEditThis}
              onValueChange={(v) => updateMutation.mutate({ assignee_admin_id: v === "none" ? null : Number(v) })}
            >
              <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Не назначен</SelectItem>
                {candidates?.admins.map((a) => <SelectItem key={a.id} value={String(a.id)}>{a.name ?? `#${a.id}`}</SelectItem>)}
              </SelectContent>
            </Select>

            <span className="text-muted-foreground">Отдел</span>
            <Select
              value={task.assignee_department_id ? String(task.assignee_department_id) : "none"}
              disabled={!canEditThis}
              onValueChange={(v) => updateMutation.mutate({ assignee_department_id: v === "none" ? null : Number(v) })}
            >
              <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Не назначен</SelectItem>
                {candidates?.departments.map((d) => (
                  <SelectItem key={d.id} value={String(d.id)}>
                    <span className="flex items-center gap-2"><PlatformIcon value={d.icon} />{d.name}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <span className="pt-1 text-muted-foreground">Теги</span>
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              {task.tags.map((t) => (
                <Badge key={t} variant="secondary" className="gap-1 text-[10px]">
                  {t}
                  {canEditThis && <button onClick={() => removeTag(t)}><X className="size-3" /></button>}
                </Badge>
              ))}
              {canEditThis && (
                <Input
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addTag()}
                  placeholder="+ тег"
                  className="h-6 w-20 text-xs"
                />
              )}
            </div>
          </div>

          <div>
            <Label className="mb-1.5 block text-xs text-muted-foreground">Описание задачи</Label>
            <Textarea
              rows={5}
              disabled={!canEditThis}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              onBlur={() => description !== (task.description ?? "") && updateMutation.mutate({ description })}
              placeholder="Описание..."
            />
          </div>

          {canDeleteThis && (
            <Button variant="outline" size="sm" className="text-destructive" onClick={() => setDeleteConfirmOpen(true)}>
              <Trash2 className="size-3.5" /> Удалить задачу
            </Button>
          )}

          <div className="space-y-2 border-t pt-3">
            <Label className="text-xs text-muted-foreground">Комментарии</Label>
            {commentsData?.comments.map((c) => (
              <div key={c.id} className="rounded-md border p-2 text-sm">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-medium">{c.admin_name ?? "—"}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-muted-foreground">{c.created_at ? formatBrowserDate(c.created_at) : ""}</span>
                    {(c.admin_id === currentAdminId || canEditAll) && (
                      <button onClick={() => deleteCommentMutation.mutate(c.id)} className="text-muted-foreground hover:text-destructive">
                        <Trash2 className="size-3" />
                      </button>
                    )}
                  </div>
                </div>
                <div className="whitespace-pre-wrap">{c.content}</div>
              </div>
            ))}
            <div className="flex gap-2">
              <Textarea rows={2} value={commentText} onChange={(e) => setCommentText(e.target.value)} placeholder="Написать комментарий..." />
              <Button size="sm" disabled={!commentText.trim()} onClick={() => addCommentMutation.mutate(commentText.trim())}>Отправить</Button>
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>

    <ConfirmDialog
      open={deleteConfirmOpen}
      onOpenChange={setDeleteConfirmOpen}
      title="Удалить задачу?"
      description={task.title}
      onConfirm={() => { deleteMutation.mutate(); setDeleteConfirmOpen(false) }}
    />
    </>
  )
}

// ── Board page ───────────────────────────────────────────────────────────────

export function TaskBoardPage() {
  const { boardId } = useParams<{ boardId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()
  const [addColumnOpen, setAddColumnOpen] = useState(false)
  const [editColumn, setEditColumn] = useState<TaskColumnRow | null>(null)
  const [deleteColumn, setDeleteColumn] = useState<TaskColumnRow | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null)

  const boardIdNum = Number(boardId)

  const { data, isLoading } = useQuery({
    queryKey: ["task-board", boardIdNum],
    queryFn: () => tasksApi.boards.get(boardIdNum),
    enabled: !!boardId,
  })

  const { data: candidates } = useQuery({
    queryKey: ["tasks-assignee-candidates"],
    queryFn: () => tasksApi.assigneeCandidates(),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["task-board", boardIdNum] })

  const createColumnMutation = useMutation({
    mutationFn: (body: { name: string; color: string }) => tasksApi.columns.create(boardIdNum, body),
    onSuccess: () => { invalidate(); setAddColumnOpen(false) },
    onError: (e: Error) => toast.error(e.message),
  })

  const updateColumnMutation = useMutation({
    mutationFn: (body: { name: string; color: string }) => tasksApi.columns.update(boardIdNum, (editColumn as TaskColumnRow).id, body),
    onSuccess: () => { invalidate(); setEditColumn(null) },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteColumnMutation = useMutation({
    mutationFn: () => tasksApi.columns.delete(boardIdNum, (deleteColumn as TaskColumnRow).id),
    onSuccess: () => { invalidate(); setDeleteColumn(null) },
    onError: (e: Error) => { toast.error(e.message); setDeleteColumn(null) },
  })

  const createTaskMutation = useMutation({
    mutationFn: ({ columnId, title }: { columnId: number; title: string }) =>
      tasksApi.tasks.create(boardIdNum, { title, column_id: columnId }),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error(e.message),
  })

  const reorderMutation = useMutation({
    mutationFn: (columns: Record<number, number[]>) => tasksApi.tasks.reorder(boardIdNum, columns),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error(e.message),
  })

  if (isLoading || !data) {
    return <div className="space-y-3"><Skeleton className="h-10 w-64" /><div className="flex gap-3">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-96 w-72" />)}</div></div>
  }

  const tasksByColumn = new Map<number, TaskRow[]>()
  for (const col of data.columns) tasksByColumn.set(col.id, [])
  for (const t of data.tasks) tasksByColumn.get(t.column_id)?.push(t)

  const handleDropTask = (targetColumnId: number, taskId: number) => {
    const task = data.tasks.find((t) => t.id === taskId)
    if (!task) return
    const sourceColumnId = task.column_id
    if (sourceColumnId === targetColumnId) return
    const destIds = (tasksByColumn.get(targetColumnId) ?? []).map((t) => t.id)
    destIds.push(taskId)
    const sourceIds = (tasksByColumn.get(sourceColumnId) ?? []).filter((t) => t.id !== taskId).map((t) => t.id)
    reorderMutation.mutate({ [targetColumnId]: destIds, [sourceColumnId]: sourceIds })
  }

  const selectedTask = data.tasks.find((t) => t.id === selectedTaskId)

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={() => navigate("/tasks")}><ArrowLeft className="size-4" /></Button>
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-xl">
            <PlatformIcon value={data.board.icon} fallback={KanbanSquare} className="size-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight">{data.board.name}</h1>
            {data.board.description && <p className="text-sm text-muted-foreground">{data.board.description}</p>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data.can_manage_boards && (
            <>
              <Button variant="outline" size="sm" onClick={() => setAddColumnOpen(true)}><Plus className="size-4" /> Колонка</Button>
              <Button variant="outline" size="icon" onClick={() => setSettingsOpen(true)}><Settings className="size-4" /></Button>
            </>
          )}
        </div>
      </div>

      <div className="flex gap-3 overflow-x-auto pb-4">
        {data.columns.map((col) => (
          <TaskColumnView
            key={col.id}
            column={col}
            tasks={tasksByColumn.get(col.id) ?? []}
            canCreate={data.can_create}
            canManageBoards={data.can_manage_boards}
            onOpenTask={setSelectedTaskId}
            onDropTask={handleDropTask}
            onEditColumn={() => setEditColumn(col)}
            onDeleteColumn={() => setDeleteColumn(col)}
            onCreateTask={(columnId, title) => createTaskMutation.mutate({ columnId, title })}
            onTaskDeleted={invalidate}
          />
        ))}
        {!data.columns.length && (
          <Card className="w-full"><CardContent className="py-12 text-center text-sm text-muted-foreground">На доске пока нет колонок.</CardContent></Card>
        )}
      </div>

      <ColumnFormDialog
        open={addColumnOpen}
        onOpenChange={setAddColumnOpen}
        initial={{ name: "", color: "#5A646E" }}
        title="Новая колонка"
        onSubmit={(v) => createColumnMutation.mutate(v)}
      />
      {editColumn && (
        <ColumnFormDialog
          open={!!editColumn}
          onOpenChange={(o) => !o && setEditColumn(null)}
          initial={{ name: editColumn.name, color: editColumn.color }}
          title="Изменить колонку"
          onSubmit={(v) => updateColumnMutation.mutate(v)}
        />
      )}
      <ConfirmDialog
        open={!!deleteColumn}
        onOpenChange={(o) => !o && setDeleteColumn(null)}
        title="Удалить колонку?"
        description={deleteColumn?.name}
        onConfirm={() => deleteColumnMutation.mutate()}
      />
      <BoardSettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        boardId={boardIdNum}
        initial={{
          name: data.board.name, description: data.board.description ?? "",
          icon: data.board.icon, color: data.board.color, department_id: data.board.department_id,
        }}
        candidates={candidates}
      />
      <TaskDetailSheet
        taskId={selectedTaskId}
        task={selectedTask}
        onClose={() => setSelectedTaskId(null)}
        candidates={candidates}
        canEditAll={data.can_edit_all}
        canDeleteAll={data.can_delete_all}
        currentAdminId={admin?.id}
      />
    </div>
  )
}
