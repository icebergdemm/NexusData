import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { KanbanSquare, Plus, ListTodo, Building2 } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PageHeader } from "@/components/layout/PageHeader"
import { tasksApi } from "./api"

function CreateBoardDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ name: "", description: "", icon: "📋", color: "#1a68de", department_id: null as number | null })

  const { data: candidates } = useQuery({
    queryKey: ["tasks-assignee-candidates"],
    queryFn: () => tasksApi.assigneeCandidates(),
    enabled: open,
  })

  const createMutation = useMutation({
    mutationFn: () => tasksApi.boards.create(form),
    onSuccess: (res) => {
      toast.success("Доска создана")
      queryClient.invalidateQueries({ queryKey: ["task-boards"] })
      onOpenChange(false)
      setForm({ name: "", description: "", icon: "📋", color: "#1a68de", department_id: null })
      void res
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Новая доска задач</DialogTitle>
          <DialogDescription>Создаёт доску с одной колонкой «Новые» — остальные колонки настраиваются на самой доске.</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">Название</Label>
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="ASPER — Доска задач" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="mb-1.5 block">Иконка (эмодзи)</Label>
              <Input value={form.icon} onChange={(e) => setForm((f) => ({ ...f, icon: e.target.value }))} placeholder="📋" maxLength={8} />
            </div>
            <div>
              <Label className="mb-1.5 block">Цвет</Label>
              <Input type="color" value={form.color} onChange={(e) => setForm((f) => ({ ...f, color: e.target.value }))} className="h-9 p-1" />
            </div>
          </div>
          <div>
            <Label className="mb-1.5 block">Описание</Label>
            <Textarea rows={2} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} placeholder="Необязательно" />
          </div>
          <div>
            <Label className="mb-1.5 block">Отдел (тег, необязательно)</Label>
            <Select
              value={form.department_id ? String(form.department_id) : "none"}
              onValueChange={(v) => setForm((f) => ({ ...f, department_id: v === "none" ? null : Number(v) }))}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Без привязки к отделу</SelectItem>
                {candidates?.departments.map((d) => <SelectItem key={d.id} value={String(d.id)}>{d.icon} {d.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!form.name.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>Создать</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function TasksListPage() {
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ["task-boards"],
    queryFn: () => tasksApi.boards.list(),
  })

  return (
    <div>
      <PageHeader
        title="Задачи"
        description="Доски задач: произвольные колонки, сроки, ответственные сотрудники/отделы"
        icon={KanbanSquare}
        actions={data?.can_manage_boards && (
          <Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Создать доску</Button>
        )}
      />

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-32" />)}
        </div>
      ) : !data?.boards.length ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {data?.can_manage_boards ? <>Пока нет ни одной доски — создайте первую.</> : "Пока нет ни одной доски задач."}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.boards.map((b) => (
            <Card
              key={b.id}
              className="cursor-pointer overflow-hidden py-0 transition-colors hover:border-primary/50"
              onClick={() => navigate(`/tasks/${b.id}`)}
            >
              <div className="h-1.5 w-full" style={{ backgroundColor: b.color || "var(--primary)" }} />
              <CardContent className="space-y-3 p-4">
                <div className="flex items-start gap-3">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-xl">
                    {b.icon || <KanbanSquare className="size-5 text-primary" />}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate font-semibold">{b.name}</div>
                    {b.description && <div className="line-clamp-2 text-xs text-muted-foreground">{b.description}</div>}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="secondary" className="gap-1"><ListTodo className="size-3" /> {b.task_count ?? 0}</Badge>
                  {b.department_name && <Badge variant="outline" className="gap-1"><Building2 className="size-3" /> {b.department_name}</Badge>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <CreateBoardDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  )
}
