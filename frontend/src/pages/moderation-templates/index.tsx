import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { FileText, Pencil, Plus, Trash2 } from "lucide-react"
import { api } from "@/lib/api"
import { canDo, useCurrentAdmin } from "@/lib/auth"
import type { ModerationTemplate, ModerationTemplateType } from "@/components/layout/ModerationTemplatePicker"
import { PageHeader } from "@/components/layout/PageHeader"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"

const TYPES: { value: ModerationTemplateType; label: string }[] = [
  { value: "message", label: "Сообщение" },
  { value: "warning", label: "Предупреждение" },
  { value: "kick", label: "Кик" },
  { value: "ban", label: "Бан" },
]

const EMPTY_FORM = { action_type: "message" as ModerationTemplateType, name: "", content: "" }

export function ModerationTemplatesPage() {
  const { data: admin, isLoading: isAdminLoading } = useCurrentAdmin()
  const canManage = canDo(admin, "moderation_templates", "manage")
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ModerationTemplate | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ModerationTemplate | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)

  const { data, isLoading } = useQuery({
    queryKey: ["moderation-templates"],
    queryFn: () => api.get<{ templates: ModerationTemplate[] }>("/api/v1/admin/moderation-templates"),
    enabled: canManage,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["moderation-templates"] })

  const saveMutation = useMutation({
    mutationFn: () => editing
      ? api.patch(`/api/v1/admin/moderation-templates/${editing.id}`, { name: form.name, content: form.content })
      : api.post("/api/v1/admin/moderation-templates", form),
    onSuccess: () => {
      toast.success(editing ? "Шаблон обновлён" : "Шаблон создан")
      setDialogOpen(false)
      setEditing(null)
      setForm(EMPTY_FORM)
      invalidate()
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/moderation-templates/${id}`),
    onSuccess: () => { toast.success("Шаблон удалён"); invalidate() },
    onError: (error: Error) => toast.error(error.message),
  })

  const openCreate = () => { setEditing(null); setForm(EMPTY_FORM); setDialogOpen(true) }
  const openEdit = (template: ModerationTemplate) => {
    setEditing(template)
    setForm({ action_type: template.action_type, name: template.name, content: template.content })
    setDialogOpen(true)
  }

  if (isAdminLoading) return <div className="text-sm text-muted-foreground">Загрузка…</div>
  if (!canManage) return <Card><CardContent className="py-10 text-center text-muted-foreground">Нет права «Управление шаблонами».</CardContent></Card>

  const templates = data?.templates ?? []
  return (
    <div className="space-y-4">
      <PageHeader
        title="Шаблоны модерации"
        description="Готовые тексты для сообщений и взысканий. Выбор шаблона только заполняет форму и ничего не отправляет сам."
        icon={FileText}
        actions={<Button onClick={openCreate}><Plus className="size-4" /> Создать шаблон</Button>}
      />
      <Card>
        <CardHeader><CardTitle className="text-base">Шаблоны</CardTitle></CardHeader>
        <CardContent>
          {isLoading ? <div className="py-8 text-center text-sm text-muted-foreground">Загрузка…</div> : (
            <div className="space-y-2">
              {templates.map((template) => (
                <div key={template.id} className="flex flex-wrap items-start justify-between gap-3 rounded-md border p-3">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{template.action_label}</Badge>
                      <span className="font-medium">{template.name}</span>
                    </div>
                    <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{template.content}</p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button size="sm" variant="outline" onClick={() => openEdit(template)}><Pencil className="size-3.5" /> Изменить</Button>
                    <Button size="icon-sm" variant="ghost" className="text-destructive" onClick={() => setDeleteTarget(template)} disabled={deleteMutation.isPending} title="Удалить"><Trash2 className="size-4" /></Button>
                  </div>
                </div>
              ))}
              {!templates.length && <div className="py-8 text-center text-sm text-muted-foreground">Шаблонов пока нет.</div>}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Изменить шаблон" : "Создать шаблон"}</DialogTitle>
            <DialogDescription>Шаблон будет доступен модераторам, у которых есть право на соответствующее действие.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div><Label className="mb-1.5 block">Тип действия</Label><Select value={form.action_type} onValueChange={(value: ModerationTemplateType) => setForm((current) => ({ ...current, action_type: value }))} disabled={!!editing}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{TYPES.map((type) => <SelectItem key={type.value} value={type.value}>{type.label}</SelectItem>)}</SelectContent></Select></div>
            <div><Label className="mb-1.5 block">Название</Label><Input maxLength={128} value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="Например: Нарушение чата" /></div>
            <div><Label className="mb-1.5 block">Текст</Label><Textarea maxLength={form.action_type === "message" ? 2000 : 1000} value={form.content} onChange={(event) => setForm((current) => ({ ...current, content: event.target.value }))} placeholder="Текст, который подставится в форму" /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setDialogOpen(false)}>Отмена</Button><Button disabled={!form.name.trim() || !form.content.trim() || saveMutation.isPending} onClick={() => saveMutation.mutate()}>Сохранить</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={deleteTarget != null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Удалить шаблон?"
        description={deleteTarget ? `«${deleteTarget.name}» будет удалён для всех модераторов.` : undefined}
        onConfirm={() => { if (deleteTarget) deleteMutation.mutate(deleteTarget.id); setDeleteTarget(null) }}
        confirmDisabled={deleteMutation.isPending}
      />
    </div>
  )
}
