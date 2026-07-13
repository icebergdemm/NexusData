import { useState } from "react"
import { useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Skull, Ship, HeartPulse, Swords, Bot, Trophy, Gift, StickyNote, Tags, Radio, Gavel, History as HistoryIcon,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { api } from "@/lib/api"
import type { PlayerDetailResponse } from "./detail-types"
import { RewardsSection } from "./rewards-section"

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "—"
}

function fmtPlayTime(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}

function fmtDuration(seconds: number | null): string {
  if (!seconds) return "—"
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return h > 0 ? `${h}ч ${m}м` : `${m}м`
}

const STATUS_LABEL: Record<string, { label: string; variant: "default" | "secondary" | "destructive" }> = {
  active: { label: "Активно", variant: "destructive" },
  expired: { label: "Истекло", variant: "secondary" },
  void: { label: "Отменено", variant: "secondary" },
}

export function PlayerDetailPage() {
  const { playerId } = useParams<{ playerId: string }>()
  const queryClient = useQueryClient()
  const [noteDraft, setNoteDraft] = useState("")
  const [trapReason, setTrapReason] = useState("")
  const [labelCategoryId, setLabelCategoryId] = useState<string>("")

  const queryKey = ["player-detail", playerId]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<PlayerDetailResponse>(`/api/v1/admin/players/${playerId}`),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey })

  const rankKitMutation = useMutation({
    mutationFn: (body: { rank_id?: number; kit_level?: number }) =>
      api.post(`/api/v1/admin/players/${playerId}/rank-kit`, body),
    onSuccess: () => { toast.success("Данные игрока обновлены"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const discordIdMutation = useMutation({
    mutationFn: (discord_id: string) => api.post(`/api/v1/admin/players/${playerId}/discord-id`, { discord_id }),
    onSuccess: () => { toast.success("Discord ID обновлён"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const noteMutation = useMutation({
    mutationFn: (content: string) => api.post(`/api/v1/admin/players/${playerId}/notes`, { content }),
    onSuccess: () => { toast.success("Заметка сохранена"); setNoteDraft(""); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const assignLabelMutation = useMutation({
    mutationFn: (category_id: number) => api.post(`/api/v1/admin/players/${playerId}/labels`, { category_id }),
    onSuccess: () => { toast.success("Метка назначена"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const removeLabelMutation = useMutation({
    mutationFn: (labelId: number) => api.delete(`/api/v1/admin/players/${playerId}/labels/${labelId}`),
    onSuccess: () => { toast.success("Метка удалена"); invalidate() },
  })

  const setTrapMutation = useMutation({
    mutationFn: (reason: string) => api.post(`/api/v1/admin/players/${playerId}/traps`, { reason }),
    onSuccess: () => { toast.success("Ловушка установлена"); setTrapReason(""); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deactivateTrapMutation = useMutation({
    mutationFn: (trapId: number) => api.delete(`/api/v1/admin/players/${playerId}/traps/${trapId}`),
    onSuccess: () => { toast.success("Ловушка снята"); invalidate() },
  })

  if (isLoading || !data) {
    return <div className="space-y-2"><Skeleton className="h-40" /><Skeleton className="h-64" /></div>
  }

  if (data.classified) {
    return (
      <Card>
        <CardContent className="py-16 text-center text-muted-foreground">
          🔒 Профиль засекречен. Доступ только для суперпользователей.
        </CardContent>
      </Card>
    )
  }

  const { player } = data
  const activePunishments = data.punishments.filter((p) => p.status === "active").length
  const rewardsCount = Object.keys(data.kval_kits).length + Object.keys(data.custom_items).length
    + Object.keys(data.custom_stats).length + Object.keys(data.custom_talents).length

  return (
    <div className="space-y-4">
      {/* ── Header ── */}
      <Card className="overflow-hidden">
        <div className="bg-gradient-to-r from-primary/15 via-card to-card px-6 py-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="relative flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-primary/20 text-2xl font-bold text-primary">
                {player.player_last_time_name.slice(0, 2).toUpperCase()}
                {data.is_online && (
                  <span className="absolute right-0 bottom-0 h-4 w-4 rounded-full border-2 border-card bg-success" title="В сети" />
                )}
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-xl font-bold">{player.player_last_time_name}</h1>
                  {data.is_online && <Badge className="bg-success text-white">В сети</Badge>}
                  {player.is_secret && <Badge variant="outline" className="border-warning/40 text-warning">🔒 Секретно</Badge>}
                </div>
                <div className="mt-0.5 font-mono text-xs text-muted-foreground">{player.player_steamid}</div>
              </div>
            </div>
            <div className="flex gap-4 text-center text-sm">
              <div><div className="text-lg font-bold">{fmtPlayTime(player.play_time_hours)}</div><div className="text-xs text-muted-foreground">наиграно</div></div>
              <Separator orientation="vertical" className="h-10" />
              <div><div className="text-lg font-bold">{data.ranks[String(player.rank_id)] ?? player.rank_id}</div><div className="text-xs text-muted-foreground">ранг</div></div>
              <Separator orientation="vertical" className="h-10" />
              <div><div className={`text-lg font-bold ${activePunishments ? "text-destructive" : ""}`}>{activePunishments}</div><div className="text-xs text-muted-foreground">активных наказаний</div></div>
            </div>
          </div>
          <div className="mt-2 text-xs text-muted-foreground">Последний вход: {fmtDate(player.last_login_time)}{player.last_ip && ` · IP ${player.last_ip}`}</div>
        </div>

        {data.has_edit && (
          <CardContent className="grid grid-cols-1 gap-4 border-t pt-4 sm:grid-cols-3">
            <div>
              <div className="mb-1 text-xs text-muted-foreground">Ранг</div>
              <Select
                value={String(player.rank_id)}
                onValueChange={(v) => rankKitMutation.mutate({ rank_id: Number(v) })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(data.ranks).map(([id, name]) => (
                    <SelectItem key={id} value={id}>{name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <div className="mb-1 text-xs text-muted-foreground">Набор</div>
              <Select
                value={String(player.kit_level)}
                onValueChange={(v) => rankKitMutation.mutate({ kit_level: Number(v) })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(data.kits).map(([id, name]) => (
                    <SelectItem key={id} value={id}>{name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <div className="mb-1 text-xs text-muted-foreground">Discord ID</div>
              <Input
                defaultValue={player.discord_id ?? ""}
                onBlur={(e) => e.target.value !== (player.discord_id ?? "") && discordIdMutation.mutate(e.target.value)}
              />
            </div>
          </CardContent>
        )}
      </Card>

      <Tabs defaultValue="stats">
        <TabsList className="flex-wrap">
          <TabsTrigger value="stats" className="gap-1.5"><Trophy className="h-3.5 w-3.5" /> Статистика</TabsTrigger>
          <TabsTrigger value="rewards" className="gap-1.5"><Gift className="h-3.5 w-3.5" /> Награды {rewardsCount > 0 && `(${rewardsCount})`}</TabsTrigger>
          <TabsTrigger value="notes" className="gap-1.5"><StickyNote className="h-3.5 w-3.5" /> Заметки ({data.notes.length})</TabsTrigger>
          <TabsTrigger value="labels" className="gap-1.5"><Tags className="h-3.5 w-3.5" /> Метки ({data.label_pairs.length})</TabsTrigger>
          <TabsTrigger value="traps" className="gap-1.5"><Radio className="h-3.5 w-3.5" /> Ловушки ({data.traps.length})</TabsTrigger>
          <TabsTrigger value="punishments" className="gap-1.5"><Gavel className="h-3.5 w-3.5" /> Наказания ({data.punishments.length})</TabsTrigger>
          <TabsTrigger value="history" className="gap-1.5"><HistoryIcon className="h-3.5 w-3.5" /> История</TabsTrigger>
        </TabsList>

        <TabsContent value="stats" className="space-y-4">
          <Card>
            <CardContent className="grid grid-cols-2 gap-4 pt-6 sm:grid-cols-3">
              {data.stats ? (
                <>
                  <Stat label="Успешных рейсов капитаном" value={data.stats.successful_missions_captain} icon={Ship} accent="info" />
                  <Stat label="Всего рейсов капитаном" value={data.stats.total_missions_captain} icon={Ship} accent="primary" />
                  <Stat label="Спасено игроков" value={data.stats.saved_players} icon={HeartPulse} accent="success" />
                  <Stat label="Убито монстров" value={data.stats.killed_monsters} icon={Bot} accent="warning" />
                  <Stat label="Убито игроков" value={data.stats.killed_players} icon={Swords} accent="destructive" />
                  <Stat label="Убито NPC" value={data.stats.killed_npc} icon={Skull} accent="muted" />
                </>
              ) : (
                <div className="col-span-full text-center text-muted-foreground">Нет данных статистики</div>
              )}
            </CardContent>
          </Card>

          {data.role_stats.length > 0 && (
            <Card>
              <CardHeader><CardTitle className="text-sm">Статистика по ролям</CardTitle></CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader><TableRow><TableHead>Роль</TableHead><TableHead>Раундов</TableHead><TableHead>MVP-очки</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {data.role_stats.map((r) => (
                      <TableRow key={r.id}><TableCell className="font-medium">{r.role}</TableCell><TableCell>{r.rounds_played}</TableCell><TableCell>{r.mvp_score}</TableCell></TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="rewards">
          <RewardsSection
            playerId={playerId!}
            canEdit={data.has_edit}
            kvalKits={data.kval_kits}
            items={data.custom_items}
            stats={data.custom_stats}
            talents={data.custom_talents}
            availableKvalKits={data.available_kval_kits}
            onChanged={invalidate}
          />
        </TabsContent>

        <TabsContent value="notes">
          <Card>
            <CardContent className="space-y-3 pt-6">
              {data.has_edit && (
                <div className="flex gap-2">
                  <Textarea value={noteDraft} onChange={(e) => setNoteDraft(e.target.value)} placeholder="Личное дело игрока..." />
                  <Button onClick={() => noteMutation.mutate(noteDraft)} disabled={!noteDraft.trim()}>Сохранить</Button>
                </div>
              )}
              {data.notes.map((n) => (
                <div key={n.id} className="rounded-md border p-3 text-sm">
                  <div className="whitespace-pre-wrap">{n.content}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{fmtDate(n.updated_at)}</div>
                </div>
              ))}
              {!data.notes.length && <div className="text-center text-muted-foreground">Заметок нет</div>}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="labels">
          <Card>
            <CardContent className="space-y-3 pt-6">
              {data.has_edit && (
                <div className="flex gap-2">
                  <Select value={labelCategoryId} onValueChange={setLabelCategoryId}>
                    <SelectTrigger className="w-64"><SelectValue placeholder="Выбрать метку" /></SelectTrigger>
                    <SelectContent>
                      {data.label_categories.map((c) => (
                        <SelectItem key={c.id} value={String(c.id)}>{c.icon} {c.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    disabled={!labelCategoryId}
                    onClick={() => assignLabelMutation.mutate(Number(labelCategoryId))}
                  >
                    Назначить
                  </Button>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                {data.label_pairs.map((lp) => (
                  <Badge key={lp.id} style={{ backgroundColor: lp.category.color }} className="gap-1.5 text-white">
                    {lp.category.icon} {lp.category.name}
                    {data.has_edit && (
                      <button onClick={() => removeLabelMutation.mutate(lp.id)} className="ml-1">✕</button>
                    )}
                  </Badge>
                ))}
                {!data.label_pairs.length && <div className="text-muted-foreground">Меток нет</div>}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="traps">
          <Card>
            <CardContent className="space-y-3 pt-6">
              {data.has_edit && (
                <div className="flex gap-2">
                  <Input value={trapReason} onChange={(e) => setTrapReason(e.target.value)} placeholder="Причина ловушки" />
                  <Button onClick={() => setTrapMutation.mutate(trapReason)}>Установить</Button>
                </div>
              )}
              {data.traps.map((t) => (
                <div key={t.id} className="flex items-center justify-between rounded-md border p-3 text-sm">
                  <div>
                    <div>{t.reason || "Без причины"}</div>
                    <div className="text-xs text-muted-foreground">{fmtDate(t.set_at)}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={t.active ? "destructive" : "secondary"}>{t.active ? "Активна" : "Снята"}</Badge>
                    {t.active && data.has_edit && (
                      <Button size="sm" variant="outline" onClick={() => deactivateTrapMutation.mutate(t.id)}>Снять</Button>
                    )}
                  </div>
                </div>
              ))}
              {!data.traps.length && <div className="text-center text-muted-foreground">Ловушек нет</div>}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="punishments">
          <Card className="overflow-hidden py-0">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Тип</TableHead>
                    <TableHead>Причина</TableHead>
                    <TableHead>Выдал</TableHead>
                    <TableHead>Дата</TableHead>
                    <TableHead>Статус</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.punishments.map((p) => (
                    <TableRow key={p.id}>
                      <TableCell className="font-medium">{p.type}</TableCell>
                      <TableCell>{p.reason || "—"}</TableCell>
                      <TableCell className="text-muted-foreground">{p.issued_by_name || "—"}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{fmtDate(p.issued_at)}</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_LABEL[p.status]?.variant ?? "secondary"}>
                          {STATUS_LABEL[p.status]?.label ?? p.status}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                  {!data.punishments.length && (
                    <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">Наказаний нет</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="history">
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader><CardTitle className="text-sm">Сессии</CardTitle></CardHeader>
              <CardContent className="space-y-1 text-sm">
                {data.sessions.slice(0, 10).map((s) => (
                  <div key={s.id} className="flex justify-between border-b py-1.5 last:border-0">
                    <span>#{s.session_number}</span>
                    <span className="text-muted-foreground">{fmtDate(s.login_time)}</span>
                    <span className="text-muted-foreground">{fmtDuration(s.session_duration)}</span>
                  </div>
                ))}
                {!data.sessions.length && <div className="text-muted-foreground">Нет данных</div>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-sm">IP-адреса</CardTitle></CardHeader>
              <CardContent className="space-y-1 text-sm">
                {data.ips.map((ip) => (
                  <div key={ip.id} className="flex justify-between border-b py-1.5 last:border-0">
                    <span className="font-mono">{ip.ip}</span>
                    <span className="text-muted-foreground">{fmtDate(ip.last_seen)}</span>
                  </div>
                ))}
                {!data.ips.length && <div className="text-muted-foreground">Нет данных</div>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-sm">Прошлые ники</CardTitle></CardHeader>
              <CardContent className="space-y-1 text-sm">
                {data.names.map((n) => (
                  <div key={n.id} className="flex justify-between border-b py-1.5 last:border-0">
                    <span>{n.nickname}</span>
                    <span className="text-muted-foreground">{fmtDate(n.last_seen)}</span>
                  </div>
                ))}
                {!data.names.length && <div className="text-muted-foreground">Нет данных</div>}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-sm">Убийства монстров</CardTitle></CardHeader>
              <CardContent className="space-y-1 text-sm">
                {data.kills.map((k) => (
                  <div key={k.id} className="flex justify-between border-b py-1.5 last:border-0">
                    <span>{k.monster_type}</span>
                    <span className="text-muted-foreground">{k.kills_count}</span>
                  </div>
                ))}
                {!data.kills.length && <div className="text-muted-foreground">Нет данных</div>}
              </CardContent>
            </Card>
            {data.role_actions.length > 0 && (
              <Card className="md:col-span-2">
                <CardHeader><CardTitle className="text-sm">Действия по ролям</CardTitle></CardHeader>
                <CardContent className="grid grid-cols-2 gap-1 text-sm sm:grid-cols-3">
                  {data.role_actions.map((a) => (
                    <div key={a.id} className="flex justify-between border-b py-1.5">
                      <span>{a.role} · {a.action_type}</span>
                      <span className="text-muted-foreground">{a.value}</span>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}

const ACCENT_CLASSES: Record<string, string> = {
  primary: "bg-primary/15 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  info: "bg-info/15 text-info",
  destructive: "bg-destructive/15 text-destructive",
  muted: "bg-muted text-muted-foreground",
}

function Stat({ label, value, icon: Icon, accent }: { label: string; value: number; icon: typeof Ship; accent: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border p-3">
      <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${ACCENT_CLASSES[accent]}`}>
        <Icon className="h-4.5 w-4.5" />
      </div>
      <div>
        <div className="text-xl font-bold leading-none">{value}</div>
        <div className="mt-1 text-xs text-muted-foreground">{label}</div>
      </div>
    </div>
  )
}
