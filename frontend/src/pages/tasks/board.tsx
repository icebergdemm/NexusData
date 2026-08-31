import { useEffect, useState, useRef } from "react"
import { useNavigate, useParams, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowLeft, Plus, Settings, Trash2, MessageSquare, CalendarClock,
  User, Building2, X, Pencil, KanbanSquare, Copy, Eye, EyeOff, Image as ImageIcon, Pin, PinOff, Send, Paperclip, Star, Shield, Link2, Ban, GripVertical, Flag, Check,
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
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import { SearchInput } from "@/components/layout/SearchInput"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PlatformIcon, PlatformIconField } from "@/components/layout/PlatformIcon"
import { AppContextMenu } from "@/components/layout/AppContextMenu"
import { useCurrentAdmin } from "@/lib/auth"
import { formatBrowserDate } from "@/lib/format-date"
import {
  tasksApi, PRIORITY_LABELS, PRIORITY_COLORS, STATUS_LABELS, STATUS_COLORS,
  type TaskColumnRow, type TaskRow, type TaskPriority, type TaskStatus, type AssigneeCandidates, type BoardPermission,
} from "./api"

function isOverdue(task: TaskRow): boolean {
  return !!task.deadline && new Date(task.deadline).getTime() < Date.now()
}

function copyToClipboard(text: string, label: string) {
  navigator.clipboard.writeText(text).then(() => toast.success(`${label} скопирован`)).catch(() => toast.error("Не удалось скопировать"))
}

function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return <Badge style={{ backgroundColor: `${STATUS_COLORS[status as TaskStatus]}22`, color: STATUS_COLORS[status as TaskStatus] }} className="text-[10px]">{STATUS_LABELS[status as TaskStatus] ?? status}</Badge>
}

