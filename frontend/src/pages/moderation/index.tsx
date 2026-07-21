import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ShieldAlert, MapPin, Ship, Target, Users2, Copy, IdCard, Link2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { PlayerSummaryDialog } from "@/components/layout/PlayerSummaryDialog"
import { AppContextMenu } from "@/components/layout/AppContextMenu"
import { api } from "@/lib/api"
import type { ModerationOverview, OfflinePlayer, OnlinePlayer } from "./types"
import { AnomaliesTab } from "./AnomaliesTab"

function copyToClipboard(text: string, label: string) {
  navigator.clipboard.writeText(text).then(() => toast.success(`${label} скопирован`)).catch(() => toast.error("Не удалось скопировать"))
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}

function fmtPlayTime(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}

function onlineDisplayName(p: OnlinePlayer): string {
  return p.forced_display_name || p.player_name || p.player_steamid
}

function offlineDisplayName(p: OfflinePlayer): string {
  return p.forced_display_name || p.player_last_time_name
}

/** Small badge shown next to a forced nickname, with the real name in a tooltip. */
function ForcedNameBadge({ realName }: { realName: string | null }) {
  if (!realName) return null
  return (
    <Badge variant="outline" className="ml-1.5 border-info/40 text-info" title={`Настоящий ник: ${realName}`}>
      <Link2 className="mr-1 size-3" /> принудительное имя
    </Badge>
  )
}

type ActionType = "kick" | "ban" | "message"

