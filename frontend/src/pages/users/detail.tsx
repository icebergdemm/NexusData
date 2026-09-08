import { useEffect, useMemo, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, ArrowLeft, Check, ChevronDown, ExternalLink, KeyRound, ShieldCheck, Trash2, UserCog, Users } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { useCurrentAdmin, usePermissionRegistry, type PermissionRegistry } from "@/lib/auth"
import { LoginKeysDialog } from "./LoginKeysDialog"
import { formatPermissionSource, hasPermissionConflict, permissionStatusLabel, resolvePermissionValue, setPermissionOverride, type PermissionValue } from "./PermissionMatrixEditor"
import type { AuthorizationTrace, UsersResponse } from "./types"
import { unwrapPages } from "./types"

export function UserDetailPage() {
  const { adminId: adminIdParam } = useParams<{ adminId: string }>()
  const adminId = Number(adminIdParam)
  const navigate = useNavigate()
  const { data: me } = useCurrentAdmin()
  const { data: registryData } = usePermissionRegistry()
  const queryClient = useQueryClient()
  const [keysOpen, setKeysOpen] = useState(false)

  const { data: usersData } = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<UsersResponse>("/api/v1/admin/users") })
  const { data: trace, isLoading, isError } = useQuery({
    queryKey: ["admin-authorization", adminId],
    queryFn: () => api.get<AuthorizationTrace>(`/api/v1/admin/users/${adminId}/authorization`),
    enabled: Number.isInteger(adminId) && adminId > 0,
  })
  const listedAdmin = usersData?.admins.find((admin) => admin.id === adminId)
  const canEdit = !!usersData?.can_edit
  const profileName = trace?.admin.discord_username ?? listedAdmin?.discord_username ?? `#${adminId}`

  const assignGroup = useMutation({
    mutationFn: (privilege_group_id: number | null) => api.post(`/api/v1/admin/users/${adminId}/assign-group`, { privilege_group_id }),
    onSuccess: () => { toast.success("Группа обновлена"); invalidateUserQueries(queryClient, adminId) },
    onError: (error: Error) => toast.error(error.message),
  })
  const toggleSuperuser = useMutation({
    mutationFn: (is_superuser: boolean) => api.post(`/api/v1/admin/users/${adminId}/assign-group`, { is_superuser }),
    onSuccess: () => { toast.success("Статус superuser обновлён"); invalidateUserQueries(queryClient, adminId) },
    onError: (error: Error) => toast.error(error.message),
  })
  const deleteAdmin = useMutation({
    mutationFn: () => api.delete(`/api/v1/admin/users/${adminId}`),
    onSuccess: () => { toast.success("Администратор удалён"); navigate("/users") },
    onError: (error: Error) => toast.error(error.message),
  })

  if (!Number.isInteger(adminId) || adminId < 1 || isError) {
    return <Card><CardContent className="space-y-3 py-12 text-center"><p className="text-muted-foreground">Администратор не найден.</p><Button variant="outline" onClick={() => navigate("/users")}><ArrowLeft className="size-4" />К списку</Button></CardContent></Card>
  }
  if (isLoading || !trace) return <div className="space-y-4"><Skeleton className="h-40" /><Skeleton className="h-12" /><Skeleton className="h-96" /></div>

  const group = trace.sources.group
  const player = trace.linked_player
  return (
    <div className="space-y-4">
      <PageHeader
        title={profileName}
        description="Профиль администратора Nexus Data"
        icon={UserCog}
        actions={<Button variant="outline" onClick={() => navigate("/users")}><ArrowLeft className="size-4" />К списку</Button>}
      />

      <Card className="overflow-hidden py-0">
        <CardContent className="flex flex-wrap items-start justify-between gap-4 bg-gradient-to-r from-primary/10 via-card to-card px-6 py-5">
          <div className="flex min-w-0 items-start gap-3">
            <div className="flex size-12 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary"><UserCog className="size-6" /></div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2"><h1 className="text-xl font-bold">{profileName}</h1>{trace.admin.is_superuser && <Badge>Superuser</Badge>}<Badge variant="outline">{trace.admin.role}</Badge></div>
              <div className="mt-1 font-mono text-xs text-muted-foreground">Discord ID: {trace.admin.discord_id}</div>
              {player ? <Link className="mt-1 inline-flex items-center gap-1 text-sm text-primary hover:underline" to={`/players/${player.player_id}`}><ExternalLink className="size-3.5" />Игровой профиль: {player.name}</Link> : <div className="mt-1 text-sm text-muted-foreground">Игровой профиль не привязан</div>}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => setKeysOpen(true)}><KeyRound className="size-4" />Ключи</Button>
            {me?.is_superuser && adminId !== me.id && canEdit && <Button variant="outline" onClick={() => toggleSuperuser.mutate(!trace.admin.is_superuser)}>{trace.admin.is_superuser ? "Снять superuser" : "Выдать superuser"}</Button>}
            {usersData?.can_delete && adminId !== me?.id && <Button variant="destructive" onClick={() => { if (window.confirm(`Удалить администратора ${profileName}?`)) deleteAdmin.mutate() }}><Trash2 className="size-4" />Удалить</Button>}
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="overview">
        <TabsList className="w-full flex-wrap justify-start"><TabsTrigger value="overview">Обзор</TabsTrigger><TabsTrigger value="permissions">Права</TabsTrigger><TabsTrigger value="departments">Департаменты ({trace.departments.length})</TabsTrigger><TabsTrigger value="keys">Ключи и аудит</TabsTrigger></TabsList>
        <TabsContent value="overview" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-4"><SummaryCard label="Группа" value={group?.name ?? "Без группы"} icon={Users} /><SummaryCard label="Департаменты" value={String(trace.departments.length)} icon={ShieldCheck} /><SummaryCard label="Конфликты" value={String(countConflicts(trace))} icon={AlertTriangle} destructive={countConflicts(trace) > 0} /><SummaryCard label="Login keys" value={String(trace.login_keys.length)} icon={KeyRound} /></div>
          <Card><CardHeader><CardTitle className="text-base">Группа прав</CardTitle></CardHeader><CardContent className="flex flex-wrap items-end gap-3"><div className="min-w-64 flex-1"><Label className="mb-1.5 block text-xs">Назначенная группа</Label><Select value={group ? String(group.id) : "none"} onValueChange={(value) => assignGroup.mutate(value === "none" ? null : Number(value))} disabled={!canEdit}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Без группы</SelectItem>{(usersData?.groups ?? []).map((item) => <SelectItem key={item.id} value={String(item.id)}>{item.name}</SelectItem>)}</SelectContent></Select></div><Button variant="outline" onClick={() => navigate("/users?tab=groups")}>К настройке групп</Button></CardContent></Card>
          <ResourceSummary trace={trace} />
        </TabsContent>
        <TabsContent value="permissions"><PermissionProfile trace={trace} registry={registryData?.pages} canEdit={canEdit} adminId={adminId} onChanged={() => invalidateUserQueries(queryClient, adminId)} /></TabsContent>
        <TabsContent value="departments"><DepartmentLinks trace={trace} /></TabsContent>
        <TabsContent value="keys"><KeysAndAudit trace={trace} onOpenKeys={() => setKeysOpen(true)} /></TabsContent>
      </Tabs>

      <LoginKeysDialog adminId={adminId} adminName={profileName} open={keysOpen} onOpenChange={setKeysOpen} />
    </div>
  )
}

