import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { UserCog } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"

const PAGES = [
  "players", "players_special", "moderation", "dispatch", "sessions", "kits", "admin",
  "audit", "punishments", "privileges", "sub_projects", "replays", "ratings", "discord_bot", "tickets",
  "documents",
]

interface AdminUserRow {
  id: number; discord_id: string; discord_username: string | null; role: string
  is_superuser: boolean; privilege_group_id: number | null; privilege_group_name: string | null
}
interface PrivilegeGroup {
  id: number; name: string; description: string | null
  permissions: { pages?: Record<string, Record<string, boolean>> } | Record<string, Record<string, boolean>>
  member_count: number
}
interface UsersResponse {
  admins: AdminUserRow[]; roles: string[]; groups: PrivilegeGroup[]
  can_edit: boolean; can_delete: boolean; is_superuser: boolean
}

function getPages(g: PrivilegeGroup): Record<string, Record<string, boolean>> {
  return (g.permissions as { pages?: Record<string, Record<string, boolean>> }).pages ?? (g.permissions as Record<string, Record<string, boolean>>) ?? {}
}

export function UsersPage() {
  const { data: me } = useCurrentAdmin()
  const [tab, setTab] = useState("users")
  const [newDiscordId, setNewDiscordId] = useState("")
  const [groupDialogOpen, setGroupDialogOpen] = useState(false)
  const [groupName, setGroupName] = useState("")
  const [groupPerms, setGroupPerms] = useState<Record<string, Record<string, boolean>>>({})
  const queryClient = useQueryClient()

  const { data } = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<UsersResponse>("/api/v1/admin/users") })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-users"] })

  const createAdmin = useMutation({
    mutationFn: () => api.post("/api/v1/admin/users", { discord_id: newDiscordId.trim(), role: "admin" }),
    onSuccess: () => { toast.success("Администратор создан"); setNewDiscordId(""); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteAdmin = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/users/${id}`),
    onSuccess: () => { toast.success("Администратор удалён"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const assignGroup = useMutation({
    mutationFn: ({ id, privilege_group_id }: { id: number; privilege_group_id: number | null }) =>
      api.post(`/api/v1/admin/users/${id}/assign-group`, { privilege_group_id }),
    onSuccess: () => { toast.success("Группа назначена"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const toggleSuperuser = useMutation({
    mutationFn: ({ id, is_superuser }: { id: number; is_superuser: boolean }) =>
      api.post(`/api/v1/admin/users/${id}/assign-group`, { is_superuser }),
    onSuccess: () => { toast.success("Обновлено"); invalidate() },
  })

  const createGroup = useMutation({
    mutationFn: () => api.post("/api/v1/admin/privilege-groups", { name: groupName, permissions: { pages: groupPerms } }),
    onSuccess: () => { toast.success("Группа создана"); setGroupDialogOpen(false); setGroupName(""); setGroupPerms({}); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteGroup = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/privilege-groups/${id}`),
    onSuccess: () => { toast.success("Группа удалена"); invalidate() },
  })

  const togglePerm = (page: string, action: string) => {
    setGroupPerms((prev) => ({
      ...prev,
      [page]: { ...prev[page], [action]: !prev[page]?.[action] },
    }))
  }

  return (
    <div>
      <PageHeader title="Пользователи Nexus Data" description="Администраторы панели и группы прав доступа" icon={UserCog} />
      <Tabs value={tab} onValueChange={setTab} className="mb-4">
        <TabsList>
          <TabsTrigger value="users">Пользователи</TabsTrigger>
          <TabsTrigger value="groups">Группы прав</TabsTrigger>
        </TabsList>
      </Tabs>

      {tab === "users" && (
        <div className="space-y-4">
          {data?.can_edit && (
            <Card>
              <CardContent className="flex items-end gap-2 pt-6">
                <div className="flex-1">
                  <Label>Discord ID нового администратора</Label>
                  <Input value={newDiscordId} onChange={(e) => setNewDiscordId(e.target.value)} />
                </div>
                <Button disabled={!newDiscordId.trim()} onClick={() => createAdmin.mutate()}>Добавить</Button>
              </CardContent>
            </Card>
          )}
          <Card>
            <CardContent className="pt-6">
              <Table>
                <TableHeader>
                  <TableRow><TableHead>Админ</TableHead><TableHead>Группа прав</TableHead><TableHead>Суперпользователь</TableHead>{data?.can_delete && <TableHead />}</TableRow>
                </TableHeader>
                <TableBody>
                  {data?.admins.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell>
                        <div className="font-medium">{a.discord_username}</div>
                        <div className="font-mono text-xs text-muted-foreground">{a.discord_id}</div>
                      </TableCell>
                      <TableCell>
                        {data.can_edit ? (
                          <Select
                            value={a.privilege_group_id ? String(a.privilege_group_id) : "none"}
                            onValueChange={(v) => assignGroup.mutate({ id: a.id, privilege_group_id: v === "none" ? null : Number(v) })}
                          >
                            <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="none">Без группы</SelectItem>
                              {data.groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>)}
                            </SelectContent>
                          </Select>
                        ) : (a.privilege_group_name ?? "—")}
                      </TableCell>
                      <TableCell>
                        {a.is_superuser ? <Badge>Да</Badge> : <Badge variant="secondary">Нет</Badge>}
                        {me?.is_superuser && a.id !== me.id && data?.can_edit && (
                          <Button size="sm" variant="ghost" className="ml-2" onClick={() => toggleSuperuser.mutate({ id: a.id, is_superuser: !a.is_superuser })}>
                            {a.is_superuser ? "Снять" : "Выдать"}
                          </Button>
                        )}
                      </TableCell>
                      {data?.can_delete && (
                        <TableCell className="text-right">
                          {a.id !== me?.id && <Button size="sm" variant="destructive" onClick={() => deleteAdmin.mutate(a.id)}>Удалить</Button>}
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "groups" && (
        <div className="space-y-4">
          {data?.can_edit && (
            <Dialog open={groupDialogOpen} onOpenChange={setGroupDialogOpen}>
              <DialogTrigger asChild><Button>+ Новая группа прав</Button></DialogTrigger>
              <DialogContent className="max-w-3xl">
                <DialogHeader>
                  <DialogTitle>Новая группа прав</DialogTitle>
                  <DialogDescription>Настройте название и права доступа для новой группы администраторов.</DialogDescription>
                </DialogHeader>
                <div className="space-y-3">
                  <div><Label>Название</Label><Input value={groupName} onChange={(e) => setGroupName(e.target.value)} /></div>
                  <div className="max-h-96 overflow-y-auto">
                    <Table>
                      <TableHeader><TableRow><TableHead>Страница</TableHead><TableHead>Просмотр</TableHead><TableHead>Изменение</TableHead><TableHead>Удаление</TableHead></TableRow></TableHeader>
                      <TableBody>
                        {PAGES.map((p) => (
                          <TableRow key={p}>
                            <TableCell className="font-mono text-xs">{p}</TableCell>
                            {["view", "edit", "delete"].map((action) => (
                              <TableCell key={action}>
                                <input type="checkbox" checked={!!groupPerms[p]?.[action]} onChange={() => togglePerm(p, action)} />
                              </TableCell>
                            ))}
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </div>
                <DialogFooter><Button disabled={!groupName.trim()} onClick={() => createGroup.mutate()}>Создать</Button></DialogFooter>
              </DialogContent>
            </Dialog>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            {data?.groups.map((g) => (
              <Card key={g.id}>
                <CardHeader className="flex flex-row items-center justify-between space-y-0">
                  <CardTitle className="text-sm">{g.name}</CardTitle>
                  <Badge variant="secondary">{g.member_count} участников</Badge>
                </CardHeader>
                <CardContent className="space-y-2">
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(getPages(g)).filter(([, v]) => v.view || v.edit || v.delete).map(([page, v]) => (
                      <Badge key={page} variant="outline" className="text-xs">
                        {page}: {[v.view && "П", v.edit && "И", v.delete && "У"].filter(Boolean).join("/")}
                      </Badge>
                    ))}
                  </div>
                  {data.can_edit && (
                    <Button size="sm" variant="destructive" onClick={() => deleteGroup.mutate(g.id)}>Удалить группу</Button>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
