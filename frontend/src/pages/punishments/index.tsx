import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Gavel, Plus } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogTrigger, DialogFooter,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface PunishmentRow {
  id: number
  player_id: number
  player_steamid: string
  player_name: string | null
  type: string
  reason: string | null
  issued_by_name: string | null
  issued_at: string | null
  expires_at: string | null
  is_active: boolean
  notes: string | null
  status: "active" | "expired" | "void"
}

interface PunishmentsResponse {
  tab: string
  rows: PunishmentRow[]
  total: number
  page: number
  pages: number
  type_labels: Record<string, string>
  punishment_types: string[]
  can_edit: boolean
  can_delete: boolean
}

const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive"> = {
  active: "destructive", expired: "secondary", void: "secondary",
}
const STATUS_LABEL: Record<string, string> = { active: "Активно", expired: "Истекло", void: "Отменено" }
const TYPE_DOT: Record<string, string> = {
  warning: "bg-warning", reprimand: "bg-info", kick: "bg-secondary-foreground/50", ban: "bg-destructive",
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "—"
}

export function PunishmentsPage() {
  const [tab, setTab] = useState("warning")
  const [page, setPage] = useState(1)
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ player_steamid: "", reason: "", expires_days: "", notes: "", server_name: "" })
  const queryClient = useQueryClient()

  const queryKey = ["punishments", tab, page]
  const { data } = useQuery({
    queryKey,
    queryFn: () => api.get<PunishmentsResponse>(`/api/v1/admin/punishments?${new URLSearchParams({ tab, page: String(page) })}`),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["punishments"] })

  const createMutation = useMutation({
    mutationFn: () => api.post("/api/v1/admin/punishments", {
      player_steamid: form.player_steamid.trim(),
      ptype: tab,
      reason: form.reason,
      expires_days: form.expires_days ? Number(form.expires_days) : null,
      notes: form.notes,
      server_name: form.server_name || null,
    }),
    onSuccess: () => {
      toast.success("Наказание выдано")
      setCreateOpen(false)
      setForm({ player_steamid: "", reason: "", expires_days: "", notes: "", server_name: "" })
      invalidate()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const voidMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/punishments/${id}/void`),
    onSuccess: () => { toast.success("Помечено неактуальным"); invalidate() },
  })

  const unbanMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/punishments/${id}/unban`, { server_name: null }),
    onSuccess: () => { toast.success("Разбан поставлен в очередь"); invalidate() },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/punishments/${id}`),
    onSuccess: () => { toast.success("Удалено"); invalidate() },
  })

  return (
    <div className="min-w-0">
      <PageHeader
        title="Наказания"
        description="Предупреждения, выговоры, кики и баны, выданные игрокам"
        icon={Gavel}
        actions={data?.can_edit && (
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button><Plus className="h-4 w-4" /> Выдать наказание</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Выдать «{data.type_labels[tab] ?? tab}»</DialogTitle>
                <DialogDescription>Наказание будет привязано к указанному SteamID и записано в историю игрока.</DialogDescription>
              </DialogHeader>
              <div className="space-y-3">
                <div>
                  <Label>SteamID</Label>
                  <Input value={form.player_steamid} onChange={(e) => setForm((f) => ({ ...f, player_steamid: e.target.value }))} />
                </div>
                <div>
                  <Label>Причина</Label>
                  <Input value={form.reason} onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))} />
                </div>
                <div>
                  <Label>Срок (дней, пусто = навсегда)</Label>
                  <Input type="number" value={form.expires_days} onChange={(e) => setForm((f) => ({ ...f, expires_days: e.target.value }))} />
                </div>
                {(tab === "ban" || tab === "kick") && (
                  <div>
                    <Label>Сервер (пусто = оба)</Label>
                    <Input value={form.server_name} onChange={(e) => setForm((f) => ({ ...f, server_name: e.target.value }))} placeholder="ASPER1 / ASPER2" />
                  </div>
                )}
                <div>
                  <Label>Внутренние заметки</Label>
                  <Textarea value={form.notes} onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))} />
                </div>
              </div>
              <DialogFooter>
                <Button disabled={!form.player_steamid.trim()} onClick={() => createMutation.mutate()}>
                  Выдать
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      />

      <Tabs value={tab} onValueChange={(v) => { setTab(v); setPage(1) }} className="mb-4">
        <TabsList>
          {(data?.punishment_types ?? ["warning", "reprimand", "kick", "ban"]).map((t) => (
            <TabsTrigger key={t} value={t} className="gap-1.5">
              <span className={`h-1.5 w-1.5 rounded-full ${TYPE_DOT[t] ?? "bg-muted-foreground"}`} />
              {data?.type_labels[t] ?? t}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <Card className="min-w-0 overflow-hidden py-0">
        <CardContent className="min-w-0 p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Игрок</TableHead>
                <TableHead>Причина</TableHead>
                <TableHead>Выдал</TableHead>
                <TableHead>Дата</TableHead>
                <TableHead>Истекает</TableHead>
                <TableHead>Статус</TableHead>
                {data?.can_edit && <TableHead className="text-right">Действия</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    <div className="font-medium">{r.player_name || "—"}</div>
                    <div className="font-mono text-xs text-muted-foreground">{r.player_steamid}</div>
                  </TableCell>
                  <TableCell className="max-w-52 truncate" title={r.reason ?? undefined}>{r.reason || "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{r.issued_by_name || "—"}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{fmtDate(r.issued_at)}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{r.expires_at ? fmtDate(r.expires_at) : "Навсегда"}</TableCell>
                  <TableCell><Badge variant={STATUS_VARIANT[r.status]}>{STATUS_LABEL[r.status]}</Badge></TableCell>
                  {data.can_edit && (
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1.5 whitespace-nowrap">
                        {r.status === "active" && r.type === "ban" && (
                          <Button size="sm" variant="outline" onClick={() => unbanMutation.mutate(r.id)}>Разбан</Button>
                        )}
                        {r.status === "active" && (
                          <Button size="sm" variant="outline" onClick={() => voidMutation.mutate(r.id)}>Отменить</Button>
                        )}
                        {data.can_delete && (
                          <Button size="sm" variant="destructive" onClick={() => deleteMutation.mutate(r.id)}>Удалить</Button>
                        )}
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
              {!data?.rows.length && (
                <TableRow><TableCell colSpan={7} className="py-10 text-center text-muted-foreground">Наказаний нет</TableCell></TableRow>
              )}
            </TableBody>
          </Table>

          {data && data.pages > 1 && (
            <div className="flex items-center justify-center gap-2 border-t p-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
              <span className="text-sm text-muted-foreground">Страница {data.page} из {data.pages}</span>
              <Button variant="outline" size="sm" disabled={page >= data.pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