function PermissionProfile({ trace, registry, canEdit, adminId, onChanged }: { trace: AuthorizationTrace; registry: PermissionRegistry | undefined; canEdit: boolean; adminId: number; onChanged: () => void }) {
  const [draft, setDraft] = useState<PermissionValue>(() => unwrapPages(trace.sources.individual))
  const [search, setSearch] = useState("")
  const [filter, setFilter] = useState<"all" | "allow" | "deny" | "override" | "conflict">("all")
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [expandedDetails, setExpandedDetails] = useState<Record<string, boolean>>({})
  const save = useMutation({
    mutationFn: (pages: PermissionValue) => api.post(`/api/v1/admin/users/${adminId}/assign-group`, { individual_permissions: { pages } }),
    onSuccess: () => { toast.success("Право обновлено"); onChanged() },
    onError: (error: Error) => toast.error(error.message),
  })
  useEffect(() => setDraft(unwrapPages(trace.sources.individual)), [trace.sources.individual])

  const getDisplayedDecision = (page: string, action: string, decision: AuthorizationTrace["effective"]["pages"][string][string]) => {
    const override = draft[page]?.[action]
    const sources = [
      ...decision.sources.filter((source) => source.kind !== "individual").map((source) => ({ ...source })),
      ...(override === undefined ? [] : [{ kind: "individual", value: override }]),
    ]
    if (override !== undefined) {
      for (const source of sources) {
        if (source.kind !== "individual" && source.applies !== false) {
          source.applies = false
          source.reason = "individual_permission_has_priority"
        }
      }
    }
    const effective = resolvePermissionValue(sources, trace.admin.policy_mode)
    return { ...decision, value: effective, state: effective ? "allow" as const : "deny" as const, sources }
  }

  const rows = useMemo(() => Object.entries(trace.effective.pages).map(([page, actions]) => ({
    page, actions: Object.entries(actions).filter(([action, decision]) => {
      const query = search.trim().toLowerCase()
      const actionLabel = registry?.[page]?.actions.find((item) => item.key === action)?.label ?? action
      const pageLabel = registry?.[page]?.label ?? page
      const displayedDecision = getDisplayedDecision(page, action, decision)
      const conflict = hasPermissionConflict(displayedDecision.sources)
      const hasOverride = draft[page]?.[action] !== undefined
      return (!query || `${pageLabel} ${page} ${actionLabel} ${action}`.toLowerCase().includes(query))
        && (filter === "all" || (filter === "allow" && displayedDecision.value) || (filter === "deny" && !displayedDecision.value) || (filter === "override" && hasOverride) || (filter === "conflict" && conflict))
    }),
  })).filter(({ actions }) => actions.length > 0), [trace.effective.pages, registry, search, filter, draft, trace.admin.policy_mode])

  const mutatePermission = (page: string, action: string, value: boolean | undefined) => {
    const next = setPermissionOverride(draft, page, action, value)
    setDraft(next)
    save.mutate(next)
  }
  const togglePage = (page: string) => setExpanded((current) => ({ ...current, [page]: !(current[page] ?? true) }))
  const toggleDetails = (key: string) => setExpandedDetails((current) => ({ ...current, [key]: !(current[key] ?? false) }))

  return <Card><CardHeader className="space-y-3"><div><CardTitle className="text-base">Все page/action permissions</CardTitle><p className="mt-1 text-xs text-muted-foreground">Результат обновляется сразу после нажатия. Подробности конкретного правила раскрываются прямо под ним.</p></div><div className="flex flex-wrap gap-2"><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск страницы или действия..." className="min-w-64 flex-1" /><Select value={filter} onValueChange={(value) => setFilter(value as typeof filter)}><SelectTrigger className="w-52"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все права</SelectItem><SelectItem value="allow">Только разрешённые</SelectItem><SelectItem value="deny">Только запрещённые</SelectItem><SelectItem value="override">С override</SelectItem><SelectItem value="conflict">Только конфликты</SelectItem></SelectContent></Select></div></CardHeader><CardContent className="space-y-2">
    <div className="rounded-md border border-info/30 bg-info/5 px-3 py-2 text-xs text-muted-foreground">Индивидуальное правило имеет приоритет. Группа и департамент только добавляют доступ, их запреты не применяются. Нажмите «Детали» у правила, чтобы увидеть источники и причину результата.</div>
    {rows.map(({ page, actions }) => <div key={page} className="rounded-md border"><button type="button" className="flex w-full items-center justify-between px-3 py-2 text-left font-medium" onClick={() => togglePage(page)}><span>{registry?.[page]?.label ?? page} <span className="font-mono text-xs text-muted-foreground">({page})</span></span><span className="text-xs text-muted-foreground">{actions.length} действий</span></button>{(expanded[page] ?? true) && <div className="border-t px-3 py-2">{actions.map(([action, decision]) => {
      const displayedDecision = getDisplayedDecision(page, action, decision)
      const conflict = hasPermissionConflict(displayedDecision.sources)
      const detailsKey = `${page}:${action}`
      const detailsExpanded = expandedDetails[detailsKey] ?? false
      const hasOverride = draft[page]?.[action] !== undefined
      return <div key={action} className="border-b py-2 last:border-0"><div className="flex flex-col gap-2 lg:flex-row lg:items-center"><div className="min-w-40 flex-1"><div className="text-sm">{registry?.[page]?.actions.find((item) => item.key === action)?.label ?? action}</div><div className="font-mono text-[10px] text-muted-foreground">{action}</div></div><div className="flex flex-wrap items-center gap-1.5"><Badge variant={displayedDecision.value ? "default" : "destructive"}>{permissionStatusLabel(displayedDecision.value)}</Badge>{conflict && <button type="button" className="inline-flex items-center gap-1 rounded-full border border-destructive/50 bg-destructive/10 px-2 py-0.5 text-[10px] font-medium text-destructive hover:bg-destructive/20" aria-expanded={detailsExpanded} title="Нажмите, чтобы увидеть конфликтующие источники" onClick={() => setExpandedDetails((current) => ({ ...current, [detailsKey]: true }))}><AlertTriangle className="size-3" />Конфликт</button>}</div><div className="flex flex-wrap gap-1.5 lg:justify-end">{canEdit && !trace.admin.is_superuser && <><Button size="sm" variant="outline" disabled={save.isPending} onClick={() => mutatePermission(page, action, true)}><Check className="size-3.5" />Разрешить</Button><Button size="sm" variant="destructive" disabled={save.isPending} onClick={() => mutatePermission(page, action, false)}>Запретить</Button><Button size="sm" variant="ghost" disabled={!hasOverride || save.isPending} onClick={() => mutatePermission(page, action, undefined)}>Сбросить</Button></>}<Button size="sm" variant="ghost" onClick={() => toggleDetails(detailsKey)}><ChevronDown className={`size-3.5 transition-transform ${detailsExpanded ? "rotate-180" : ""}`} />{detailsExpanded ? "Скрыть детали" : "Детали"}</Button></div></div>{detailsExpanded && <div className="mt-2 rounded-md border border-muted-foreground/20 bg-muted/10 px-3 py-2 text-xs"><div className="font-medium">Источники решения</div>{displayedDecision.sources.length ? <div className="mt-2 space-y-1.5">{displayedDecision.sources.map((source, index) => <div key={`${source.kind}-${index}`} className="flex flex-wrap items-center gap-1.5"><span>{formatPermissionSource(source)}</span><Badge variant={source.applies === false ? "secondary" : source.value ? "default" : "destructive"}>{source.applies === false ? "Не применяется" : source.value ? "Разрешает" : "Запрещает"}</Badge></div>)}</div> : <p className="mt-1 text-muted-foreground">Явное правило не задано; доступ не выдан.</p>}{conflict && <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-2"><div className="font-medium text-destructive">Почему конфликт</div><p className="mt-1 text-muted-foreground">Применимые источники дают разные решения. Индивидуальное правило имеет приоритет; если его нет, разрешение приходит от группы или департамента.</p><p className="mt-1 text-muted-foreground">Чтобы убрать конфликт, сбросьте индивидуальное правило или измените источник в разделе группы/департамента.</p></div>}</div>}</div>})}</div>}</div>)}
    {!rows.length && <div className="py-10 text-center text-sm text-muted-foreground">По этим фильтрам прав нет.</div>}
  </CardContent></Card>
}

function DepartmentLinks({ trace }: { trace: AuthorizationTrace }) {
  return <Card><CardHeader><CardTitle className="text-base">Членство и уровни</CardTitle></CardHeader><CardContent className="space-y-2">{trace.departments.length ? trace.departments.map((department) => <div key={department.id} className="rounded-md border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><Link className="font-medium text-primary hover:underline" to={`/departments/${department.id}`}>{department.name}</Link><Badge variant="outline">{department.is_head ? "Глава" : department.is_deputy_head ? "Заместитель" : "Участник"}</Badge></div><div className="mt-1 text-xs text-muted-foreground">{department.is_auto ? "Автоматическое членство" : "Ручное членство"}</div><div className="mt-2 flex flex-wrap gap-1">{department.levels.filter((level) => level.held).map((level) => <Link key={level.id} to={`/departments/${department.id}#level-${level.id}`}><Badge variant="outline" className="hover:bg-accent">{level.name}</Badge></Link>)}</div><div className="mt-2 text-xs text-muted-foreground">Управление участниками и уровнями: <Link className="text-primary hover:underline" to={`/departments/${department.id}`}>открыть департамент</Link></div></div>) : <p className="py-8 text-center text-sm text-muted-foreground">Администратор не состоит в департаментах.</p>}</CardContent></Card>
}

function KeysAndAudit({ trace, onOpenKeys }: { trace: AuthorizationTrace; onOpenKeys: () => void }) {
  return <div className="space-y-4"><Card><CardHeader><CardTitle className="text-base">Ключи входа</CardTitle></CardHeader><CardContent className="flex flex-wrap items-center justify-between gap-3"><div className="text-sm text-muted-foreground">Активные и отозванные ключи: {trace.login_keys.length}. Секреты здесь не показываются.</div><Button variant="outline" onClick={onOpenKeys}><KeyRound className="size-4" />Открыть ключи</Button></CardContent></Card><Card><CardHeader><CardTitle className="text-base">Аудит действий</CardTitle></CardHeader><CardContent><Button variant="outline" asChild><Link to={`/audit?admin_id=${trace.admin.id}`}><ExternalLink className="size-4" />Открыть аудит этого администратора</Link></Button></CardContent></Card></div>
}

function ResourceSummary({ trace }: { trace: AuthorizationTrace }) {
  return <div className="grid gap-4 md:grid-cols-2"><Card><CardHeader><CardTitle className="text-sm">Документные ACL</CardTitle></CardHeader><CardContent className="text-sm text-muted-foreground">{trace.resource_acl.documents.length} применимых правил · уровни view/comment/edit</CardContent></Card><Card><CardHeader><CardTitle className="text-sm">ACL досок задач</CardTitle></CardHeader><CardContent className="text-sm text-muted-foreground">{trace.resource_acl.task_boards.length} применимых правил</CardContent></Card></div>
}

function SummaryCard({ label, value, icon: Icon, destructive = false }: { label: string; value: string; icon: typeof Users; destructive?: boolean }) {
  return <Card><CardContent className="flex items-center gap-3 p-4"><Icon className={`size-5 ${destructive ? "text-destructive" : "text-muted-foreground"}`} /><div><div className="text-lg font-semibold">{value}</div><div className="text-xs text-muted-foreground">{label}</div></div></CardContent></Card>
}

function countConflicts(trace: AuthorizationTrace) {
  return Object.values(trace.effective.pages).reduce((total, actions) => total + Object.values(actions).filter((decision) => hasPermissionConflict(decision.sources)).length, 0)
}

function invalidateUserQueries(queryClient: ReturnType<typeof useQueryClient>, adminId: number) {
  queryClient.invalidateQueries({ queryKey: ["admin-users"] })
  queryClient.invalidateQueries({ queryKey: ["admin-authorization", adminId] })
}
