import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { KeyRound, Plus } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"

interface PrivGroup {
  id: number; name: string; group_key: string; description: string | null
  commands: string[]; allowed_action_ids: string[]; assignable_by_groups: number[]; member_count: number
}
interface PlayerPriv {
  id: number; steamid: string; player_name: string | null; server_group_id: number | null
  commands: string[]; allowed_action_ids: string[]; reason: string | null; server_scope: string | null
  is_auto: boolean; auto_source_department_name: string | null
}
interface ServerPrivResponse {
  privileges: PlayerPriv[]; priv_groups: PrivGroup[]; admin_priv_groups: { id: number; name: string }[]
  page: number; total: number; total_pages: number; is_superuser: boolean
}

const EMPTY_ASSIGN_FORM = { steamid: "", server_group_id: "none", commands: "", allowed_action_ids: "", reason: "", server_scope: "" }
const EMPTY_GROUP_FORM = { name: "", group_key: "", commands: "", allowed_action_ids: "" }

export function ServerPrivilegesPage() {
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [serverTab, setServerTab] = useState<"all" | "ASPER1" | "ASPER2">("all")
  const [assignOpen, setAssignOpen] = useState(false)
  const [assignForm, setAssignForm] = useState(EMPTY_ASSIGN_FORM)
  const [editingPrivId, setEditingPrivId] = useState<number | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<PlayerPriv | null>(null)
  const [groupOpen, setGroupOpen] = useState(false)
  const [groupForm, setGroupForm] = useState(EMPTY_GROUP_FORM)
  const [editingGroupId, setEditingGroupId] = useState<number | null>(null)
  const [deleteGroupTarget, setDeleteGroupTarget] = useState<PrivGroup | null>(null)
  const queryClient = useQueryClient()

  const { data } = useQuery({
    queryKey: ["server-privileges", page, search],
    queryFn: () => api.get<ServerPrivResponse>(`/api/v1/admin/server-privileges?${new URLSearchParams({ page: String(page), search })}`),
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["server-privileges"] })

  const assign = useMutation({
    mutationFn: () => api.post("/api/v1/admin/server-privileges/assign", {
      steamid: assignForm.steamid.trim(),
      server_group_id: assignForm.server_group_id === "none" ? null : Number(assignForm.server_group_id),
      commands: assignForm.commands.split("\n").map((s) => s.trim()).filter(Boolean),
      allowed_action_ids: assignForm.allowed_action_ids.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean),
      reason: assignForm.reason || null,
      server_scope: assignForm.server_scope || null,
    }),
    onSuccess: () => {
      toast.success(editingPrivId ? "Привилегии обновлены" : "Привилегии назначены")
      setAssignOpen(false)
      setAssignForm(EMPTY_ASSIGN_FORM)
      setEditingPrivId(null)
      invalidate()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const revoke = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/server-privileges/${id}`),
    onSuccess: () => { toast.success("Привилегии отозваны"); setRevokeTarget(null); invalidate() },
  })

  const openCreatePriv = () => {
    setEditingPrivId(null)
    setAssignForm(EMPTY_ASSIGN_FORM)
    setAssignOpen(true)
  }
  const openEditPriv = (p: PlayerPriv) => {
    setEditingPrivId(p.id)
    setAssignForm({
      steamid: p.steamid,
      server_group_id: p.server_group_id ? String(p.server_group_id) : "none",
      commands: p.commands.join("\n"),
      allowed_action_ids: p.allowed_action_ids.join(", "),
      reason: p.reason ?? "",
      server_scope: p.server_scope ?? "",
    })
    setAssignOpen(true)
  }

  const createGroup = useMutation({
    mutationFn: () => api.post("/api/v1/admin/server-privilege-groups", {
      name: groupForm.name, group_key: groupForm.group_key,
      commands: groupForm.commands.split("\n").map((s) => s.trim()).filter(Boolean),
      allowed_action_ids: groupForm.allowed_action_ids.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean),
    }),
    onSuccess: () => { toast.success("Группа создана"); setGroupOpen(false); setGroupForm(EMPTY_GROUP_FORM); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const updateGroup = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/server-privilege-groups/${editingGroupId}`, {
      name: groupForm.name, group_key: groupForm.group_key,
      commands: groupForm.commands.split("\n").map((s) => s.trim()).filter(Boolean),
      allowed_action_ids: groupForm.allowed_action_ids.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean),
    }),
    onSuccess: () => { toast.success("Группа обновлена"); setGroupOpen(false); setGroupForm(EMPTY_GROUP_FORM); setEditingGroupId(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteGroup = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/server-privilege-groups/${id}`),
    onSuccess: () => { toast.success("Группа удалена"); setDeleteGroupTarget(null); invalidate() },
  })

  const openCreateGroup = () => {
    setEditingGroupId(null)
    setGroupForm(EMPTY_GROUP_FORM)
    setGroupOpen(true)
  }
  const openEditGroup = (g: PrivGroup) => {
    setEditingGroupId(g.id)
    setGroupForm({
      name: g.name, group_key: g.group_key,
      commands: g.commands.join("\n"),
      allowed_action_ids: g.allowed_action_ids.join(", "),
    })
    setGroupOpen(true)
  }

  const groupName = (id: number | null) => data?.priv_groups.find((g) => g.id === id)?.name ?? "-"

  return (
    <div>
      <PageHeader title="Серверные привилегии" description="Игровые команды и права доступа игроков на серверах" icon={KeyRound} />
      <Tabs defaultValue="players" className="space-y-4">
      <TabsList>
        <TabsTrigger value="players">Игроки</TabsTrigger>
        <TabsTrigger value="groups">Группы</TabsTrigger>
      </TabsList>

      <TabsContent value="players" className="space-y-4">
        {/* ── 2 серверных таба ── */}
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex gap-1 rounded-lg bg-muted p-1">
              {([
                { id: "all", label: "Все серверы", icon: "🌐" },
                { id: "ASPER1", label: "ASPER1", icon: "🟦" },
                { id: "ASPER2", label: "ASPER2", icon: "🟪" },
              ] as const).map((t) => {
                return (
                  <button
                    key={t.id}
                    onClick={() => setServerTab(t.id)}
                    className={`rounded-md px-4 py-1.5 text-sm font-medium transition-all ${serverTab === t.id ? "bg-background shadow text-foreground" : "text-muted-foreground hover:text-foreground"}`}
                  >
                    {t.icon} {t.label} <span className="ml-1 text-xs opacity-60">({t.id === "all" ? data?.total ?? 0 : data?.privileges.filter((p) => p.server_scope === t.id).length ?? 0})</span>
                  </button>
                )
              })}
            </div>
            <div className="flex gap-2">
              <Button onClick={() => { setEditingPrivId(null); setAssignForm({ ...EMPTY_ASSIGN_FORM, server_scope: serverTab === "all" ? "" : serverTab }); setAssignOpen(true) }} className="bg-gradient-to-r from-primary to-primary/80 shadow">
                <Plus className="size-4" /> Назначить {serverTab === "all" ? "" : `на ${serverTab}`}
              </Button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Один игрок может иметь <b>разные права на разных серверах</b> — создайте два отдельных гранта с областью ASPER1 и ASPER2. Глобальные гранты (Все) суммируются с серверными.
          </p>
        </div>

        <Card className="overflow-hidden py-0">
          <CardHeader className="flex flex-row items-center gap-2 space-y-0 border-b bg-muted/30 py-3">
            <CardTitle className="text-sm font-semibold">
              {serverTab === "all" ? `Игроки с привилегиями (${data?.total ?? 0})` : `Игроки — ${serverTab} (${data?.privileges.filter((p) => p.server_scope === serverTab).length ?? 0})`}
            </CardTitle>
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по SteamID / имени..." className="ml-auto max-w-xs" />
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader><TableRow><TableHead>Игрок</TableHead><TableHead>Группа</TableHead><TableHead>Область</TableHead><TableHead>Команды</TableHead><TableHead /></TableRow></TableHeader>
              <TableBody>
                {(serverTab === "all" ? data?.privileges : data?.privileges.filter((p) => p.server_scope === serverTab))?.map((p) => (
                  <TableRow key={p.id} className="group hover:bg-muted/50 transition-colors">
                    <TableCell>
                      <div className="font-medium group-hover:text-primary transition-colors">{p.player_name ?? "-"}</div>
                      <div className="font-mono text-xs text-muted-foreground">{p.steamid}</div>
                    </TableCell>
                    <TableCell className="space-x-1">
                      <Badge variant="secondary" className="shadow-sm">{groupName(p.server_group_id)}</Badge>
                      {p.is_auto && (
                        <Badge variant="outline" className="border-info/40 text-[10px] text-info">
                          От отдела: {p.auto_source_department_name ?? "—"}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {p.server_scope ? (
                        <Badge className={p.server_scope === "ASPER1" ? "bg-blue-500 hover:bg-blue-600" : "bg-purple-500 hover:bg-purple-600"}>{p.server_scope}</Badge>
                      ) : (
                        <Badge variant="outline">Все</Badge>
                      )}
                    </TableCell>
                    <TableCell className="max-w-xs truncate text-xs text-muted-foreground">{p.commands.join(", ") || "-"}</TableCell>
                    <TableCell className="space-x-1.5 text-right whitespace-nowrap">
                      <Button size="sm" variant="outline" onClick={() => openEditPriv(p)} className="hover:border-primary/50">Изменить</Button>
                      <Button size="sm" variant="destructive" onClick={() => setRevokeTarget(p)}>Отозвать</Button>
                    </TableCell>
                  </TableRow>
                ))}
                {!(serverTab === "all" ? data?.privileges.length : data?.privileges.filter((p) => p.server_scope === serverTab).length) && <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">Нет назначений для {serverTab === "all" ? "выбранного фильтра" : serverTab}</TableCell></TableRow>}
              </TableBody>
            </Table>
            {data && data.total_pages > 1 && serverTab === "all" && (
              <div className="flex items-center justify-center gap-2 border-t bg-muted/30 py-3">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
                <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages}</span>
                <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
              </div>
            )}
          </CardContent>
        </Card>
      </TabsContent>

      <TabsContent value="groups" className="space-y-4">
        <div className="flex justify-end">
          <Button onClick={openCreateGroup}><Plus className="size-4" /> Новая группа</Button>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {data?.priv_groups.map((g) => (
            <Card key={g.id}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0">
                <CardTitle className="text-sm">{g.name} <span className="font-mono text-xs text-muted-foreground">({g.group_key})</span></CardTitle>
                <Badge variant="secondary">{g.member_count} игроков</Badge>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div><span className="text-muted-foreground">Команды:</span> {g.commands.join(", ") || "-"}</div>
                <div><span className="text-muted-foreground">Действия:</span> {g.allowed_action_ids.join(", ") || "-"}</div>
                <div className="flex gap-1.5">
                  <Button size="sm" variant="outline" onClick={() => openEditGroup(g)}>Изменить</Button>
                  <Button size="sm" variant="destructive" onClick={() => setDeleteGroupTarget(g)}>Удалить группу</Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </TabsContent>
      </Tabs>

      <Dialog open={assignOpen} onOpenChange={setAssignOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingPrivId ? "Изменить привилегии" : "Назначить привилегии"}</DialogTitle>
            <DialogDescription>
              Игровые команды и группа привилегий для конкретного игрока по SteamID.
              У одного игрока может быть несколько грантов одновременно — по одному на
              каждую область (глобально/ASPER1/ASPER2) плюс автоматический от отдела —
              они суммируются, а не заменяют друг друга.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="mb-1.5 block">SteamID</Label>
              <Input value={assignForm.steamid} disabled={!!editingPrivId} onChange={(e) => setAssignForm((f) => ({ ...f, steamid: e.target.value }))} />
            </div>
            <div>
              <Label className="mb-1.5 block">Группа</Label>
              <Select value={assignForm.server_group_id} onValueChange={(v) => setAssignForm((f) => ({ ...f, server_group_id: v }))}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Без группы</SelectItem>
                  {data?.priv_groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="mb-1.5 block">Область</Label>
              <Select value={assignForm.server_scope || "all"} onValueChange={(v) => setAssignForm((f) => ({ ...f, server_scope: v === "all" ? "" : v }))} disabled={!!editingPrivId}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">🌐 Все серверы</SelectItem>
                  <SelectItem value="ASPER1">🟦 ASPER1</SelectItem>
                  <SelectItem value="ASPER2">🟪 ASPER2</SelectItem>
                </SelectContent>
              </Select>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {editingPrivId
                  ? "Область гранта нельзя сменить при редактировании — отзовите его и назначьте заново на нужную область."
                  : "Каждая область — отдельный грант. Назначив ASPER1, а затем отдельно ASPER2, игрок получит разные права на каждом сервере."}
              </p>
            </div>
            <div>
              <Label className="mb-1.5 block">Команды (по одной на строку)</Label>
              <Textarea value={assignForm.commands} onChange={(e) => setAssignForm((f) => ({ ...f, commands: e.target.value }))} rows={3} />
            </div>
            <div>
              <Label className="mb-1.5 block">ID действий</Label>
              <Textarea value={assignForm.allowed_action_ids} onChange={(e) => setAssignForm((f) => ({ ...f, allowed_action_ids: e.target.value }))} rows={3} />
            </div>
            <div>
              <Label className="mb-1.5 block">Причина</Label>
              <Input value={assignForm.reason} onChange={(e) => setAssignForm((f) => ({ ...f, reason: e.target.value }))} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAssignOpen(false)}>Отмена</Button>
            <Button disabled={!assignForm.steamid.trim() || assign.isPending} onClick={() => assign.mutate()}>
              {editingPrivId ? "Сохранить" : "Назначить"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={groupOpen} onOpenChange={setGroupOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingGroupId ? "Изменить группу" : "Новая группа"}</DialogTitle>
            <DialogDescription>Именованный набор команд и действий, который можно назначать игрокам целиком.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="mb-1.5 block">Название</Label>
              <Input value={groupForm.name} onChange={(e) => setGroupForm((f) => ({ ...f, name: e.target.value }))} />
            </div>
            <div>
              <Label className="mb-1.5 block">Буквенный ID</Label>
              <Input value={groupForm.group_key} onChange={(e) => setGroupForm((f) => ({ ...f, group_key: e.target.value }))} />
            </div>
            <div>
              <Label className="mb-1.5 block">Команды (по одной на строку)</Label>
              <Textarea value={groupForm.commands} onChange={(e) => setGroupForm((f) => ({ ...f, commands: e.target.value }))} rows={4} />
            </div>
            <div>
              <Label className="mb-1.5 block">ID действий</Label>
              <Textarea value={groupForm.allowed_action_ids} onChange={(e) => setGroupForm((f) => ({ ...f, allowed_action_ids: e.target.value }))} rows={4} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setGroupOpen(false)}>Отмена</Button>
            <Button
              disabled={!groupForm.name.trim() || !groupForm.group_key.trim() || createGroup.isPending || updateGroup.isPending}
              onClick={() => (editingGroupId ? updateGroup.mutate() : createGroup.mutate())}
            >
              {editingGroupId ? "Сохранить" : "Создать"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={revokeTarget != null}
        onOpenChange={(o) => !o && setRevokeTarget(null)}
        title="Отозвать привилегии?"
        description={revokeTarget ? `Привилегии игрока «${revokeTarget.player_name ?? revokeTarget.steamid}» будут отозваны.` : undefined}
        confirmLabel="Отозвать"
        onConfirm={() => revokeTarget && revoke.mutate(revokeTarget.id)}
      />
      <ConfirmDialog
        open={deleteGroupTarget != null}
        onOpenChange={(o) => !o && setDeleteGroupTarget(null)}
        title="Удалить группу?"
        description={deleteGroupTarget ? `Группа «${deleteGroupTarget.name}» будет удалена. У игроков этой группы останутся только индивидуальные команды.` : undefined}
        onConfirm={() => deleteGroupTarget && deleteGroup.mutate(deleteGroupTarget.id)}
      />
    </div>
  )
}
