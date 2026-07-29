import { useState } from "react"
import { Link } from "react-router-dom"
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
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { UserCog, ShieldCheck, KeyRound } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { LoginKeysDialog } from "./LoginKeysDialog"
import { useCurrentAdmin, usePermissionRegistry } from "@/lib/auth"
import { PermissionMatrixEditor, type PermissionValue } from "./PermissionMatrixEditor"

interface LinkedPlayer { player_id: number; player_name: string; auto: boolean }
interface AdminUserRow {
  id: number; discord_id: string; discord_username: string | null; role: string
  is_superuser: boolean; privilege_group_id: number | null; privilege_group_name: string | null
  individual_permissions: { pages?: PermissionValue } | PermissionValue | null
  linked_player: LinkedPlayer | null
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

function unwrapPages(raw: { pages?: PermissionValue } | PermissionValue | null | undefined): PermissionValue {
  if (!raw) return {}
  return (raw as { pages?: PermissionValue }).pages ?? (raw as PermissionValue) ?? {}
}

export function UsersPage() {
  const { data: me } = useCurrentAdmin()
  const { data: registryData } = usePermissionRegistry()
  const [tab, setTab] = useState("users")
  const [newDiscordId, setNewDiscordId] = useState("")
  const [groupDialogOpen, setGroupDialogOpen] = useState(false)
  const [editingGroupId, setEditingGroupId] = useState<number | null>(null)
  const [groupName, setGroupName] = useState("")
  const [groupPerms, setGroupPerms] = useState<PermissionValue>({})
  const [indivDialogOpen, setIndivDialogOpen] = useState(false)
  // Ключи входа без Discord — только для суперпользователя (см. LoginKeysDialog).
  const [keysTarget, setKeysTarget] = useState<{ id: number; name: string } | null>(null)
  const [indivTarget, setIndivTarget] = useState<AdminUserRow | null>(null)
  const [indivPerms, setIndivPerms] = useState<PermissionValue>({})
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

  const updateGroup = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/privilege-groups/${editingGroupId}`, { name: groupName, permissions: { pages: groupPerms } }),
    onSuccess: () => { toast.success("Группа обновлена"); setGroupDialogOpen(false); setEditingGroupId(null); setGroupName(""); setGroupPerms({}); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const openCreateGroup = () => {
    setEditingGroupId(null)
    setGroupName("")
    setGroupPerms({})
    setGroupDialogOpen(true)
  }

  const openEditGroup = (g: PrivilegeGroup) => {
    setEditingGroupId(g.id)
    setGroupName(g.name)
    setGroupPerms(unwrapPages(g.permissions))
    setGroupDialogOpen(true)
  }

  const deleteGroup = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/privilege-groups/${id}`),
    onSuccess: () => { toast.success("Группа удалена"); invalidate() },
  })

  const openIndividualPerms = (a: AdminUserRow) => {
    setIndivTarget(a)
    setIndivPerms(unwrapPages(a.individual_permissions))
    setIndivDialogOpen(true)
  }

  const saveIndividualPerms = useMutation({
    mutationFn: () =>
      api.post(`/api/v1/admin/users/${indivTarget!.id}/assign-group`, { individual_permissions: { pages: indivPerms } }),
    onSuccess: () => { toast.success("Индивидуальные права сохранены"); setIndivDialogOpen(false); setIndivTarget(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

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
              <CardContent className="flex items-end gap-2">
                <div className="flex-1">
                  <Label className="mb-1.5 block">Discord ID нового администратора</Label>
                  <Input value={newDiscordId} onChange={(e) => setNewDiscordId(e.target.value)} />
                </div>
                <Button disabled={!newDiscordId.trim()} onClick={() => createAdmin.mutate()}>Добавить</Button>
              </CardContent>
            </Card>
          )}
          <Card>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow><TableHead>Админ</TableHead><TableHead>Игрок</TableHead><TableHead>Группа прав</TableHead><TableHead>Индивидуальные права</TableHead><TableHead>Суперпользователь</TableHead>{me?.is_superuser && <TableHead>Вход по ключу</TableHead>}{data?.can_delete && <TableHead />}</TableRow>
                </TableHeader>
                <TableBody>
                  {data?.admins.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell>
                        <div className="font-medium">{a.discord_username}</div>
                        <div className="font-mono text-xs text-muted-foreground">{a.discord_id}</div>
                      </TableCell>
                      <TableCell>
                        {a.linked_player ? (
                          <Link to={`/players/${a.linked_player.player_id}`} className="text-primary underline-offset-2 hover:underline">
                            {a.linked_player.player_name}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                        {a.linked_player?.auto && <Badge variant="outline" className="ml-1.5 text-[10px]">авто</Badge>}
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
                        ) : (a.privilege_group_name ?? "-")}
                      </TableCell>
                      <TableCell>
                        {(() => {
                          const count = Object.keys(unwrapPages(a.individual_permissions)).length
                          return (
                            <Button size="sm" variant="outline" className="gap-1.5" onClick={() => openIndividualPerms(a)} disabled={!data?.can_edit}>
                              <ShieldCheck className="size-3.5" />
                              {count > 0 ? `${count} стр.` : "Настроить"}
                            </Button>
                          )
                        })()}
                      </TableCell>
                      <TableCell>
                        {a.is_superuser ? <Badge>Да</Badge> : <Badge variant="secondary">Нет</Badge>}
                        {me?.is_superuser && a.id !== me.id && data?.can_edit && (
                          <Button size="sm" variant="ghost" className="ml-2" onClick={() => toggleSuperuser.mutate({ id: a.id, is_superuser: !a.is_superuser })}>
                            {a.is_superuser ? "Снять" : "Выдать"}
                          </Button>
                        )}
                      </TableCell>
                      {me?.is_superuser && (
                        <TableCell>
                          <Button
                            size="sm" variant="outline" className="gap-1.5"
                            onClick={() => setKeysTarget({ id: a.id, name: a.discord_username ?? a.discord_id })}
                          >
                            <KeyRound className="size-3.5" /> Ключи
                          </Button>
                        </TableCell>
                      )}
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
            <Dialog open={groupDialogOpen} onOpenChange={(o) => { setGroupDialogOpen(o); if (!o) setEditingGroupId(null) }}>
              <Button onClick={openCreateGroup}>+ Новая группа прав</Button>
              <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
                <DialogHeader>
                  <DialogTitle>{editingGroupId ? "Изменить группу прав" : "Новая группа прав"}</DialogTitle>
                  <DialogDescription>Настройте название и права доступа для группы администраторов.</DialogDescription>
                </DialogHeader>
                <div className="space-y-3">
                  <div><Label className="mb-1.5 block">Название</Label><Input value={groupName} onChange={(e) => setGroupName(e.target.value)} /></div>
                  {registryData && (
                    <PermissionMatrixEditor registry={registryData.pages} value={groupPerms} onChange={setGroupPerms} />
                  )}
                </div>
                <DialogFooter>
                  <Button
                    disabled={!groupName.trim() || createGroup.isPending || updateGroup.isPending}
                    onClick={() => (editingGroupId ? updateGroup.mutate() : createGroup.mutate())}
                  >
                    {editingGroupId ? "Сохранить" : "Создать"}
                  </Button>
                </DialogFooter>
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
                    {Object.entries(unwrapPages(g.permissions)).filter(([, v]) => Object.values(v).some(Boolean)).map(([page, v]) => (
                      <Badge key={page} variant="outline" className="text-xs">
                        {page}: {Object.entries(v).filter(([, on]) => on).map(([action]) => action).join("/")}
                      </Badge>
                    ))}
                  </div>
                  {data.can_edit && (
                    <div className="flex gap-1.5">
                      <Button size="sm" variant="outline" onClick={() => openEditGroup(g)}>Изменить</Button>
                      <Button size="sm" variant="destructive" onClick={() => deleteGroup.mutate(g.id)}>Удалить группу</Button>
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      <Dialog open={indivDialogOpen} onOpenChange={(o) => { setIndivDialogOpen(o); if (!o) setIndivTarget(null) }}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Индивидуальные права — {indivTarget?.discord_username ?? indivTarget?.discord_id}</DialogTitle>
            <DialogDescription>
              Переопределяют права группы для этого конкретного администратора. Отмеченное здесь действует поверх группы;
              оставьте всё пустым, чтобы использовать только групповые права.
            </DialogDescription>
          </DialogHeader>
          {registryData && (
            <PermissionMatrixEditor registry={registryData.pages} value={indivPerms} onChange={setIndivPerms} />
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setIndivDialogOpen(false)}>Отмена</Button>
            <Button disabled={saveIndividualPerms.isPending} onClick={() => saveIndividualPerms.mutate()}>Сохранить</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {keysTarget && (
        <LoginKeysDialog
          key={keysTarget.id}
          adminId={keysTarget.id}
          adminName={keysTarget.name}
          open={!!keysTarget}
          onOpenChange={(o) => !o && setKeysTarget(null)}
        />
      )}
    </div>
  )
}
