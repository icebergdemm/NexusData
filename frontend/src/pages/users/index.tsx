import { useEffect, useState } from "react"
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
import { UserCog, ShieldCheck, KeyRound, AlertTriangle } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
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
  permission_summary: {
    department_count: number
    conflict_count: number
    effective: Record<string, boolean>
  }
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

interface PermissionDecision {
  value: boolean
  state: "allow" | "deny" | "unset"
  sources: Array<{ kind: string; value: boolean; [key: string]: unknown }>
}
interface AuthorizationTrace {
  admin: { id: number; discord_id: string; discord_username: string | null; role: string; is_superuser: boolean; policy_mode: string }
  effective: { pages: Record<string, Record<string, PermissionDecision>> }
  sources: { individual: unknown; group: { id: number; name: string; permissions: unknown } | null; department_site: unknown[] }
  departments: Array<{ id: number; name: string; held_level_keys: string[]; is_head: boolean; is_deputy_head: boolean; is_auto: boolean; levels: Array<{ id: number; key: string; name: string; held: boolean }> }>
  linked_player: { player_id: number; name: string } | null
  login_keys: Array<{ id: number; prefix: string; label: string | null; revoked: boolean; expires_at: string | null; use_count: number }>
  resource_acl: { documents: unknown[]; task_boards: unknown[] }
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
  const [search, setSearch] = useState("")
  const [selectedAdminId, setSelectedAdminId] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const { data } = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<UsersResponse>("/api/v1/admin/users") })

  const filteredAdmins = (data?.admins ?? []).filter((a) => {
    if (!search.trim()) return true
    const q = search.trim().toLowerCase()
    return (
      (a.discord_username ?? "").toLowerCase().includes(q) ||
      (a.discord_id ?? "").toLowerCase().includes(q) ||
      (a.privilege_group_name ?? "").toLowerCase().includes(q) ||
      (a.linked_player?.player_name ?? "").toLowerCase().includes(q) ||
      (a.role ?? "").toLowerCase().includes(q) ||
      (a.is_superuser ? "суперпользователь superuser" : "").includes(q)
    )
  })

  useEffect(() => {
    if (!filteredAdmins.length) {
      setSelectedAdminId(null)
    } else if (!filteredAdmins.some((admin) => admin.id === selectedAdminId)) {
      setSelectedAdminId(filteredAdmins[0].id)
    }
  }, [filteredAdmins, selectedAdminId])

  const selectedAdmin = (data?.admins ?? []).find((admin) => admin.id === selectedAdminId) ?? null
  const { data: authorization } = useQuery({
    queryKey: ["admin-authorization", selectedAdminId],
    queryFn: () => api.get<AuthorizationTrace>(`/api/v1/admin/users/${selectedAdminId}/authorization`),
    enabled: selectedAdminId !== null,
  })

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
          <div className="flex items-center gap-2">
            <SearchInput value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по имени, Discord ID, группе, игроку..." className="max-w-sm" />
            {search && <span className="text-xs text-muted-foreground">Найдено {filteredAdmins.length} из {data?.admins.length ?? 0}</span>}
          </div>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)]">
          <Card>
            <CardContent>
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow><TableHead>Админ</TableHead><TableHead>Игрок</TableHead><TableHead>Группа</TableHead><TableHead>Департаменты</TableHead><TableHead>Конфликты</TableHead><TableHead>Итог</TableHead><TableHead>Индивидуальные</TableHead><TableHead>Superuser</TableHead>{me?.is_superuser && <TableHead>Ключи</TableHead>}{data?.can_delete && <TableHead />}</TableRow>
                </TableHeader>
                <TableBody>
                  {filteredAdmins.map((a) => (
                    <TableRow key={a.id} onClick={() => setSelectedAdminId(a.id)} className={selectedAdminId === a.id ? "bg-primary/10" : "cursor-pointer"}>
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
                        {data?.can_edit ? (
                          <Select
                            value={a.privilege_group_id ? String(a.privilege_group_id) : "none"}
                            onValueChange={(v) => assignGroup.mutate({ id: a.id, privilege_group_id: v === "none" ? null : Number(v) })}
                          >
                            <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="none">Без группы</SelectItem>
                              {data?.groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>)}
                            </SelectContent>
                          </Select>
                        ) : (a.privilege_group_name ?? "-")}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{a.permission_summary.department_count}</Badge>
                      </TableCell>
                      <TableCell>
                        {a.permission_summary.conflict_count > 0 ? (
                          <Badge variant="destructive" className="gap-1"><AlertTriangle className="size-3" />{a.permission_summary.conflict_count}</Badge>
                        ) : <Badge variant="secondary">0</Badge>}
                      </TableCell>
                      <TableCell className="space-x-1">
                        {Object.entries(a.permission_summary.effective).map(([page, allowed]) => (
                          <Badge key={page} variant={allowed ? "default" : "secondary"} className="text-[10px]">{page}:{allowed ? "да" : "нет"}</Badge>
                        ))}
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
              </div>
            </CardContent>
          </Card>
          <AuthorizationInspector
            admin={selectedAdmin}
            trace={authorization}
            registry={registryData?.pages}
            canEdit={!!data?.can_edit}
            onEditIndividual={selectedAdmin ? () => openIndividualPerms(selectedAdmin) : undefined}
            onOpenKeys={selectedAdmin ? () => setKeysTarget({ id: selectedAdmin.id, name: selectedAdmin.discord_username ?? selectedAdmin.discord_id }) : undefined}
          />
          </div>
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
                    {Object.entries(unwrapPages(g.permissions)).filter(([, v]) => Object.values(v).some((state) => state !== undefined)).map(([page, v]) => (
                      <Badge key={page} variant="outline" className="text-xs">
                        {page}: {Object.entries(v).filter(([, state]) => state !== undefined).map(([action, state]) => `${action}:${state ? "да" : "нет"}`).join("/")}
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
              Здесь можно явно разрешить или запретить действие для этого администратора.
              «—» оставляет решение другим источникам; в режиме deny_v2 любой явный запрет побеждает разрешение.
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

function AuthorizationInspector({ admin, trace, registry, canEdit, onEditIndividual, onOpenKeys }: {
  admin: AdminUserRow | null
  trace: AuthorizationTrace | undefined
  registry: Record<string, { label: string; actions: Array<{ key: string; label: string }> }> | undefined
  canEdit: boolean
  onEditIndividual?: () => void
  onOpenKeys?: () => void
}) {
  if (!admin) {
    return <Card><CardContent className="flex min-h-64 items-center justify-center text-sm text-muted-foreground">Выберите администратора</CardContent></Card>
  }
  if (!trace) {
    return <Card><CardContent className="min-h-64 p-4 text-sm text-muted-foreground">Загрузка полного дерева прав…</CardContent></Card>
  }

  const sourceLabel = (source: { kind: string; [key: string]: unknown }) => {
    if (source.kind === "individual") return "Индивидуально"
    if (source.kind === "group") return `Группа: ${String(source.group_name ?? "без названия")}`
    if (source.kind === "department_access_level") return `${String(source.department_name ?? "Департамент")} → ${String(source.level_name ?? source.level_key)}`
    if (source.kind === "department_site") return "Правило отдела"
    return "Базовое правило"
  }

  const conflictCount = Object.values(trace.effective.pages).reduce(
    (total, actions) => total + Object.values(actions).filter((decision) => {
      const values = new Set(decision.sources.map((source) => source.value))
      return values.has(true) && values.has(false)
    }).length,
    0,
  )

  return (
    <Card className="min-w-0">
      <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
        <div>
          <CardTitle className="text-base">Полный аудит прав</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">{admin.discord_username ?? admin.discord_id} · режим {trace.admin.policy_mode}</p>
        </div>
        <div className="flex gap-1.5">
          {canEdit && <Button size="sm" variant="outline" onClick={onEditIndividual}><ShieldCheck className="mr-1 size-3.5" />Индивидуальные</Button>}
          {onOpenKeys && <Button size="sm" variant="outline" onClick={onOpenKeys}><KeyRound className="mr-1 size-3.5" />Ключи</Button>}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="grid gap-2 rounded-md border p-3 sm:grid-cols-2">
          <div><span className="text-muted-foreground">Группа:</span> {trace.sources.group?.name ?? "Без группы"}</div>
          <div><span className="text-muted-foreground">Игрок:</span> {trace.linked_player ? <Link className="text-primary hover:underline" to={`/players/${trace.linked_player.player_id}`}>{trace.linked_player.name}</Link> : "не привязан"}</div>
          <div><span className="text-muted-foreground">Департаментов:</span> {trace.departments.length}</div>
          <div><span className="text-muted-foreground">Login keys:</span> {trace.login_keys.length}</div>
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="font-medium">Effective page/action permissions</h3>
            <div className="flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground">
              <span>✓ разрешено · × запрещено · — не задано</span>
              {conflictCount > 0 && <span className="flex items-center gap-1 text-destructive"><AlertTriangle className="size-3" />Конфликт источников: {conflictCount}</span>}
            </div>
          </div>
          <div className="max-h-[48vh] space-y-1.5 overflow-y-auto pr-1">
            {Object.entries(trace.effective.pages).map(([page, actions]) => (
              <details key={page} className="rounded-md border px-3 py-2">
                <summary className="cursor-pointer list-none font-medium">{registry?.[page]?.label ?? page}</summary>
                <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                  {Object.entries(actions).map(([action, decision]) => (
                    <div key={action} className="rounded border bg-muted/20 px-2 py-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs">{registry?.[page]?.actions.find((item) => item.key === action)?.label ?? action}</span>
                        <Badge variant={decision.state === "allow" ? "default" : decision.state === "deny" ? "destructive" : "secondary"}>{decision.state}</Badge>
                      </div>
                      <div className="mt-1 text-[10px] text-muted-foreground">
                        {decision.sources.length ? decision.sources.map((source, index) => <span key={`${source.kind}-${index}`} className="mr-1.5">{sourceLabel(source)}</span>) : "нет источника"}
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </div>
        </div>

        <div>
          <h3 className="mb-2 font-medium">Департаменты и уровни</h3>
          {trace.departments.length ? trace.departments.map((department) => (
            <div key={department.id} className="mb-1.5 rounded-md border px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <Link className="font-medium text-primary hover:underline" to={`/departments/${department.id}`}>{department.name}</Link>
                <span className="text-xs text-muted-foreground">{department.is_head ? "глава" : department.is_deputy_head ? "заместитель" : "участник"}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1">{department.levels.filter((level) => level.held).map((level) => <Link key={level.id} to={`/departments/${department.id}#level-${level.id}`}><Badge variant="outline" className="cursor-pointer hover:bg-accent">{level.name}</Badge></Link>)}</div>
            </div>
          )) : <p className="text-xs text-muted-foreground">Нет членства в департаментах.</p>}
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-md border p-3"><div className="font-medium">Документные ACL</div><div className="mt-1 text-xs text-muted-foreground">{trace.resource_acl.documents.length} применимых правил · view/comment/edit</div></div>
          <div className="rounded-md border p-3"><div className="font-medium">ACL досок задач</div><div className="mt-1 text-xs text-muted-foreground">{trace.resource_acl.task_boards.length} применимых правил</div></div>
        </div>
      </CardContent>
    </Card>
  )
}
