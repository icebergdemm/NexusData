import { useState } from "react"
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useInfiniteScroll } from "@/hooks/useInfiniteScroll"
import { toast } from "sonner"
import { Gavel, Plus, Star, StickyNote, Pencil } from "lucide-react"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogTrigger, DialogFooter,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"

interface PunishmentRow {
  id: number
  player_id: number
  player_steamid: string
  player_name: string | null
  player_actual_name?: string | null
  player_forced_name?: string | null
  type: string
  reason: string | null
  issued_by_name: string | null
  issued_by_was_superuser: boolean
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
  return formatBrowserDate(iso)
}

export function PunishmentsPage() {
  const [tab, setTab] = useState("warning")
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("all")
  const [superuserOnly, setSuperuserOnly] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<PunishmentRow | null>(null)
  const [editForm, setEditForm] = useState({ reason: "", expires_days: "", notes: "" })
  const [form, setForm] = useState({ player_steamid: "", reason: "", expires_days: "", notes: "", server_name: "" })
  const queryClient = useQueryClient()
  const debouncedSearch = useDebouncedValue(search.trim())

  const { data: infiniteData, isFetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["punishments", tab, debouncedSearch, status, superuserOnly],
    queryFn: ({ pageParam = 1 }) => api.get<PunishmentsResponse>(`/api/v1/admin/punishments?${new URLSearchParams({
      tab, page: String(pageParam as number), search: debouncedSearch,
      ...(status !== "all" ? { status } : {}),
      ...(superuserOnly ? { superuser_only: "true" } : {}),
    })}`),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page < last.pages ? last.page + 1 : undefined),
  })
  const data = infiniteData?.pages[0]
  const allRows = infiniteData?.pages.flatMap((p) => p.rows) ?? []
  const total = data?.total ?? 0
  const sentinelRef = useInfiniteScroll(() => { if (hasNextPage) fetchNextPage() }, !!hasNextPage, isFetchingNextPage)

  const changeTab = (v: string) => setTab(v)
  const changeSearch = (v: string) => setSearch(v)
  const changeStatus = (v: string) => setStatus(v)
  const changeSuperuserOnly = (v: boolean) => setSuperuserOnly(v)

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
  const editMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/punishments/${editTarget?.id}`, {
      reason: editForm.reason, expires_days: editForm.expires_days ? Number(editForm.expires_days) : null, notes: editForm.notes,
    }),
    onSuccess: () => { toast.success("Наказание обновлено"); setEditTarget(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })
  const openEdit = (row: PunishmentRow) => {
    const days = row.expires_at ? Math.max(1, Math.ceil((new Date(row.expires_at).getTime() - Date.now()) / 86400000)) : ""
    setEditForm({ reason: row.reason ?? "", expires_days: String(days), notes: row.notes ?? "" })
    setEditTarget(row)
  }

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
                  <Label className="mb-1.5 block">SteamID</Label>
                  <Input value={form.player_steamid} onChange={(e) => setForm((f) => ({ ...f, player_steamid: e.target.value }))} />
                </div>
                <div>
                  <Label className="mb-1.5 block">Причина</Label>
                  <Input value={form.reason} onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))} />
                </div>
                <div>
                  <Label className="mb-1.5 block">Срок (дней, пусто = навсегда)</Label>
                  <Input type="number" value={form.expires_days} onChange={(e) => setForm((f) => ({ ...f, expires_days: e.target.value }))} />
                </div>
                {(tab === "ban" || tab === "kick") && (
                  <div>
                    <Label className="mb-1.5 block">Сервер (пусто = оба)</Label>
                    <Input value={form.server_name} onChange={(e) => setForm((f) => ({ ...f, server_name: e.target.value }))} placeholder="ASPER1 / ASPER2" />
                  </div>
                )}
                <div>
                  <Label className="mb-1.5 block">Внутренние заметки</Label>
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

      <Tabs value={tab} onValueChange={changeTab} className="mb-4">
        <TabsList>
          {(data?.punishment_types ?? ["warning", "reprimand", "kick", "ban"]).map((t) => (
            <TabsTrigger key={t} value={t} className="gap-1.5">
              <span className={`h-1.5 w-1.5 rounded-full ${TYPE_DOT[t] ?? "bg-muted-foreground"}`} />
              {data?.type_labels[t] ?? t}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput
          value={search}
          onChange={(e) => changeSearch(e.target.value)}
          placeholder="Игрок, SteamID, причина, кто выдал, заметки..."
          className="max-w-sm"
        />
        <Select value={status} onValueChange={changeStatus}>
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Любой статус</SelectItem>
            <SelectItem value="active">Активно</SelectItem>
            <SelectItem value="expired">Истекло</SelectItem>
            <SelectItem value="void">Отменено</SelectItem>
          </SelectContent>
        </Select>
        <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Checkbox checked={superuserOnly} onCheckedChange={(c) => changeSuperuserOnly(c === true)} />
          Только выданные суперпользователями
        </label>
        {data && <span className="ml-auto text-xs text-muted-foreground">{data.total} записей</span>}
      </div>

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
              {allRows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    <div className="font-medium">
                      {r.player_id ? (
                        <PlayerHoverLink playerId={r.player_id}>{r.player_actual_name || r.player_name || "-"}</PlayerHoverLink>
                      ) : (r.player_actual_name || r.player_name || "-")}
                      {r.player_forced_name && <span className="ml-1 text-[11px] text-muted-foreground" title={`Закреплённое: ${r.player_forced_name}`}>🔗</span>}
                    </div>
                    {r.player_forced_name && r.player_forced_name !== (r.player_actual_name || r.player_name) && (
                      <div className="text-xs text-muted-foreground">🔗 {r.player_forced_name}</div>
                    )}
                    <div className="font-mono text-xs text-muted-foreground">{r.player_steamid}</div>
                  </TableCell>
                  <TableCell className="max-w-52 truncate" title={r.reason ?? undefined}>{r.reason || "-"}</TableCell>
                  <TableCell className="text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      {r.issued_by_name || "-"}
                      {r.issued_by_was_superuser && (
                        <span title="Выдано суперпользователем"><Star className="size-3.5 fill-warning text-warning" /></span>
                      )}
                      {r.notes && (
                        <span title={`Заметка для админов: ${r.notes}`}><StickyNote className="size-3.5 text-info" /></span>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">{fmtDate(r.issued_at)}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{r.expires_at ? fmtDate(r.expires_at) : "Навсегда"}</TableCell>
                  <TableCell><Badge variant={STATUS_VARIANT[r.status]}>{STATUS_LABEL[r.status]}</Badge></TableCell>
                  {data?.can_edit && (
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1.5 whitespace-nowrap">
                        {r.status === "active" && r.type === "ban" && (
                          <Button size="sm" variant="outline" onClick={() => unbanMutation.mutate(r.id)}>Разбан</Button>
                        )}
                        {r.status === "active" && (
                          <Button size="sm" variant="outline" onClick={() => voidMutation.mutate(r.id)}>Отменить</Button>
                        )}
                        <Button size="sm" variant="outline" onClick={() => openEdit(r)}><Pencil className="size-3.5" /> Изменить</Button>
                        {data?.can_delete && (
                          <Button size="sm" variant="destructive" onClick={() => deleteMutation.mutate(r.id)}>Удалить</Button>
                        )}
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
              {!allRows.length && (
                <TableRow>
                  <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                    {isFetching ? "Загрузка…" : (search || status !== "all" || superuserOnly) ? "Ничего не найдено по этим фильтрам" : "Наказаний нет"}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
          <div ref={sentinelRef as never} className="h-1" />
          {isFetchingNextPage && (
            <div className="flex justify-center gap-2 p-3 text-sm text-muted-foreground">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" /> Загрузка…
            </div>
          )}
          {!hasNextPage && allRows.length > 0 && (
            <div className="border-t p-3 text-center text-xs text-muted-foreground">Загружено {allRows.length} из {total} · конец списка</div>
          )}
        </CardContent>
      </Card>
      <Dialog open={!!editTarget} onOpenChange={(open) => !open && setEditTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Изменить наказание</DialogTitle>
            <DialogDescription>{editTarget?.player_name || editTarget?.player_steamid}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div><Label className="mb-1.5 block">Причина</Label><Textarea value={editForm.reason} onChange={(e) => setEditForm((f) => ({ ...f, reason: e.target.value }))} /></div>
            <div><Label className="mb-1.5 block">Новый срок в днях</Label><Input type="number" min="1" placeholder="Пусто = бессрочно" value={editForm.expires_days} onChange={(e) => setEditForm((f) => ({ ...f, expires_days: e.target.value }))} /></div>
            <div><Label className="mb-1.5 block">Внутренняя заметка</Label><Textarea placeholder="Что важно знать модераторам..." value={editForm.notes} onChange={(e) => setEditForm((f) => ({ ...f, notes: e.target.value }))} /></div>
          </div>
          <DialogFooter><Button disabled={editMutation.isPending} onClick={() => editMutation.mutate()}>Сохранить изменения</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
