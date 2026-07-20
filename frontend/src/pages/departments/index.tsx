import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Building2, Plus, Users2, Crown } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { PageHeader } from "@/components/layout/PageHeader"
import { useCurrentAdmin, canDo } from "@/lib/auth"
import { departmentsApi, OPTIONAL_MODULE_KEYS, MODULE_LABELS, type CreateDepartmentBody, type DepartmentModule } from "./api"

function CreateDepartmentDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<CreateDepartmentBody>({ name: "", enabled_modules: [] })

  const createMutation = useMutation({
    mutationFn: () => departmentsApi.create(form),
    onSuccess: () => {
      toast.success("Отдел создан")
      queryClient.invalidateQueries({ queryKey: ["departments"] })
      onOpenChange(false)
      setForm({ name: "", enabled_modules: [] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const toggleModule = (mod: DepartmentModule, checked: boolean) => {
    setForm((f) => ({
      ...f,
      enabled_modules: checked
        ? [...(f.enabled_modules ?? []), mod]
        : (f.enabled_modules ?? []).filter((m) => m !== mod),
    }))
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Новый отдел</DialogTitle>
          <DialogDescription>Создаёт рабочее пространство отдела. Роли Discord и модули можно донастроить позже.</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">Название</Label>
            <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Медицинская служба" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="mb-1.5 block">Иконка (эмодзи)</Label>
              <Input value={form.icon ?? ""} onChange={(e) => setForm((f) => ({ ...f, icon: e.target.value }))} placeholder="🩺" maxLength={8} />
            </div>
            <div>
              <Label className="mb-1.5 block">Цвет</Label>
              <Input type="color" value={form.color ?? "#1a68de"} onChange={(e) => setForm((f) => ({ ...f, color: e.target.value }))} className="h-9 p-1" />
            </div>
          </div>
          <div>
            <Label className="mb-1.5 block">Описание</Label>
            <Textarea rows={3} value={form.description ?? ""} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} placeholder="Чем занимается отдел..." />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="mb-1.5 block">ID базовой роли Discord</Label>
              <Input value={form.base_discord_role_id ?? ""} onChange={(e) => setForm((f) => ({ ...f, base_discord_role_id: e.target.value }))} placeholder="123456789012345678" />
            </div>
            <div>
              <Label className="mb-1.5 block">Название роли</Label>
              <Input value={form.base_discord_role_name ?? ""} onChange={(e) => setForm((f) => ({ ...f, base_discord_role_name: e.target.value }))} placeholder="Сотрудник мед. службы" />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Обладатели этой роли на Discord-сервере будут автоматически добавлены в отдел фоновым воркером (модуль синхронизации ролей подключается отдельно).
          </p>

          <div>
            <Label className="mb-1.5 block">Модули</Label>
            <div className="grid grid-cols-2 gap-2">
              {OPTIONAL_MODULE_KEYS.map((mod) => (
                <label key={mod} className="flex items-center gap-2 rounded-md border px-2.5 py-2 text-sm">
                  <Checkbox
                    checked={(form.enabled_modules ?? []).includes(mod)}
                    onCheckedChange={(checked) => toggleModule(mod, checked === true)}
                  />
                  {MODULE_LABELS[mod]}
                </label>
              ))}
            </div>
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

export function DepartmentsPage() {
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)
  const { data: admin } = useCurrentAdmin()
  const seesAllDepartments = !!admin?.is_superuser || canDo(admin, "departments", "view_all")

  const { data, isLoading } = useQuery({
    queryKey: ["departments"],
    queryFn: () => departmentsApi.list(),
  })

  return (
    <div>
      <PageHeader
        title="Отделы"
        description="Рабочие пространства отделов: документация, состав, права доступа"
        icon={Building2}
        actions={data?.can_create && (
          <Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Создать отдел</Button>
        )}
      />

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-40" />)}
        </div>
      ) : !data?.departments.length ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {seesAllDepartments
              ? <>Пока нет ни одного отдела{data?.can_create && " — создайте первый"}.</>
              : "Вы пока не состоите ни в одном отделе."}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.departments.map((d) => (
            <Card
              key={d.id}
              className="cursor-pointer overflow-hidden py-0 transition-colors hover:border-primary/50"
              onClick={() => navigate(`/departments/${d.id}`)}
            >
              <div className="h-1.5 w-full" style={{ backgroundColor: d.color || "var(--primary)" }} />
              <CardContent className="space-y-3 p-4">
                <div className="flex items-start gap-3">
                  <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-xl">
                    {d.icon || <Building2 className="size-5 text-primary" />}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate font-semibold">{d.name}</div>
                    {d.description && <div className="line-clamp-2 text-xs text-muted-foreground">{d.description}</div>}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="secondary" className="gap-1"><Users2 className="size-3" /> {d.member_count}</Badge>
                  {d.head && (
                    <Badge variant="outline" className="gap-1"><Crown className="size-3" /> {d.head.admin_name ?? "-"}</Badge>
                  )}
                  {d.is_head && <Badge className="bg-primary/15 text-primary">Вы — глава</Badge>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <CreateDepartmentDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  )
}
