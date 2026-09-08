import { useMemo, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, ChevronRight, UserCog } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { api } from "@/lib/api"
import { usePermissionRegistry } from "@/lib/auth"
import { PermissionMatrixEditor, type PermissionValue } from "./PermissionMatrixEditor"
import type { PrivilegeGroup, UsersResponse } from "./types"
import { unwrapPages } from "./types"

export function UsersPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { data: registryData } = usePermissionRegistry()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState(() => searchParams.get("tab") === "groups" ? "groups" : "users")
  const [newDiscordId, setNewDiscordId] = useState("")
  const [search, setSearch] = useState("")
  const [groupFilter, setGroupFilter] = useState("all")
  const [departmentFilter, setDepartmentFilter] = useState("all")
  const [conflictFilter, setConflictFilter] = useState("all")
  const [groupDialogOpen, setGroupDialogOpen] = useState(false)
  const [editingGroupId, setEditingGroupId] = useState<number | null>(null)
  const [groupName, setGroupName] = useState("")
  const [groupPerms, setGroupPerms] = useState<PermissionValue>({})

  const { data, isLoading } = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<UsersResponse>("/api/v1/admin/users") })

  const filteredAdmins = useMemo(() => {
    const query = search.trim().toLowerCase()
    return (data?.admins ?? []).filter((admin) => {
      const matchesSearch = !query || [
        admin.discord_username, admin.discord_id, admin.privilege_group_name,
        admin.linked_player?.player_name, admin.role,
        admin.is_superuser ? "superuser суперпользователь" : "",
      ].some((value) => (value ?? "").toLowerCase().includes(query))
      const matchesGroup = groupFilter === "all"
        || (groupFilter === "none" ? admin.privilege_group_id === null : admin.privilege_group_id === Number(groupFilter))
      const matchesDepartments = departmentFilter === "all"
        || (departmentFilter === "with" ? admin.permission_summary.department_count > 0 : admin.permission_summary.department_count === 0)
      const matchesConflicts = conflictFilter === "all"
        || (conflictFilter === "with" ? admin.permission_summary.conflict_count > 0 : admin.permission_summary.conflict_count === 0)
      return matchesSearch && matchesGroup && matchesDepartments && matchesConflicts
    })
  }, [data?.admins, search, groupFilter, departmentFilter, conflictFilter])

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-users"] })
  const closeGroupDialog = () => {
    setGroupDialogOpen(false)
    setEditingGroupId(null)
    setGroupName("")
    setGroupPerms({})
  }

  const createAdmin = useMutation({
    mutationFn: () => api.post("/api/v1/admin/users", { discord_id: newDiscordId.trim(), role: "admin" }),
    onSuccess: () => { toast.success("Администратор создан"); setNewDiscordId(""); invalidate() },
    onError: (error: Error) => toast.error(error.message),
  })
  const createGroup = useMutation({
    mutationFn: () => api.post("/api/v1/admin/privilege-groups", { name: groupName.trim(), permissions: { pages: groupPerms } }),
    onSuccess: () => { toast.success("Группа создана"); closeGroupDialog(); invalidate() },
    onError: (error: Error) => toast.error(error.message),
  })
  const updateGroup = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/privilege-groups/${editingGroupId}`, { name: groupName.trim(), permissions: { pages: groupPerms } }),
    onSuccess: () => { toast.success("Группа обновлена"); closeGroupDialog(); invalidate() },
    onError: (error: Error) => toast.error(error.message),
  })
  const deleteGroup = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/privilege-groups/${id}`),
    onSuccess: () => { toast.success("Группа удалена"); invalidate() },
    onError: (error: Error) => toast.error(error.message),
  })

  const resetFilters = () => {
    setSearch("")
    setGroupFilter("all")
    setDepartmentFilter("all")
    setConflictFilter("all")
  }
  const openCreateGroup = () => {
    setEditingGroupId(null); setGroupName(""); setGroupPerms({}); setGroupDialogOpen(true)
  }
  const openEditGroup = (group: PrivilegeGroup) => {
    setEditingGroupId(group.id); setGroupName(group.name); setGroupPerms(unwrapPages(group.permissions)); setGroupDialogOpen(true)
  }

  return (
    <div>
      <PageHeader title="Пользователи Nexus Data" description="Администраторы панели и группы прав доступа" icon={UserCog} />
      <Tabs value={tab} onValueChange={setTab} className="mb-4">
        <TabsList><TabsTrigger value="users">Администраторы</TabsTrigger><TabsTrigger value="groups">Группы прав</TabsTrigger></TabsList>
      </Tabs>

      {tab === "users" && (
        <div className="space-y-4">
          <Card>
            <CardContent className="flex flex-wrap items-end gap-2">
              <div className="min-w-64 flex-1">
                <Label className="mb-1.5 block">Discord ID нового администратора</Label>
                <Input value={newDiscordId} onChange={(event) => setNewDiscordId(event.target.value)} disabled={!data?.can_edit} />
              </div>
              <Button disabled={!data?.can_edit || !newDiscordId.trim() || createAdmin.isPending} onClick={() => createAdmin.mutate()}>Добавить</Button>
            </CardContent>
          </Card>

          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-64 flex-1"><Label className="mb-1.5 block text-xs">Поиск</Label><SearchInput value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Имя, Discord ID, группа, игрок, роль..." /></div>
            <FilterSelect label="Группа" value={groupFilter} onValueChange={setGroupFilter}>
              <SelectItem value="all">Все группы</SelectItem><SelectItem value="none">Без группы</SelectItem>
              {data?.groups.map((group) => <SelectItem key={group.id} value={String(group.id)}>{group.name}</SelectItem>)}
            </FilterSelect>
            <FilterSelect label="Департаменты" value={departmentFilter} onValueChange={setDepartmentFilter}><SelectItem value="all">Все</SelectItem><SelectItem value="with">Есть членство</SelectItem><SelectItem value="without">Нет членства</SelectItem></FilterSelect>
            <FilterSelect label="Конфликты" value={conflictFilter} onValueChange={setConflictFilter}><SelectItem value="all">Все</SelectItem><SelectItem value="with">Есть конфликты</SelectItem><SelectItem value="without">Нет конфликтов</SelectItem></FilterSelect>
            {(search || groupFilter !== "all" || departmentFilter !== "all" || conflictFilter !== "all") && <Button variant="ghost" onClick={resetFilters}>Сбросить</Button>}
            <Badge variant="secondary" className="mb-1 font-normal">{filteredAdmins.length} из {data?.admins.length ?? 0}</Badge>
          </div>

          <Card className="overflow-hidden py-0">
            <CardContent className="p-0">
              {isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-10 animate-pulse rounded bg-muted" />)}</div> : (
                <Table>
                  <TableHeader><TableRow><TableHead>Администратор</TableHead><TableHead>Группа</TableHead><TableHead>Департаменты</TableHead><TableHead>Конфликты</TableHead><TableHead>Роль</TableHead><TableHead className="w-8" /></TableRow></TableHeader>
                  <TableBody>
                    {filteredAdmins.map((admin) => <TableRow key={admin.id} className="cursor-pointer" onClick={(event) => { if (event.target instanceof Element && event.target.closest("a,button")) return; navigate(`/users/${admin.id}`) }}>
                      <TableCell>
                        <div className="font-medium">{admin.discord_username ?? "Без имени"}</div>
                        <div className="font-mono text-xs text-muted-foreground">{admin.discord_id}</div>
                        {admin.linked_player && <Link to={`/players/${admin.linked_player.player_id}`} className="text-xs text-primary underline-offset-2 hover:underline">Игрок: {admin.linked_player.player_name}</Link>}
                      </TableCell>
                      <TableCell>{admin.privilege_group_name ?? <span className="text-muted-foreground">Без группы</span>}</TableCell>
                      <TableCell><Badge variant="outline">{admin.permission_summary.department_count}</Badge></TableCell>
                      <TableCell>{admin.permission_summary.conflict_count > 0 ? <Badge variant="destructive" className="gap-1"><AlertTriangle className="size-3" />{admin.permission_summary.conflict_count}</Badge> : <Badge variant="secondary">0</Badge>}</TableCell>
                      <TableCell><div className="flex flex-wrap gap-1"><Badge variant="outline">{admin.role}</Badge>{admin.is_superuser && <Badge>Superuser</Badge>}</div></TableCell>
                      <TableCell><ChevronRight className="size-4 text-muted-foreground" /></TableCell>
                    </TableRow>)}
                    {!filteredAdmins.length && <TableRow><TableCell colSpan={6} className="py-12 text-center text-muted-foreground">Администраторы не найдены</TableCell></TableRow>}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {tab === "groups" && <GroupsTab data={data} canEdit={!!data?.can_edit} onCreate={openCreateGroup} onEdit={openEditGroup} onDelete={(id) => deleteGroup.mutate(id)} />}

      <Dialog open={groupDialogOpen} onOpenChange={(open) => open ? setGroupDialogOpen(true) : closeGroupDialog()}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader><DialogTitle>{editingGroupId ? "Изменить группу прав" : "Новая группа прав"}</DialogTitle><DialogDescription>Права группы применяются ко всем её участникам и только добавляют доступ. Индивидуальное правило администратора имеет приоритет.</DialogDescription></DialogHeader>
          <div className="space-y-3"><div><Label className="mb-1.5 block">Название</Label><Input value={groupName} onChange={(event) => setGroupName(event.target.value)} /></div>{registryData && <PermissionMatrixEditor registry={registryData.pages} value={groupPerms} onChange={setGroupPerms} grantOnly />}</div>
          <DialogFooter><Button variant="ghost" onClick={closeGroupDialog}>Отмена</Button><Button disabled={!groupName.trim() || createGroup.isPending || updateGroup.isPending} onClick={() => editingGroupId ? updateGroup.mutate() : createGroup.mutate()}>{editingGroupId ? "Сохранить" : "Создать"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function GroupsTab({ data, canEdit, onCreate, onEdit, onDelete }: { data: UsersResponse | undefined; canEdit: boolean; onCreate: () => void; onEdit: (group: PrivilegeGroup) => void; onDelete: (id: number) => void }) {
  return <div className="space-y-4">{canEdit && <Button onClick={onCreate}>+ Новая группа прав</Button>}<div className="grid gap-4 md:grid-cols-2">{data?.groups.map((group) => <Card key={group.id}><CardHeader className="flex flex-row items-center justify-between space-y-0"><CardTitle className="text-sm">{group.name}</CardTitle><Badge variant="secondary">{group.member_count} участников</Badge></CardHeader><CardContent className="space-y-3"><div className="text-xs text-muted-foreground">Группа выдаёт только разрешения. Запреты группы не применяются.</div><div className="flex flex-wrap gap-1">{Object.entries(unwrapPages(group.permissions)).filter(([, actions]) => Object.values(actions).some((value) => value === true)).map(([page, actions]) => <Badge key={page} variant="outline" className="text-xs">{page}: {Object.entries(actions).filter(([, value]) => value === true).map(([action]) => `${action}: да`).join("/")}</Badge>)}</div>{canEdit && <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => onEdit(group)}>Изменить</Button><Button size="sm" variant="destructive" onClick={() => onDelete(group.id)}>Удалить группу</Button></div>}</CardContent></Card>)}</div></div>
}

function FilterSelect({ label, value, onValueChange, children }: { label: string; value: string; onValueChange: (value: string) => void; children: React.ReactNode }) {
  return <div className="w-44"><Label className="mb-1.5 block text-xs">{label}</Label><Select value={value} onValueChange={onValueChange}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{children}</SelectContent></Select></div>
}