// ── Board Permissions Dialog с поиском ───────────────────────────────────────
function BoardPermissionsDialog({ boardId, open, onOpenChange }: { boardId: number; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { data: permsData } = useQuery({ queryKey: ["board-perms", boardId], queryFn: () => tasksApi.boards.permissions.get(boardId), enabled: open })
  const { data: candidates } = useQuery({ queryKey: ["tasks-assignee-candidates"], queryFn: () => tasksApi.assigneeCandidates(), enabled: open })
  const { data: groupsData } = useQuery({ queryKey: ["admin-groups"], queryFn: () => import("@/lib/api").then(m => m.api.get<{ groups: { id: number; name: string }[] }>("/api/v1/admin/users").then(r => (r as unknown as { groups: { id: number; name: string }[] }).groups ? { groups: (r as unknown as { groups: { id: number; name: string }[] }).groups } : { groups: [] })), enabled: open })
  // fallback: fetch groups via users endpoint already returns groups, use candidates for admins
  const [rows, setRows] = useState<BoardPermission[]>([])
  const [newType, setNewType] = useState<BoardPermission["grantee_type"]>("user")
  const [searchAdmin, setSearchAdmin] = useState("")
  const [searchGroup, setSearchGroup] = useState("")
  const [searchDept, setSearchDept] = useState("")
  const [selectedAdminId, setSelectedAdminId] = useState("")
  const [selectedGroupId, setSelectedGroupId] = useState("")
  const [selectedDeptLevel, setSelectedDeptLevel] = useState("")

  useEffect(() => { if (permsData) setRows(permsData.permissions) }, [permsData])

  const filteredAdmins = (candidates?.admins ?? []).filter(a => {
    if (!searchAdmin.trim()) return true
    const q = searchAdmin.toLowerCase()
    return (a.name ?? "").toLowerCase().includes(q) || String(a.id).includes(q)
  }).slice(0, 8)

  const filteredGroups = ((groupsData as unknown as { groups: { id: number; name: string }[] })?.groups ?? candidates?.departments ?? []).filter((g: { id: number; name: string | null }) => {
    if (!searchGroup.trim()) return true
    const q = searchGroup.toLowerCase()
    return (g.name ?? "").toLowerCase().includes(q) || String(g.id).includes(q)
  }).slice(0, 8) as { id: number; name: string | null }[]

  const filteredDepts = (candidates?.departments ?? []).filter(d => {
    if (!searchDept.trim()) return true
    const q = searchDept.toLowerCase()
    return (d.name ?? "").toLowerCase().includes(q) || String(d.id).includes(q)
  }).slice(0, 8)

  const saveMutation = useMutation({
    mutationFn: () => tasksApi.boards.permissions.set(boardId, rows),
    onSuccess: () => { toast.success("Права сохранены"); onOpenChange(false) },
    onError: (e: Error) => toast.error(e.message),
  })

  const addRow = () => {
    let key = ""
    if (newType === "user") key = selectedAdminId
    else if (newType === "group_nd") key = selectedGroupId
    else if (newType === "dept_level") key = selectedDeptLevel
    if (!key) { toast.error("Выберите значение"); return }
    if (rows.some(r => r.grantee_type === newType && r.grantee_key === key)) { toast.error("Уже добавлено"); return }
    setRows([...rows, { grantee_type: newType, grantee_key: key, can_view: true, can_create: false, can_edit: false, can_delete: false }])
    setSelectedAdminId(""); setSelectedGroupId(""); setSelectedDeptLevel("")
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto">
        <DialogHeader><DialogTitle>Права доски</DialogTitle><DialogDescription>Кто может смотреть / добавлять / изменять / удалять. Пусто = только глобальные права + суперюзер.</DialogDescription></DialogHeader>
        <div className="space-y-3">
          {rows.map((r, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 rounded border p-2 text-xs">
              <Badge variant="secondary" className="text-[10px]">{r.grantee_type === "group_nd" ? "Группа ND" : r.grantee_type === "user" ? "Пользователь" : "Уровень отдела"}</Badge>
              <span className="font-mono text-xs">{r.grantee_key}</span>
              <div className="ml-auto flex gap-1">
                {(["can_view", "can_create", "can_edit", "can_delete"] as const).map(k => (
                  <label key={k} className="flex items-center gap-1 text-[11px]"><input type="checkbox" checked={r[k]} onChange={e => setRows(rows.map((x, idx) => idx === i ? { ...x, [k]: e.target.checked } : x))} />{k.replace("can_", "")}</label>
                ))}
                <Button size="icon-xs" variant="ghost" onClick={() => setRows(rows.filter((_, idx) => idx !== i))}><X className="size-3" /></Button>
              </div>
            </div>
          ))}
          <div className="rounded border p-3 space-y-2 bg-muted/20">
            <div className="flex gap-2">
              <Select value={newType} onValueChange={v => setNewType(v as BoardPermission["grantee_type"])}><SelectTrigger className="w-40"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="user">Пользователь</SelectItem><SelectItem value="group_nd">Группа ND</SelectItem><SelectItem value="dept_level">Уровень отдела</SelectItem></SelectContent></Select>
              <Button size="sm" onClick={addRow}><Plus className="size-3" /> Добавить</Button>
            </div>
            {newType === "user" && (
              <div className="space-y-1">
                <SearchInput value={searchAdmin} onChange={e => setSearchAdmin(e.target.value)} placeholder="Поиск по нику / DS ID / ID..." className="h-8" />
                <div className="max-h-28 overflow-y-auto rounded border bg-background">
                  {filteredAdmins.map(a => (
                    <button key={a.id} className={`flex w-full items-center justify-between px-2 py-1.5 text-left text-xs hover:bg-accent ${selectedAdminId === String(a.id) ? "bg-accent" : ""}`} onClick={() => setSelectedAdminId(String(a.id))}>
                      <span>{a.name ?? `#${a.id}`} <span className="text-muted-foreground">#{a.id}</span></span>{selectedAdminId === String(a.id) && <Check className="size-3 text-primary" />}
                    </button>
                  ))}
                  {!filteredAdmins.length && <div className="px-2 py-2 text-xs text-muted-foreground">Не найдено</div>}
                </div>
              </div>
            )}
            {newType === "group_nd" && (
              <div className="space-y-1">
                <SearchInput value={searchGroup} onChange={e => setSearchGroup(e.target.value)} placeholder="Поиск группы..." className="h-8" />
                <div className="max-h-28 overflow-y-auto rounded border bg-background">
                  {filteredGroups.map((g: { id: number; name: string | null }) => (
                    <button key={g.id} className={`flex w-full items-center justify-between px-2 py-1.5 text-left text-xs hover:bg-accent ${selectedGroupId === String(g.id) ? "bg-accent" : ""}`} onClick={() => setSelectedGroupId(String(g.id))}>
                      <span>{g.name ?? `#${g.id}`}</span>{selectedGroupId === String(g.id) && <Check className="size-3 text-primary" />}
                    </button>
                  ))}
                  {!filteredGroups.length && <div className="px-2 py-2 text-xs text-muted-foreground">Не найдено</div>}
                </div>
              </div>
            )}
            {newType === "dept_level" && (
              <div className="space-y-1">
                <SearchInput value={searchDept} onChange={e => setSearchDept(e.target.value)} placeholder="Поиск отдела..." className="h-8" />
                <div className="max-h-28 overflow-y-auto rounded border bg-background">
                  {filteredDepts.map(d => (
                    <button key={d.id} className="w-full px-2 py-1.5 text-left text-xs hover:bg-accent" onClick={() => setSelectedDeptLevel(`${d.id}:base`)}>{d.name} <span className="text-muted-foreground">#{d.id}</span></button>
                  ))}
                </div>
                <Input placeholder="deptId:levelKey (напр. 3:base)" value={selectedDeptLevel} onChange={e => setSelectedDeptLevel(e.target.value)} className="h-8" />
              </div>
            )}
          </div>
        </div>
        <DialogFooter><Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>Сохранить</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Task Card (без тегов, с анимацией) ───────────────────────────────────────
function TaskCard({ task, onOpen, onDragStart, onDeleted, boardId, columns }: {
  task: TaskRow
  onOpen: () => void
  onDragStart: (e: React.DragEvent) => void
  onDeleted: () => void
  boardId: number
  columns: TaskColumnRow[]
}) {
  const overdue = isOverdue(task)
  const queryClient = useQueryClient()
  const copyLink = () => {
    const url = `${window.location.origin}/tasks/${boardId}?task=${task.id}`
    copyToClipboard(url, "Ссылка на задачу")
  }
  const deleteMutation = useMutation({
    mutationFn: () => tasksApi.tasks.delete(task.id),
    onSuccess: onDeleted,
    onError: (e: Error) => toast.error(e.message),
  })
  const toggleUrgent = () => {
    const newPriority = task.priority === "urgent" ? null : "urgent" as TaskPriority
    tasksApi.tasks.update(task.id, { priority: newPriority }).then(() => queryClient.invalidateQueries({ queryKey: ["task-board", boardId] }))
  }

  const moveActions = columns.filter(c => c.id !== task.column_id).map(c => ({
    label: `Переместить → ${c.name}`,
    icon: GripVertical,
    onSelect: () => tasksApi.tasks.update(task.id, { column_id: c.id }).then(() => queryClient.invalidateQueries({ queryKey: ["task-board", boardId] })),
  }))

  return (
    <AppContextMenu
      actions={[
        { label: "Открыть", icon: Pencil, onSelect: onOpen },
        { label: "Копировать название", icon: Copy, onSelect: () => copyToClipboard(task.title, "Название") },
        ...moveActions.slice(0, 5),
        { label: task.priority === "urgent" ? "Снять высокий приоритет" : "Выделить высоким приоритетом", icon: Flag, onSelect: toggleUrgent, separatorBefore: true },
        { label: task.is_hidden ? "Показать задачу" : "Скрыть задачу", icon: task.is_hidden ? Eye : EyeOff, onSelect: () => tasksApi.tasks.toggleHidden(task.id).then(() => queryClient.invalidateQueries({ queryKey: ["task-board", boardId] })) },
        { label: "Отклонить с причиной", icon: Ban, onSelect: () => { const r = prompt("Причина отклонения:"); if (r) tasksApi.tasks.reject(task.id, r).then(() => queryClient.invalidateQueries({ queryKey: ["task-board", boardId] })) }, separatorBefore: true, destructive: true },
        { label: "Удалить", icon: Trash2, destructive: true, onSelect: () => deleteMutation.mutate() },
      ]}
    >
    <Card
      draggable
      onDragStart={onDragStart}
      onClick={onOpen}
      className={`group cursor-pointer gap-0 overflow-hidden py-0 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:border-primary/50 animate-in fade-in-0 zoom-in-[0.98] data-[dragging=true]:opacity-50 ${task.is_hidden ? "opacity-60 border-dashed" : ""} ${task.priority === "urgent" ? "ring-1 ring-urgent/30" : ""}`}
    >
      {task.preview_url && <img src={task.preview_url} alt="" className="h-28 w-full object-cover" />}
      <CardContent className="space-y-1.5 p-3">
        <div className="flex items-start justify-between gap-1">
          <div className="line-clamp-2 text-sm font-medium leading-tight">{task.title}</div>
          {task.is_hidden && <Badge variant="outline" className="text-[10px] border-warning/40 text-warning shrink-0">скрыта</Badge>}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <TaskStatusBadge status={task.status} />
          {task.priority && <Badge style={{ backgroundColor: `${PRIORITY_COLORS[task.priority]}22`, color: PRIORITY_COLORS[task.priority] }} className="text-[10px]">{PRIORITY_LABELS[task.priority]}</Badge>}
          {task.attachment_count > 0 && <Badge variant="outline" className="gap-1 text-[10px]"><ImageIcon className="size-3" />{task.attachment_count}</Badge>}
          {task.rejection_reason && <Badge variant="outline" className="text-[10px] border-destructive/40 text-destructive">отклонена</Badge>}
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
              <CalendarClock className="size-3" /> {formatBrowserDate(task.deadline)}
            </span>
          ) : <span />}
          <span className="flex items-center gap-2">
            {task.comment_count > 0 && <span className="flex items-center gap-1"><MessageSquare className="size-3" /> {task.comment_count}</span>}
            <Button size="icon-xs" variant="ghost" className="h-5 w-5 opacity-0 group-hover:opacity-100" onClick={(e) => { e.stopPropagation(); copyLink() }} title="Копировать ссылку"><Link2 className="size-3" /></Button>
          </span>
        </div>
      </CardContent>
    </Card>
    </AppContextMenu>
  )
}

// ── Column ───────────────────────────────────────────────────────────────────
function TaskColumnView({ column, tasks, canCreate, canManageBoards, onOpenTask, onDropTask, onEditColumn, onDeleteColumn, onCreateTask, onTaskDeleted, boardId, allColumns }: {
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
  boardId: number
  allColumns: TaskColumnRow[]
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
      className={`flex w-72 shrink-0 flex-col gap-2 rounded-lg border p-2 transition-colors ${dragOver ? "border-primary bg-primary/5" : "border-transparent bg-muted/20"}`}
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
            boardId={boardId}
            columns={allColumns}
            onOpen={() => onOpenTask(t.id)}
            onDragStart={(e) => e.dataTransfer.setData("text/plain", String(t.id))}
            onDeleted={onTaskDeleted}
          />
        ))}
      </div>

      {canCreate && (
        addingTask ? (
          <div className="flex flex-col gap-1.5 rounded-md border bg-card p-2 animate-in fade-in-0">
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

// ── Task Detail Dialog (center, без тегов, логи внизу, большое поле файлов) ──
function TaskDetailDialog({ taskId, task, onClose, candidates, canEditAll, canDeleteAll, currentAdminId, boardId }: {
  taskId: number | null
  task: TaskRow | undefined
  onClose: () => void
  candidates?: AssigneeCandidates
  canEditAll: boolean
  canDeleteAll: boolean
  currentAdminId: number | undefined
  boardId: number
  allTasks?: TaskRow[]
}) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [commentText, setCommentText] = useState("")
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState("")
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data: activityData } = useQuery({ queryKey: ["task-activity", taskId], queryFn: () => tasksApi.tasks.activity(taskId as number), enabled: !!taskId })
  const { data: attachmentsData } = useQuery({ queryKey: ["task-attachments", taskId], queryFn: () => tasksApi.tasks.attachments.list(taskId as number), enabled: !!taskId })

  useEffect(() => {
    if (task) { setTitle(task.title); setDescription(task.description ?? "") }
  }, [task])

  const canEditThis = !!task && (canEditAll || task.created_by_admin_id === currentAdminId || task.assignee_admin_id === currentAdminId)
  const canDeleteThis = !!task && (canDeleteAll || task.created_by_admin_id === currentAdminId || task.assignee_admin_id === currentAdminId)

  const invalidateBoard = () => { if (task) queryClient.invalidateQueries({ queryKey: ["task-board", boardId] }); queryClient.invalidateQueries({ queryKey: ["task-activity", taskId] }); queryClient.invalidateQueries({ queryKey: ["task-attachments", taskId] }) }

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

  const uploadFiles = async (files: FileList | File[]) => {
    const list = Array.from(files as unknown as File[])
    for (const f of list) {
      try { await tasksApi.tasks.attachments.upload(taskId as number, f, false); toast.success(`${f.name} прикреплён`) } catch (e: unknown) { toast.error((e as Error).message) }
    }
    queryClient.invalidateQueries({ queryKey: ["task-attachments", taskId] }); invalidateBoard()
  }

  const setPreviewMutation = useMutation({
    mutationFn: (attachmentId: number | null) => tasksApi.tasks.update(taskId as number, { preview_attachment_id: attachmentId }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["task-attachments", taskId] }); invalidateBoard(); toast.success("Превью обновлено") },
    onError: (e: Error) => toast.error(e.message),
  })

  const copyLink = () => {
    const url = `${window.location.origin}/tasks/${boardId}?task=${taskId}`
    copyToClipboard(url, "Ссылка на задачу")
  }

  if (!task) return null

  return (
    <>
    <Dialog open={!!taskId} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] w-full max-w-2xl overflow-y-auto" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Input
              value={title}
              disabled={!canEditThis}
              onChange={(e) => setTitle(e.target.value)}
              onBlur={() => title.trim() && title !== task.title && updateMutation.mutate({ title: title.trim() })}
              className="border-none px-0 text-lg font-semibold shadow-none focus-visible:ring-0"
            />
            <Button size="icon-xs" variant="ghost" onClick={copyLink} title="Копировать ссылку"><Link2 className="size-4" /></Button>
          </DialogTitle>
          <DialogDescription className="flex flex-wrap gap-1">
            <TaskStatusBadge status={task.status} />
            {task.is_hidden && <Badge variant="outline" className="border-warning/40 text-warning">скрыта</Badge>}
            {task.rejection_reason && <span className="text-xs text-destructive">Отклонена: {task.rejection_reason}</span>}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-[100px_1fr] items-center gap-2 text-sm">
            <span className="text-muted-foreground">Статус</span>
            <Select value={task.status} disabled={!canEditThis} onValueChange={(v) => updateMutation.mutate({ status: v as TaskStatus })}>
              <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(STATUS_LABELS).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
              </SelectContent>
            </Select>

            <span className="text-muted-foreground">Создано</span>
            <span>{task.created_at ? formatBrowserDate(task.created_at) : "-"} {task.created_by_name ? `· ${task.created_by_name}` : ""}</span>

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
          </div>

          <div>
            <Label className="mb-1.5 block text-xs text-muted-foreground">Описание задачи</Label>
            <Textarea
              rows={4}
              disabled={!canEditThis}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              onBlur={() => description !== (task.description ?? "") && updateMutation.mutate({ description })}
              placeholder="Описание..."
            />
          </div>

          {/* Большое поле для скриншотов/файлов */}
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground flex items-center gap-1"><Paperclip className="size-3" /> Скриншоты / файлы — перетащите или нажмите</Label>
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files) }}
              onPaste={(e) => { const files = Array.from(e.clipboardData.files); if (files.length) { e.preventDefault(); uploadFiles(files as unknown as FileList) } }}
              onClick={() => fileInputRef.current?.click()}
              className={`flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-4 text-center transition-colors ${dragOver ? "border-primary bg-primary/5" : "border-muted-foreground/30 bg-muted/20 hover:bg-muted/30"}`}
            >
              <ImageIcon className="size-8 text-muted-foreground" />
              <div className="text-sm">Перетащите файл или картинку сюда</div>
              <div className="text-xs text-muted-foreground">или нажмите для выбора · поддерживается Ctrl+V</div>
              <div className="text-[11px] text-muted-foreground">PNG, JPG, PDF до 10 MB</div>
            </div>
            <input ref={fileInputRef} type="file" multiple accept="image/*,.pdf,.txt,.log" className="hidden" onChange={(e) => { if (e.target.files?.length) uploadFiles(e.target.files) }} />
            <div className="grid grid-cols-3 gap-2">
              {(attachmentsData?.attachments ?? []).map((att) => (
                <div key={att.id} className={`relative rounded border overflow-hidden group ${task.preview_attachment_id === att.id ? "ring-2 ring-primary" : ""}`}>
                  {att.mime_type?.startsWith("image/") ? <img src={att.url} alt={att.filename} className="h-24 w-full object-cover" /> : <div className="h-24 flex items-center justify-center bg-muted text-xs p-1 text-center">{att.filename}</div>}
                  <div className="absolute inset-0 hidden group-hover:flex items-center justify-center gap-1 bg-black/40">
                    <Button size="icon-xs" variant="secondary" onClick={() => setPreviewMutation.mutate(att.id)} title="Сделать превью"><Star className="size-3" /></Button>
                    <Button size="icon-xs" variant="secondary" onClick={() => window.open(att.url, "_blank")} title="Открыть"><Eye className="size-3" /></Button>
                    {canEditThis && <Button size="icon-xs" variant="destructive" onClick={() => tasksApi.tasks.attachments.delete(task.id, att.id).then(() => { queryClient.invalidateQueries({ queryKey: ["task-attachments", taskId] }); invalidateBoard() })}><Trash2 className="size-3" /></Button>}
                  </div>
                  <div className="p-1 text-[10px] truncate bg-background/80">{att.filename}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {canEditThis && (
              <>
                <Button size="sm" variant="outline" onClick={() => setRejectOpen(true)} className="text-destructive"><Ban className="size-3.5" /> Отклонить</Button>
                <Button size="sm" variant="outline" onClick={() => tasksApi.tasks.toggleHidden(task.id).then(() => invalidateBoard())}>{task.is_hidden ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />} {task.is_hidden ? "Показать" : "Скрыть"}</Button>
              </>
            )}
            {canDeleteThis && (
              <Button variant="outline" size="sm" className="text-destructive ml-auto" onClick={() => setDeleteConfirmOpen(true)}>
                <Trash2 className="size-3.5" /> Удалить задачу
              </Button>
            )}
          </div>

          <div className="space-y-2">
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

          {/* Логи в самом низу */}
          <div className="space-y-2 border-t pt-3">
            <Label className="text-xs text-muted-foreground">История действий — лог</Label>
            <div className="max-h-48 overflow-y-auto space-y-1 pr-1">
              {(activityData?.activity ?? []).map((a) => (
                <div key={a.id} className="flex gap-2 text-xs rounded bg-muted/30 px-2 py-1">
                  <span className="font-medium shrink-0">{a.admin_name ?? "Система"}</span>
                  <span className="text-muted-foreground">{a.action}</span>
                  {a.detail && <span className="truncate">— {a.detail}</span>}
                  <span className="ml-auto text-[10px] text-muted-foreground shrink-0">{a.created_at ? formatBrowserDate(a.created_at) : ""}</span>
                </div>
              ))}
              {!activityData?.activity.length && <div className="text-xs text-muted-foreground text-center py-2">Нет записей</div>}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>

    <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader><DialogTitle>Отклонить задачу</DialogTitle><DialogDescription>Укажите причину — задача станет скрытой.</DialogDescription></DialogHeader>
        <Textarea value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} placeholder="Причина отклонения" rows={3} />
        <DialogFooter>
          <Button variant="ghost" onClick={() => setRejectOpen(false)}>Отмена</Button>
          <Button variant="destructive" disabled={!rejectReason.trim()} onClick={async () => { await tasksApi.tasks.reject(task.id, rejectReason.trim()); queryClient.invalidateQueries({ queryKey: ["task-board", boardId] }); setRejectOpen(false); setRejectReason(""); onClose() }}>Отклонить и скрыть</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

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

// ── Board Chat (закрывающийся сайдбар, скриншоты, якоря с названием) ─────────
function BoardChat({ boardId, tasks, onClose }: { boardId: number; tasks: TaskRow[]; onClose?: () => void }) {
  const [input, setInput] = useState("")
  const [replyTo, setReplyTo] = useState<number | null>(null)
  const [showPinned, setShowPinned] = useState(true)
  const fileRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const { data, refetch } = useQuery({ queryKey: ["board-chat", boardId], queryFn: () => tasksApi.boards.chat.list(boardId, { limit: 50 }), refetchInterval: 5000 })
  const messages = data?.messages ?? []
  const pinned = data?.pinned ?? []
  const taskMap = new Map(tasks.map(t => [t.id, t]))

  const sendMutation = useMutation({
    mutationFn: async () => {
      if (!input.trim() && !fileRef.current?.files?.length) return
      const fd = new FormData()
      fd.append("content", input.trim())
      if (replyTo) fd.append("reply_to_id", String(replyTo))
      const files = fileRef.current?.files
      if (files) for (let i = 0; i < files.length; i++) fd.append("files", files[i])
      if (!files || files.length === 0) {
        return tasksApi.boards.chat.sendJson(boardId, { content: input.trim(), reply_to_id: replyTo })
      }
      return tasksApi.boards.chat.send(boardId, fd)
    },
    onSuccess: () => { setInput(""); setReplyTo(null); if (fileRef.current) fileRef.current.value = ""; queryClient.invalidateQueries({ queryKey: ["board-chat", boardId] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const renderContent = (text: string) => {
    const parts = text.split(/(task:\/\/\d+|#task-\d+|https?:\/\/[^\s]+\/tasks\/\d+\?task=\d+|#\d+)/g)
    return parts.map((p, i) => {
      const m = p.match(/task:\/\/(\d+)|#task-(\d+)|#(\d+)/)
      let taskId: string | null = null
      if (m) taskId = m[1] || m[2] || m[3]
      else {
        const u = p.match(/task=(\d+)/)
        if (u) taskId = u[1]
      }
      if (taskId) {
        const t = taskMap.get(Number(taskId))
        const label = t ? `${t.title.slice(0, 24)}` : `#${taskId}`
        const emoji = t ? (t.priority === "urgent" ? "🔥 " : t.status === "done" ? "✅ " : "📋 ") : "🔗 "
        return <a key={i} href={`?task=${taskId}`} onClick={(e) => { e.preventDefault(); window.dispatchEvent(new CustomEvent("open-task", { detail: Number(taskId) })) }} className="inline-flex items-center gap-1 rounded bg-primary/15 px-1.5 py-0.5 font-medium text-xs text-primary hover:underline">{emoji}{label}</a>
      }
      if (p.startsWith("http")) {
        return <a key={i} href={p} target="_blank" rel="noreferrer" className="text-primary underline">{p}</a>
      }
      return <span key={i}>{p}</span>
    })
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] w-80 shrink-0 flex-col border-l bg-card sticky top-4 self-start">
      <div className="flex items-center justify-between border-b p-2">
        <span className="text-sm font-semibold flex items-center gap-1.5"><MessageSquare className="size-4" /> Чат доски</span>
        <div className="flex gap-1">
          <Button size="icon-xs" variant="ghost" onClick={() => refetch()} title="Обновить">↻</Button>
          <Button size="icon-xs" variant="ghost" onClick={() => setShowPinned(!showPinned)} title={showPinned ? "Скрыть закрепы" : "Показать закрепы"}>{showPinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}</Button>
          {onClose && <Button size="icon-xs" variant="ghost" onClick={onClose} title="Закрыть чат"><X className="size-3.5" /></Button>}
        </div>
      </div>
      {showPinned && pinned.length > 0 && (
        <div className="border-b bg-amber-50 dark:bg-amber-950/20 p-2 space-y-1 max-h-32 overflow-y-auto">
          <div className="text-[11px] font-medium text-amber-700 dark:text-amber-300 flex items-center gap-1"><Pin className="size-3" /> Закреплённые ({pinned.length})</div>
          {pinned.map(m => (
            <div key={m.id} className="rounded bg-amber-100 dark:bg-amber-900/30 px-2 py-1 text-xs">
              <span className="font-medium">{m.author_name}: </span>{m.content.slice(0, 80)}
            </div>
          ))}
        </div>
      )}
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {messages.map(m => (
          <div key={m.id} className="group rounded border bg-muted/20 p-2 text-sm">
            <div className="flex items-center justify-between gap-1">
              <span className="text-xs font-medium">{m.author_name ?? "—"}</span>
              <span className="text-[10px] text-muted-foreground">{m.created_at ? formatBrowserDate(m.created_at) : ""}</span>
            </div>
            {m.reply_to_id && <div className="text-[11px] text-muted-foreground border-l-2 pl-1">↳ ответ на #{m.reply_to_id}</div>}
            <div className="whitespace-pre-wrap break-words text-sm mt-1">{renderContent(m.content)}</div>
            {m.attachments?.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{m.attachments.map(a => a.mime_type?.startsWith("image/") ? <a key={a.id} href={a.url} target="_blank" rel="noreferrer"><img src={a.url} alt={a.filename} className="h-16 rounded border" /></a> : <a key={a.id} href={a.url} target="_blank" className="text-xs text-primary underline">{a.filename}</a>)}</div>}
            <div className="mt-1 hidden group-hover:flex gap-1">
              <Button size="xs" variant="ghost" onClick={() => setReplyTo(m.id)}>Ответить</Button>
              <Button size="xs" variant="ghost" onClick={() => tasksApi.boards.chat.togglePin(m.board_id, m.id).then(() => queryClient.invalidateQueries({ queryKey: ["board-chat", boardId] }))}>{m.is_pinned ? <PinOff className="size-3" /> : <Pin className="size-3" />} {m.is_pinned ? "Открепить" : "Закрепить"}</Button>
              <Button size="xs" variant="ghost" onClick={() => copyToClipboard(m.content, "Сообщение")}>Копировать</Button>
            </div>
          </div>
        ))}
        {!messages.length && <div className="py-8 text-center text-sm text-muted-foreground">Пока нет сообщений — начните общение</div>}
      </div>
      {replyTo && <div className="flex items-center justify-between bg-muted px-2 py-1 text-xs"><span>Ответ на #{replyTo}</span><Button size="icon-xs" variant="ghost" onClick={() => setReplyTo(null)}><X className="size-3" /></Button></div>}
      <div className="border-t p-2 flex gap-1">
        <Input type="file" ref={fileRef} multiple accept="image/*,.pdf,.txt,.log" className="hidden" />
        <Button size="icon-sm" variant="ghost" onClick={() => fileRef.current?.click()} title="Файлы"><Paperclip className="size-4" /></Button>
        <Textarea
          rows={1}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMutation.mutate() } }}
          onPaste={(e) => {
            const files = Array.from(e.clipboardData.files)
            if (files.length && fileRef.current) {
              e.preventDefault()
              const dt = new DataTransfer()
              files.forEach(f => dt.items.add(f))
              fileRef.current.files = dt.files
              toast.success("Файл из буфера добавлен — отправьте сообщением")
            }
          }}
          placeholder="Сообщение... (task://123 — якорь)"
          className="min-h-9 flex-1 resize-none"
        />
        <Button size="icon-sm" onClick={() => sendMutation.mutate()} disabled={(!input.trim() && !fileRef.current?.files?.length) || sendMutation.isPending}><Send className="size-4" /></Button>
      </div>
    </div>
  )
}

// ── Board page ───────────────────────────────────────────────────────────────

export function TaskBoardPage() {
  const { boardId } = useParams<{ boardId: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()
  const [addColumnOpen, setAddColumnOpen] = useState(false)
  const [editColumn, setEditColumn] = useState<TaskColumnRow | null>(null)
  const [deleteColumn, setDeleteColumn] = useState<TaskColumnRow | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null)
  const [showHidden, setShowHidden] = useState(false)
  const [permsOpen, setPermsOpen] = useState(false)
  const [chatOpen, setChatOpen] = useState(true)

  const boardIdNum = Number(boardId)

  const { data, isLoading } = useQuery({
    queryKey: ["task-board", boardIdNum, showHidden],
    queryFn: () => tasksApi.boards.get(boardIdNum, showHidden),
    enabled: !!boardId,
  })

  // open task from ?task= query or anchor event
  useEffect(() => {
    const q = searchParams.get("task")
    if (q) setSelectedTaskId(Number(q))
  }, [searchParams])
  useEffect(() => {
    const h = (e: Event) => setSelectedTaskId((e as CustomEvent).detail)
    window.addEventListener("open-task", h as EventListener)
    return () => window.removeEventListener("open-task", h as EventListener)
  }, [])

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
    <div className="flex h-[calc(100vh-4rem)] gap-3">
      <div className="flex flex-1 flex-col min-w-0">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
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
            <label className="flex items-center gap-1.5 text-xs"><Switch checked={showHidden} onCheckedChange={setShowHidden} /> Показывать скрытые</label>
            <Button variant="outline" size="sm" onClick={() => setPermsOpen(true)}><Shield className="size-4" /> Права</Button>
            <Button variant={chatOpen ? "secondary" : "outline"} size="sm" onClick={() => setChatOpen(!chatOpen)}><MessageSquare className="size-4" /> {chatOpen ? "Скрыть чат" : "Чат"}</Button>
            {data.can_manage_boards && (
              <>
                <Button variant="outline" size="sm" onClick={() => setAddColumnOpen(true)}><Plus className="size-4" /> Колонка</Button>
                <Button variant="outline" size="icon" onClick={() => setSettingsOpen(true)}><Settings className="size-4" /></Button>
              </>
            )}
          </div>
        </div>

        <div className="flex gap-3 overflow-x-auto pb-4 flex-1">
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
              boardId={boardIdNum}
              allColumns={data.columns}
            />
          ))}
          {!data.columns.length && (
            <Card className="w-full"><CardContent className="py-12 text-center text-sm text-muted-foreground">На доске пока нет колонок.</CardContent></Card>
          )}
        </div>
      </div>

      {chatOpen && <BoardChat boardId={boardIdNum} tasks={data.tasks} onClose={() => setChatOpen(false)} />}

      {/* Task detail по центру, чат остаётся интерактивным */}
      <TaskDetailDialog
        taskId={selectedTaskId}
        task={selectedTask}
        onClose={() => { setSelectedTaskId(null); setSearchParams(prev => { prev.delete("task"); return prev }, { replace: true }) }}
        candidates={candidates}
        canEditAll={data.can_edit_all}
        canDeleteAll={data.can_delete_all}
        currentAdminId={admin?.id}
        boardId={boardIdNum}
      />

      <BoardPermissionsDialog boardId={boardIdNum} open={permsOpen} onOpenChange={setPermsOpen} />

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
    </div>
  )
}

// ── Reused dialogs ─────────────────

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
