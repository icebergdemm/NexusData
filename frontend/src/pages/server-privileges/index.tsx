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
import { KeyRound } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface PrivGroup {
  id: number; name: string; group_key: string; description: string | null
  commands: string[]; allowed_action_ids: string[]; assignable_by_groups: number[]; member_count: number
}
interface PlayerPriv {
  id: number; steamid: string; player_name: string | null; server_group_id: number | null
  commands: string[]; allowed_action_ids: string[]; reason: string | null; server_scope: string | null
}
interface ServerPrivResponse {
  privileges: PlayerPriv[]; priv_groups: PrivGroup[]; admin_priv_groups: { id: number; name: string }[]
  page: number; total: number; total_pages: number; is_superuser: boolean
}

export function ServerPrivilegesPage() {
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const [assignForm, setAssignForm] = useState({ steamid: "", server_group_id: "none", commands: "", allowed_action_ids: "", reason: "", server_scope: "" })
  const [groupForm, setGroupForm] = useState({ name: "", group_key: "", commands: "", allowed_action_ids: "" })
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
    onSuccess: () => { toast.success("Привилегии назначены"); setAssignForm({ steamid: "", server_group_id: "none", commands: "", allowed_action_ids: "", reason: "", server_scope: "" }); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const revoke = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/server-privileges/${id}`),
    onSuccess: () => { toast.success("Привилегии отозваны"); invalidate() },
  })

  const createGroup = useMutation({
    mutationFn: () => api.post("/api/v1/admin/server-privilege-groups", {
      name: groupForm.name, group_key: groupForm.group_key,
      commands: groupForm.commands.split("\n").map((s) => s.trim()).filter(Boolean),
      allowed_action_ids: groupForm.allowed_action_ids.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean),
    }),
    onSuccess: () => { toast.success("Группа создана"); setGroupForm({ name: "", group_key: "", commands: "", allowed_action_ids: "" }); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteGroup = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/server-privilege-groups/${id}`),
    onSuccess: () => { toast.success("Группа удалена"); invalidate() },
  })

  const groupName = (id: number | null) => data?.priv_groups.find((g) => g.id === id)?.name ?? "—"

  return (
    <div>
      <PageHeader title="Серверные привилегии" description="Игровые команды и права доступа игроков на серверах" icon={KeyRound} />
      <Tabs defaultValue="players" className="space-y-4">
      <TabsList>
        <TabsTrigger value="players">Игроки</TabsTrigger>
        <TabsTrigger value="groups">Группы</TabsTrigger>
      </TabsList>

      <TabsContent value="players" className="space-y-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">Назначить привилегии</CardTitle></CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-3">
            <div><Label>SteamID</Label><Input value={assignForm.steamid} onChange={(e) => setAssignForm((f) => ({ ...f, steamid: e.target.value }))} /></div>
            <div>
              <Label>Группа</Label>
              <Select value={assignForm.server_group_id} onValueChange={(v) => setAssignForm((f) => ({ ...f, server_group_id: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Без группы</SelectItem>
                  {data?.priv_groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div><Label>Область (пусто = все)</Label><Input value={assignForm.server_scope} onChange={(e) => setAssignForm((f) => ({ ...f, server_scope: e.target.value }))} placeholder="ASPER1 / ASPER2" /></div>
            <div><Label>Команды (по одной на строку)</Label><Textarea value={assignForm.commands} onChange={(e) => setAssignForm((f) => ({ ...f, commands: e.target.value }))} rows={2} /></div>
            <div><Label>ID действий</Label><Textarea value={assignForm.allowed_action_ids} onChange={(e) => setAssignForm((f) => ({ ...f, allowed_action_ids: e.target.value }))} rows={2} /></div>
            <div><Label>Причина</Label><Input value={assignForm.reason} onChange={(e) => setAssignForm((f) => ({ ...f, reason: e.target.value }))} /></div>
            <Button disabled={!assignForm.steamid.trim()} onClick={() => assign.mutate()}>Назначить</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center gap-2 space-y-0">
            <CardTitle className="text-sm">Игроки с привилегиями ({data?.total ?? 0})</CardTitle>
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск..." className="ml-auto max-w-xs" />
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>Игрок</TableHead><TableHead>Группа</TableHead><TableHead>Область</TableHead><TableHead>Команды</TableHead><TableHead /></TableRow></TableHeader>
              <TableBody>
                {data?.privileges.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell>
                      <div className="font-medium">{p.player_name ?? "—"}</div>
                      <div className="font-mono text-xs text-muted-foreground">{p.steamid}</div>
                    </TableCell>
                    <TableCell><Badge variant="secondary">{groupName(p.server_group_id)}</Badge></TableCell>
                    <TableCell>{p.server_scope ?? "Все"}</TableCell>
                    <TableCell className="max-w-xs truncate text-xs text-muted-foreground">{p.commands.join(", ") || "—"}</TableCell>
                    <TableCell className="text-right"><Button size="sm" variant="destructive" onClick={() => revoke.mutate(p.id)}>Отозвать</Button></TableCell>
                  </TableRow>
                ))}
                {!data?.privileges.length && <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground">Нет назначений</TableCell></TableRow>}
              </TableBody>
            </Table>
            {data && data.total_pages > 1 && (
              <div className="mt-4 flex items-center justify-center gap-2">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
                <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages}</span>
                <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
              </div>
            )}
          </CardContent>
        </Card>
      </TabsContent>

      <TabsContent value="groups" className="space-y-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">Новая группа</CardTitle></CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-2">
            <div><Label>Название</Label><Input value={groupForm.name} onChange={(e) => setGroupForm((f) => ({ ...f, name: e.target.value }))} /></div>
            <div><Label>Буквенный ID</Label><Input value={groupForm.group_key} onChange={(e) => setGroupForm((f) => ({ ...f, group_key: e.target.value }))} /></div>
            <div><Label>Команды (по одной на строку)</Label><Textarea value={groupForm.commands} onChange={(e) => setGroupForm((f) => ({ ...f, commands: e.target.value }))} rows={3} /></div>
            <div><Label>ID действий</Label><Textarea value={groupForm.allowed_action_ids} onChange={(e) => setGroupForm((f) => ({ ...f, allowed_action_ids: e.target.value }))} rows={3} /></div>
            <Button disabled={!groupForm.name.trim() || !groupForm.group_key.trim()} onClick={() => createGroup.mutate()}>Создать</Button>
          </CardContent>
        </Card>

        <div className="grid gap-4 md:grid-cols-2">
          {data?.priv_groups.map((g) => (
            <Card key={g.id}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0">
                <CardTitle className="text-sm">{g.name} <span className="font-mono text-xs text-muted-foreground">({g.group_key})</span></CardTitle>
                <Badge variant="secondary">{g.member_count} игроков</Badge>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div><span className="text-muted-foreground">Команды:</span> {g.commands.join(", ") || "—"}</div>
                <div><span className="text-muted-foreground">Действия:</span> {g.allowed_action_ids.join(", ") || "—"}</div>
                <Button size="sm" variant="destructive" onClick={() => deleteGroup.mutate(g.id)}>Удалить группу</Button>
              </CardContent>
            </Card>
          ))}
        </div>
      </TabsContent>
      </Tabs>
    </div>
  )
}