export function ModerationPage() {
  const [search, setSearch] = useState("")
  const [searchQuery, setSearchQuery] = useState<string | undefined>()
  const [actionTarget, setActionTarget] = useState<{ steamid: string; name: string; type: ActionType } | null>(null)
  const [actionReason, setActionReason] = useState("")
  const [actionServer, setActionServer] = useState("")
  const [summaryPlayerId, setSummaryPlayerId] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const queryKey = ["moderation-overview", searchQuery]
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams()
      if (searchQuery) params.set("search", searchQuery)
      return api.get<ModerationOverview>(`/api/v1/admin/moderation/overview?${params}`)
    },
    refetchInterval: 15000,
  })

  const actionMutation = useMutation({
    mutationFn: () => api.post("/api/v1/admin/moderation/action", {
      action_type: actionTarget?.type,
      target_steamid: actionTarget?.steamid,
      reason: actionTarget?.type !== "message" ? (actionReason || null) : null,
      message_text: actionTarget?.type === "message" ? (actionReason || null) : null,
      server_name: actionServer || null,
    }),
    onSuccess: () => {
      toast.success("Действие поставлено в очередь")
      setActionTarget(null)
      setActionReason("")
      queryClient.invalidateQueries({ queryKey: ["moderation-overview"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const reviewMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/moderation/anticheat/${id}/review`),
    onSuccess: () => { toast.success("Отмечено проверенным"); queryClient.invalidateQueries({ queryKey }) },
  })

  const submitSearch = () => setSearchQuery(search.trim().length >= 2 ? search.trim() : undefined)

  const offlineRows: OfflinePlayer[] = searchQuery ? (data?.search_results ?? []) : (data?.recent_offline ?? [])

  if (isLoading || !data) {
    return <div className="space-y-2"><Skeleton className="h-40" /><Skeleton className="h-96" /></div>
  }

  const unreviewedCount = data.ac_events.filter((e) => !e.reviewed).length

  return (
    <div>
      <PageHeader title="Модерирование" description="Онлайн-статус серверов, поиск игроков и события античита" icon={ShieldAlert} />

      <Tabs defaultValue="status">
        <TabsList>
          <TabsTrigger value="status" className="gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-success" /> Онлайн ({data.online_count})
          </TabsTrigger>
          <TabsTrigger value="banhammer">Вышедшие игроки</TabsTrigger>
          <TabsTrigger value="anticheat" className="gap-1.5">
            Античит {unreviewedCount > 0 && <Badge variant="destructive" className="h-4 px-1.5 text-[10px]">{unreviewedCount}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="anomalies" className="gap-1.5">
            <ShieldAlert className="size-3.5" /> Аномалии
          </TabsTrigger>
        </TabsList>

        <TabsContent value="status" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            {Object.entries(data.round_states).map(([sn, rs]) => (
              <Card key={sn} className="overflow-hidden py-0">
                <CardHeader className="flex flex-row items-center justify-between space-y-0 border-b bg-muted/30 py-3">
                  <CardTitle className="text-sm font-semibold">{sn}</CardTitle>
                  <Badge className={rs.active ? "bg-success text-white" : ""} variant={rs.active ? "default" : "secondary"}>
                    {rs.active ? "🟢 Раунд идёт" : "⚪ Раунд не идёт"}
                  </Badge>
                </CardHeader>
                <CardContent className="py-4 text-sm">
                  {rs.active ? (
                    <div className="grid grid-cols-2 gap-3">
                      <InfoRow icon={MapPin} label="Локация" value={rs.location ?? "-"} />
                      <InfoRow icon={Ship} label="Корабль" value={rs.submarine_name ?? "-"} />
                      <InfoRow icon={Target} label="Миссия" value={rs.mission ?? "-"} />
                      <InfoRow icon={Users2} label="Игроков в раунде" value={String(rs.players.length)} />
                    </div>
                  ) : (
                    <div className="text-muted-foreground">
                      {rs.last_end_at ? `Последний раунд завершён: ${fmtDate(rs.last_end_at)}` : "Нет данных о раундах"}
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>

          <Card className="overflow-hidden py-0">
            <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Игроки онлайн</CardTitle></CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Игрок</TableHead>
                    <TableHead>Сервер</TableHead>
                    <TableHead>Роль</TableHead>
                    <TableHead>Пинг</TableHead>
                    <TableHead>Дата входа</TableHead>
                    {data.can_edit && <TableHead className="text-right">Действия</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.online_players.map((p) => {
                    const name = onlineDisplayName(p)
                    return (
                    <AppContextMenu
                      key={p.player_steamid}
                      actions={[
                        ...(p.player_id ? [{ label: "Открыть профиль", icon: ShieldAlert, onSelect: () => setSummaryPlayerId(p.player_id as number) }] : []),
                        { label: "Скопировать SteamID", icon: IdCard, onSelect: () => copyToClipboard(p.player_steamid, "SteamID") },
                        { label: "Скопировать имя", icon: Copy, onSelect: () => copyToClipboard(name, "Имя") },
                        ...(data.can_edit ? [
                          { label: "Сообщение", separatorBefore: true, onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "message" as const }) },
                          { label: "Кик", onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "kick" as const }) },
                          { label: "Бан", destructive: true, onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "ban" as const }) },
                        ] : []),
                      ]}
                    >
                      <TableRow className={p.is_new_player ? "bg-warning/10 hover:bg-warning/15" : undefined}>
                        <TableCell className="font-medium">
                          {p.player_id ? (
                            <button className="text-left text-primary underline-offset-2 hover:underline" onClick={() => setSummaryPlayerId(p.player_id)}>
                              {name}
                            </button>
                          ) : name}
                          {p.is_new_player && <Badge variant="outline" className="ml-1.5 border-warning/40 text-[10px] text-warning">Новый</Badge>}
                          {p.forced_display_name && <ForcedNameBadge realName={p.player_name} />}
                        </TableCell>
                        <TableCell><Badge variant="secondary">{p.server_name}</Badge></TableCell>
                        <TableCell>{p.job_id ?? "-"}</TableCell>
                        <TableCell>{p.ping ?? "-"}</TableCell>
                        <TableCell className="text-sm text-muted-foreground">{fmtDate(p.join_time)}</TableCell>
                        {data.can_edit && (
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1.5 whitespace-nowrap">
                              <Button size="sm" variant="outline" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "message" })}>Сообщение</Button>
                              <Button size="sm" variant="outline" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "kick" })}>Кик</Button>
                              <Button size="sm" variant="destructive" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "ban" })}>Бан</Button>
                            </div>
                          </TableCell>
                        )}
                      </TableRow>
                    </AppContextMenu>
                    )
                  })}
                  {!data.online_players.length && (
                    <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">Никого нет онлайн</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="banhammer" className="space-y-4">
          <div className="flex gap-2">
            <SearchInput
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitSearch()}
              placeholder="Поиск игрока по нику/SteamID..."
              className="max-w-xs"
            />
            <Button variant="secondary" onClick={submitSearch}>Найти</Button>
          </div>
          <Card className="overflow-hidden py-0">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Игрок</TableHead>
                    <TableHead>Наиграно</TableHead>
                    <TableHead>Первый вход</TableHead>
                    <TableHead>Последний вход</TableHead>
                    {data.can_edit && <TableHead className="text-right">Действия</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {offlineRows.map((p) => {
                    const name = offlineDisplayName(p)
                    return (
                    <AppContextMenu
                      key={p.player_id}
                      actions={[
                        { label: "Открыть профиль", icon: ShieldAlert, onSelect: () => setSummaryPlayerId(p.player_id) },
                        { label: "Скопировать SteamID", icon: IdCard, onSelect: () => copyToClipboard(p.player_steamid, "SteamID") },
                        { label: "Скопировать имя", icon: Copy, onSelect: () => copyToClipboard(name, "Имя") },
                        ...(data.can_edit ? [
                          { label: "Сообщение", separatorBefore: true, onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "message" as const }) },
                          { label: "Кик", onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "kick" as const }) },
                          { label: "Бан", destructive: true, onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "ban" as const }) },
                        ] : []),
                      ]}
                    >
                      <TableRow className={p.is_new_player ? "bg-warning/10 hover:bg-warning/15" : undefined}>
                        <TableCell>
                          <button className="font-medium text-primary underline-offset-2 hover:underline" onClick={() => setSummaryPlayerId(p.player_id)}>
                            {name}
                          </button>
                          {p.is_new_player && <Badge variant="outline" className="ml-1.5 border-warning/40 text-[10px] text-warning">Новый</Badge>}
                          {p.forced_display_name && <ForcedNameBadge realName={p.player_last_time_name} />}
                          <div className="font-mono text-xs text-muted-foreground">{p.player_steamid}</div>
                        </TableCell>
                        <TableCell>{fmtPlayTime(p.play_time_hours)}</TableCell>
                        <TableCell className="text-sm text-muted-foreground">{fmtDate(p.first_login)}</TableCell>
                        <TableCell className="text-sm text-muted-foreground">{fmtDate(p.last_login_time)}</TableCell>
                        {data.can_edit && (
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1.5 whitespace-nowrap">
                              <Button size="sm" variant="outline" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "message" })}>Сообщение</Button>
                              <Button size="sm" variant="outline" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "kick" })}>Кик</Button>
                              <Button size="sm" variant="destructive" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "ban" })}>Бан</Button>
                            </div>
                          </TableCell>
                        )}
                      </TableRow>
                    </AppContextMenu>
                    )
                  })}
                  {!offlineRows.length && (
                    <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">Нет результатов</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="anticheat">
          <Card className="overflow-hidden py-0">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Игрок</TableHead>
                    <TableHead>Событие</TableHead>
                    <TableHead>Время</TableHead>
                    <TableHead>Статус</TableHead>
                    {data.can_edit && <TableHead className="text-right">Действия</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.ac_events.map((ev) => (
                    <TableRow key={ev.id}>
                      <TableCell className="font-medium">
                        {ev.player_id ? (
                          <button className="text-left text-primary underline-offset-2 hover:underline" onClick={() => setSummaryPlayerId(ev.player_id)}>
                            {ev.player_name || ev.player_steamid}
                          </button>
                        ) : (ev.player_name || ev.player_steamid)}
                      </TableCell>
                      <TableCell><span className="font-mono text-xs">{ev.event_type}</span></TableCell>
                      <TableCell className="text-sm text-muted-foreground">{fmtDate(ev.occurred_at)}</TableCell>
                      <TableCell>
                        <Badge variant={ev.reviewed ? "secondary" : "destructive"}>
                          {ev.reviewed ? "Проверено" : "Новое"}
                        </Badge>
                      </TableCell>
                      {data.can_edit && (
                        <TableCell className="text-right">
                          {!ev.reviewed && (
                            <Button size="sm" variant="outline" onClick={() => reviewMutation.mutate(ev.id)}>Отметить проверенным</Button>
                          )}
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                  {!data.ac_events.length && (
                    <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">Событий нет</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="anomalies">
          <AnomaliesTab setSummaryPlayerId={setSummaryPlayerId} />
        </TabsContent>
      </Tabs>

      <Dialog open={!!actionTarget} onOpenChange={(open) => !open && setActionTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {actionTarget?.type === "ban" ? "Забанить" : actionTarget?.type === "kick" ? "Кикнуть" : "Отправить сообщение"} - {actionTarget?.name}
            </DialogTitle>
            <DialogDescription>Действие будет поставлено в очередь и выполнено игровым сервером при следующем опросе.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="mb-1.5 block">{actionTarget?.type === "message" ? "Текст сообщения" : "Причина"}</Label>
              <Textarea value={actionReason} onChange={(e) => setActionReason(e.target.value)} />
            </div>
            <div>
              <Label className="mb-1.5 block">Сервер (пусто = оба)</Label>
              <Input value={actionServer} onChange={(e) => setActionServer(e.target.value)} placeholder="ASPER1 / ASPER2" />
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => actionMutation.mutate()} disabled={actionMutation.isPending}>Подтвердить</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <PlayerSummaryDialog playerId={summaryPlayerId} open={summaryPlayerId != null} onOpenChange={(o) => !o && setSummaryPlayerId(null)} />
    </div>
  )
}

function InfoRow({ icon: Icon, label, value }: { icon: typeof MapPin; label: string; value: string }) {
  return (
    <div className="flex items-start gap-2">
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <div>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="font-medium">{value}</div>
      </div>
    </div>
  )
}
