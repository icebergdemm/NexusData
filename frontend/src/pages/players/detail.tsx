import { useEffect, useState } from "react"
import { useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Skull, Ship, HeartPulse, Swords, Bot, Trophy, Gift, StickyNote, Tags, Radio, Gavel, History as HistoryIcon, Star, Link2,
  Pencil, Check, X, KeyRound, Copy, Plus, RefreshCw, Trash2, LockKeyhole,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Skeleton } from "@/components/ui/skeleton"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription,
  AlertDialogFooter, AlertDialogCancel, AlertDialogAction,
} from "@/components/ui/alert-dialog"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { useCurrentAdmin } from "@/lib/auth"
import type { PlayerDetailResponse, Note, PunishmentRow } from "./detail-types"
import { RewardsSection } from "./rewards-section"
import { AdaptiveIcon } from "@/components/layout/PlatformIcon"

interface AdminOption { id: number; discord_username: string | null }

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

function copyToClipboard(text: string, label: string) {
  navigator.clipboard.writeText(text).then(() => toast.success(`${label} скопирован`)).catch(() => toast.error("Не удалось скопировать"))
}

function fmtPlayTime(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}

function fmtDuration(seconds: number | null): string {
  if (!seconds) return "-"
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
  const { data: admin } = useCurrentAdmin()
  const [noteDraft, setNoteDraft] = useState("")
  const [trapReason, setTrapReason] = useState("")
  const [labelCategoryId, setLabelCategoryId] = useState<string>("")
  const [linkDialogOpen, setLinkDialogOpen] = useState(false)
  const [linkAdminId, setLinkAdminId] = useState<string>("none")
  const [linkForcedName, setLinkForcedName] = useState("")
  const [forcedNameDialogOpen, setForcedNameDialogOpen] = useState(false)
  const [forcedNameDraft, setForcedNameDraft] = useState("")
  const [discordIdDraft, setDiscordIdDraft] = useState("")
  const [punishDialogOpen, setPunishDialogOpen] = useState(false)
  /** Наказание, для которого открыт диалог подтверждения отмены, и режим:
   *  "void" — просто пометить отменённым, "unban" — плюс снять бан на серверах. */
  const [revokeTarget, setRevokeTarget] = useState<{ row: PunishmentRow; mode: "void" | "unban" } | null>(null)
  const [punishForm, setPunishForm] = useState({ ptype: "warning", reason: "", expires_days: "", notes: "", server_name: "" })

  const queryKey = ["player-detail", playerId]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<PlayerDetailResponse>(`/api/v1/admin/players/${playerId}`),
  })

  useEffect(() => {
    setDiscordIdDraft(data?.player.discord_id ?? "")
  }, [data?.player.discord_id])

  const { data: adminsData } = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => api.get<{ admins: AdminOption[] }>("/api/v1/admin/users"),
    enabled: !!admin?.is_superuser,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey })

  const linkAdminMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/players/${playerId}/link-admin`, {
      admin_id: linkAdminId === "none" ? null : Number(linkAdminId),
      forced_display_name: linkForcedName.trim() || null,
    }),
    onSuccess: () => { toast.success("Связь обновлена"); setLinkDialogOpen(false); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const openLinkDialog = () => {
    setLinkAdminId(data?.player.linked_admin_id ? String(data.player.linked_admin_id) : "none")
    setLinkForcedName(data?.player.forced_display_name ?? "")
    setLinkDialogOpen(true)
  }

  const forcedNameMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/players/${playerId}/forced-name`, {
      forced_display_name: forcedNameDraft.trim() || null,
    }),
    onSuccess: () => { toast.success("Имя обновлено"); setForcedNameDialogOpen(false); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const openForcedNameDialog = () => {
    setForcedNameDraft(data?.player.forced_display_name ?? "")
    setForcedNameDialogOpen(true)
  }

  const rankKitMutation = useMutation({
    mutationFn: (body: { rank_id?: number; kit_level?: number }) =>
      api.post(`/api/v1/admin/players/${playerId}/rank-kit`, body),
    onSuccess: () => { toast.success("Данные игрока обновлены"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const syncMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/players/${playerId}/sync`, {}),
    onSuccess: () => toast.success("Синхронизация запрошена — сервер обновит кэш при следующем опросе очереди"),
    onError: (e: Error) => toast.error(e.message),
  })

  const discordIdMutation = useMutation({
    mutationFn: (discord_id: string) => api.post(`/api/v1/admin/players/${playerId}/discord-id`, { discord_id }),
    onSuccess: () => { toast.success("Discord ID сохранён и подтверждён"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const noteMutation = useMutation({
    mutationFn: (content: string) => api.post(`/api/v1/admin/players/${playerId}/notes`, { content }),
    onSuccess: () => { toast.success("Заметка сохранена"); setNoteDraft(""); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const updateNoteMutation = useMutation({
    mutationFn: ({ noteId, content }: { noteId: number; content: string }) =>
      api.post(`/api/v1/admin/players/${playerId}/notes/${noteId}`, { content }),
    onSuccess: () => { toast.success("Заметка обновлена"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteNoteMutation = useMutation({
    mutationFn: (noteId: number) => api.delete(`/api/v1/admin/players/${playerId}/notes/${noteId}`),
    onSuccess: () => { toast.success("Заметка удалена"); invalidate() },
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
    onError: (e: Error) => toast.error(e.message),
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

  // Отмена наказания/разбана — действие необратимое для игрока (бан снимается
  // на серверах), поэтому спрашиваем подтверждение, как и на общей странице
  // наказаний это делает отдельная кнопка.
  const voidPunishmentMutation = useMutation({
    mutationFn: (punishmentId: number) => api.post(`/api/v1/admin/punishments/${punishmentId}/void`),
    onSuccess: () => { toast.success("Наказание отменено"); setRevokeTarget(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const unbanPunishmentMutation = useMutation({
    // server_name: null — разбан уходит на оба сервера (см. api_punishment_unban).
    mutationFn: (punishmentId: number) => api.post(`/api/v1/admin/punishments/${punishmentId}/unban`, { server_name: null }),
    onSuccess: () => { toast.success("Разбан поставлен в очередь, наказание отменено"); setRevokeTarget(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const toggleSecretMutation = useMutation({
    mutationFn: () => api.post<{ is_secret: boolean }>(`/api/v1/admin/players/${playerId}/toggle-secret`, {}),
    onSuccess: (res) => {
      toast.success(res.is_secret ? "Профиль засекречен" : "Профиль рассекречен")
      invalidate()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const createPunishmentMutation = useMutation({
    mutationFn: () => api.post("/api/v1/admin/punishments", {
      player_steamid: data?.player.player_steamid,
      ptype: punishForm.ptype,
      reason: punishForm.reason,
      expires_days: punishForm.expires_days ? Number(punishForm.expires_days) : null,
      notes: punishForm.notes,
      server_name: punishForm.server_name || null,
    }),
    onSuccess: () => {
      toast.success("Наказание выдано")
      setPunishDialogOpen(false)
      setPunishForm({ ptype: "warning", reason: "", expires_days: "", notes: "", server_name: "" })
      invalidate()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (isLoading || !data) {
    return <div className="space-y-2"><Skeleton className="h-40" /><Skeleton className="h-64" /></div>
  }

  if (data.classified) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <div className="flex flex-col items-center gap-3 text-muted-foreground">
            <span className="flex items-center gap-2"><AdaptiveIcon emoji="🔒" icon={LockKeyhole} /> Профиль засекречен. Нужен допуск (право «Игроки → Доступ к засекреченным профилям») или права суперпользователя.</span>
            {admin?.is_superuser && (
              <Button size="sm" variant="outline" disabled={toggleSecretMutation.isPending} onClick={() => toggleSecretMutation.mutate()}>
                <LockKeyhole className="size-4" /> Рассекретить
              </Button>
            )}
          </div>
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
      <Card className="overflow-hidden py-0">
        <div className="bg-gradient-to-r from-primary/15 via-card to-card px-6 py-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold">{player.player_last_time_name}</h1>
                {player.forced_display_name && (
                  <span className="text-[11px] text-muted-foreground" title={`Закреплённое имя: ${player.forced_display_name}`}>🔗 {player.forced_display_name}</span>
                )}
                {data.is_online && <Badge className="bg-success text-white">В сети</Badge>}
                {player.is_secret && <Badge variant="outline" className="gap-1 border-warning/40 text-warning"><AdaptiveIcon emoji="🔒" icon={LockKeyhole} />Секретно</Badge>}
                {admin?.is_superuser && (
                  <Button
                    size="sm"
                    variant={player.is_secret ? "default" : "outline"}
                    disabled={toggleSecretMutation.isPending}
                    onClick={() => toggleSecretMutation.mutate()}
                    title={player.is_secret ? "Рассекретить профиль" : "Засекретить профиль"}
                  >
                    <LockKeyhole className="size-3.5" /> {player.is_secret ? "Рассекретить" : "Засекретить"}
                  </Button>
                )}
                {admin?.is_superuser && (
                  <Button size="icon-xs" variant="ghost" onClick={openLinkDialog} title="Связать с пользователем Nexus Data">
                    <Link2 className="size-3.5" />
                  </Button>
                )}
                {/* players:set_forced_name — доступно и не-суперпользователям, в
                    отличие от привязки аккаунта выше: меняет только отображаемое
                    имя, без выдачи сайтовых прав через аккаунт. Суперам кнопка не
                    нужна отдельно — у них уже есть полный диалог по ссылке выше. */}
                {!admin?.is_superuser && data.can_set_forced_name && (
                  <Button size="icon-xs" variant="ghost" onClick={openForcedNameDialog} title="Установить принудительное имя">
                    <Pencil className="size-3.5" />
                  </Button>
                )}
                {data.has_edit && (
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    disabled={syncMutation.isPending}
                    onClick={() => syncMutation.mutate()}
                    title="Принудительно синхронизировать кэш игрока на сервере"
                  >
                    <RefreshCw className="size-3.5" />
                  </Button>
                )}
              </div>
              <div className="mt-0.5 flex items-center gap-1 font-mono text-xs text-muted-foreground">
                SteamID: {player.player_steamid}
                <Button
                  size="icon-xs"
                  variant="ghost"
                  className="size-4"
                  onClick={() => copyToClipboard(player.player_steamid, "SteamID")}
                  title="Скопировать SteamID"
                >
                  <Copy className="size-3" />
                </Button>
              </div>
              {player.discord_id && (
                <div className="mt-0.5 flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                  Discord: {player.discord_id}
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    className="size-4"
                    onClick={() => copyToClipboard(player.discord_id!, "Discord ID")}
                    title="Скопировать Discord ID"
                  >
                    <Copy className="size-3" />
                  </Button>
                  {player.discord_id_confirmed ? (
                    <Badge variant="outline" className="border-success/40 text-[10px] text-success">Подтверждён</Badge>
                  ) : (
                    <Badge variant="outline" className="border-warning/40 text-[10px] text-warning">Не точный</Badge>
                  )}
                </div>
              )}
              {data.server_privileges.length > 0 && (
                <div className="mt-1 flex flex-col gap-1">
                  {data.server_privileges.map((priv) => (
                    <div key={priv.id} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <KeyRound className="size-3 shrink-0" />
                      {priv.server_group_name ?? "Индивидуальные права"}
                      <Badge variant="outline" className="text-[10px]">{priv.server_scope ?? "Все сервера"}</Badge>
                      {priv.is_auto ? (
                        <Badge variant="outline" className="border-info/40 text-[10px] text-info">
                          От отдела: {priv.auto_source_department_name ?? "—"}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px]">Назначено вручную</Badge>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="flex gap-4 text-left text-sm">
              <div><div className="text-lg font-bold">{fmtPlayTime(player.play_time_hours)}</div><div className="text-xs text-muted-foreground">наиграно</div></div>
              <Separator orientation="vertical" className="h-10" />
              <div><div className="text-lg font-bold">{data.ranks[String(player.rank_id)] ?? player.rank_id}</div><div className="text-xs text-muted-foreground">звание</div></div>
              <Separator orientation="vertical" className="h-10" />
              <div><div className={`text-lg font-bold ${activePunishments ? "text-destructive" : ""}`}>{activePunishments}</div><div className="text-xs text-muted-foreground">активных наказаний</div></div>
            </div>
          </div>
          <div className="mt-2 text-xs text-muted-foreground">Последний вход: {fmtDate(player.last_login_time)}{player.last_ip && ` · IP ${player.last_ip}`}</div>
        </div>

        {data.has_edit && (
          <CardContent className="grid grid-cols-1 gap-4 border-t py-4 sm:grid-cols-3">
            <div>
              <div className="mb-1 text-xs text-muted-foreground">Звание</div>
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
              <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                Discord ID
                {player.discord_id && (player.discord_id_confirmed
                  ? <Badge variant="outline" className="border-success/40 text-[10px] text-success">Подтверждён</Badge>
                  : <Badge variant="outline" className="border-warning/40 text-[10px] text-warning">Не точный</Badge>)}
              </div>
              <div className="flex gap-1.5">
                <Input
                  value={discordIdDraft}
                  onChange={(e) => setDiscordIdDraft(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && discordIdMutation.mutate(discordIdDraft)}
                />
                <Button
                  size="sm"
                  disabled={discordIdMutation.isPending}
                  onClick={() => discordIdMutation.mutate(discordIdDraft)}
                >
                  <Check className="size-3.5" /> Сохранить
                </Button>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">Изменение вручную помечает ID как подтверждённый.</p>
            </div>
          </CardContent>
        )}
      </Card>

      <Tabs defaultValue="stats">
        <TabsList className="w-full flex-wrap">
          <TabsTrigger value="stats" className="flex-1 gap-1.5"><Trophy className="h-3.5 w-3.5" /> Статистика</TabsTrigger>
          <TabsTrigger value="rewards" className="flex-1 gap-1.5"><Gift className="h-3.5 w-3.5" /> Награды {rewardsCount > 0 && `(${rewardsCount})`}</TabsTrigger>
          <TabsTrigger value="notes" className="flex-1 gap-1.5"><StickyNote className="h-3.5 w-3.5" /> Заметки ({data.notes.length})</TabsTrigger>
          <TabsTrigger value="labels" className="flex-1 gap-1.5"><Tags className="h-3.5 w-3.5" /> Метки ({data.label_pairs.length})</TabsTrigger>
          <TabsTrigger value="traps" className="flex-1 gap-1.5"><Radio className="h-3.5 w-3.5" /> Ловушки ({data.traps.length})</TabsTrigger>
          <TabsTrigger value="punishments" className="flex-1 gap-1.5"><Gavel className="h-3.5 w-3.5" /> Наказания ({data.punishments.length})</TabsTrigger>
          <TabsTrigger value="history" className="flex-1 gap-1.5"><HistoryIcon className="h-3.5 w-3.5" /> История</TabsTrigger>
        </TabsList>

        <TabsContent value="stats" className="space-y-4">
          <Card>
            <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-3">
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
                <div className="px-10 py-8">
                  <Table>
                    <TableHeader><TableRow><TableHead>Роль</TableHead><TableHead>Раундов</TableHead><TableHead>MVP-очки</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {data.role_stats.map((r) => (
                        <TableRow key={r.id}><TableCell className="font-medium">{r.role}</TableCell><TableCell>{r.rounds_played}</TableCell><TableCell>{r.mvp_score}</TableCell></TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="rewards">
          <RewardsSection
            playerId={playerId!}
            canEdit={data.can_manage_rewards}
            kvalKits={data.kval_kits}
            items={data.custom_items}
            jobItems={data.custom_job_items}
            stats={data.custom_stats}
            talents={data.custom_talents}
            availableKvalKits={data.available_kval_kits}
            reference={data.reward_reference}
            onChanged={invalidate}
          />
        </TabsContent>

        <TabsContent value="notes">
          <Card>
            <CardContent className="space-y-3">
              {data.can_manage_notes && (
                <div className="flex gap-2">
                  <Textarea value={noteDraft} onChange={(e) => setNoteDraft(e.target.value)} placeholder="Личное дело игрока..." />
                  <Button onClick={() => noteMutation.mutate(noteDraft)} disabled={!noteDraft.trim()}>Сохранить</Button>
                </div>
              )}
              {data.notes.map((n) => (
                <NoteRow
                  key={n.id}
                  note={n}
                  canEdit={data.can_manage_notes}
                  onSave={(content) => updateNoteMutation.mutate({ noteId: n.id, content })}
                  onDelete={() => deleteNoteMutation.mutate(n.id)}
                  saving={updateNoteMutation.isPending}
                  deleting={deleteNoteMutation.isPending}
                />
              ))}
              {!data.notes.length && <div className="text-center text-muted-foreground">Заметок нет</div>}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="labels">
          <Card>
            <CardContent className="space-y-3">
              {data.can_manage_labels && (
                <div className="flex gap-2">
                  <Select value={labelCategoryId} onValueChange={setLabelCategoryId}>
                    <SelectTrigger className="w-64"><SelectValue placeholder="Выбрать метку" /></SelectTrigger>
                    <SelectContent>
                      {data.label_categories.map((c) => (
                        <SelectItem key={c.id} value={String(c.id)}><span className="flex items-center gap-2"><span aria-hidden="true">{c.icon || "🏷"}</span>{c.name}</span></SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    disabled={!labelCategoryId || assignLabelMutation.isPending}
                    onClick={() => assignLabelMutation.mutate(Number(labelCategoryId))}
                  >
                    Назначить
                  </Button>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                {data.label_pairs.map((lp) => (
                  <Badge key={lp.id} style={{ backgroundColor: lp.category.color }} className="gap-1.5 text-white">
                    <span aria-hidden="true">{lp.category.icon || "🏷"}</span>{lp.category.name}
                    {data.can_manage_labels && (
                      <button
                        type="button"
                        onClick={() => removeLabelMutation.mutate(lp.id)}
                        disabled={removeLabelMutation.isPending}
                        className="ml-1 inline-flex"
                        aria-label={`Снять метку ${lp.category.name}`}
                      >
                        <X className="size-3" aria-hidden="true" />
                      </button>
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
            <CardContent className="space-y-3">
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

        <TabsContent value="punishments" className="space-y-3">
          {data.can_issue_punishment && (
            <div className="flex justify-end">
              <Button onClick={() => setPunishDialogOpen(true)}><Plus className="h-4 w-4" /> Выдать наказание</Button>
            </div>
          )}
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
                    {data.can_revoke_punishment && <TableHead className="text-right">Действия</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.punishments.map((p) => (
                    <TableRow key={p.id}>
                      <TableCell className="font-medium">{p.type}</TableCell>
                      <TableCell>{p.reason || "-"}</TableCell>
                      <TableCell className="text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          {p.issued_by_name || "-"}
                          {p.issued_by_was_superuser && (
                            <span title="Выдано суперпользователем"><Star className="size-3.5 fill-warning text-warning" /></span>
                          )}
                          {p.notes && (
                            <span title={`Заметка для админов: ${p.notes}`}><StickyNote className="size-3.5 text-info" /></span>
                          )}
                        </span>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">{fmtDate(p.issued_at)}</TableCell>
                      <TableCell>
                        <Badge variant={STATUS_LABEL[p.status]?.variant ?? "secondary"}>
                          {STATUS_LABEL[p.status]?.label ?? p.status}
                        </Badge>
                      </TableCell>
                      {data.can_revoke_punishment && (
                        <TableCell className="text-right">
                          {p.status === "active" ? (
                            <div className="flex justify-end gap-1.5 whitespace-nowrap">
                              {p.type === "ban" && (
                                <Button size="sm" variant="outline" onClick={() => setRevokeTarget({ row: p, mode: "unban" })}>
                                  Разбан
                                </Button>
                              )}
                              <Button size="sm" variant="outline" onClick={() => setRevokeTarget({ row: p, mode: "void" })}>
                                Отменить
                              </Button>
                            </div>
                          ) : (
                            <span className="text-xs text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                  {!data.punishments.length && (
                    <TableRow><TableCell colSpan={data.can_revoke_punishment ? 6 : 5} className="py-8 text-center text-muted-foreground">Наказаний нет</TableCell></TableRow>
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
              <CardContent className="p-0">
                <ScrollArea className="h-64">
                  <div className="space-y-1 p-4 pt-0 text-sm">
                    {data.sessions.map((s) => (
                      <div key={s.id} className="flex justify-between border-b py-1.5 last:border-0">
                        <span>#{s.session_number}</span>
                        <span className="text-muted-foreground">{fmtDate(s.login_time)}</span>
                        <span className="text-muted-foreground">{fmtDuration(s.session_duration)}</span>
                      </div>
                    ))}
                    {!data.sessions.length && <div className="text-muted-foreground">Нет данных</div>}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-sm">IP-адреса</CardTitle></CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-64">
                  <div className="space-y-1 p-4 pt-0 text-sm">
                    {data.ips.map((ip) => (
                      <div key={ip.id} className="flex justify-between border-b py-1.5 last:border-0">
                        <span className="font-mono">{ip.ip}</span>
                        <span className="text-muted-foreground">{fmtDate(ip.last_seen)}</span>
                      </div>
                    ))}
                    {!data.ips.length && <div className="text-muted-foreground">Нет данных</div>}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-sm">Прошлые ники</CardTitle></CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-64">
                  <div className="space-y-1 p-4 pt-0 text-sm">
                    {data.names.map((n) => (
                      <div key={n.id} className="flex justify-between border-b py-1.5 last:border-0">
                        <span>{n.nickname}</span>
                        <span className="text-muted-foreground">{fmtDate(n.last_seen)}</span>
                      </div>
                    ))}
                    {!data.names.length && <div className="text-muted-foreground">Нет данных</div>}
                  </div>
                </ScrollArea>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-sm">Убийства монстров</CardTitle></CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-64">
                  <div className="space-y-1 p-4 pt-0 text-sm">
                    {data.kills.map((k) => (
                      <div key={k.id} className="flex justify-between border-b py-1.5 last:border-0">
                        <span>{k.monster_type}</span>
                        <span className="text-muted-foreground">{k.kills_count}</span>
                      </div>
                    ))}
                    {!data.kills.length && <div className="text-muted-foreground">Нет данных</div>}
                  </div>
                </ScrollArea>
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

      <Dialog open={punishDialogOpen} onOpenChange={setPunishDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Выдать «{data.punishment_type_labels[punishForm.ptype] ?? punishForm.ptype}»</DialogTitle>
            <DialogDescription>Наказание будет привязано к {player.player_steamid} и записано в историю игрока.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="mb-1.5 block">Тип</Label>
              <Select value={punishForm.ptype} onValueChange={(v) => setPunishForm((f) => ({ ...f, ptype: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {data.punishment_types.map((t) => (
                    <SelectItem key={t} value={t}>{data.punishment_type_labels[t] ?? t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="mb-1.5 block">Причина</Label>
              <Input value={punishForm.reason} onChange={(e) => setPunishForm((f) => ({ ...f, reason: e.target.value }))} />
            </div>
            <div>
              <Label className="mb-1.5 block">Срок (дней, пусто = навсегда)</Label>
              <Input type="number" value={punishForm.expires_days} onChange={(e) => setPunishForm((f) => ({ ...f, expires_days: e.target.value }))} />
            </div>
            {(punishForm.ptype === "ban" || punishForm.ptype === "kick") && (
              <div>
                <Label className="mb-1.5 block">Сервер (пусто = оба)</Label>
                <Input value={punishForm.server_name} onChange={(e) => setPunishForm((f) => ({ ...f, server_name: e.target.value }))} placeholder="ASPER1 / ASPER2" />
              </div>
            )}
            <div>
              <Label className="mb-1.5 block">Внутренние заметки</Label>
              <Textarea value={punishForm.notes} onChange={(e) => setPunishForm((f) => ({ ...f, notes: e.target.value }))} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPunishDialogOpen(false)}>Отмена</Button>
            <Button disabled={createPunishmentMutation.isPending} onClick={() => createPunishmentMutation.mutate()}>
              Выдать
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={revokeTarget != null} onOpenChange={(o) => !o && setRevokeTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {revokeTarget?.mode === "unban" ? "Снять бан?" : "Отменить наказание?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {revokeTarget && (
                <>
                  «{data.punishment_type_labels[revokeTarget.row.type] ?? revokeTarget.row.type}»
                  {revokeTarget.row.reason ? ` — ${revokeTarget.row.reason}` : ""}.{" "}
                  {revokeTarget.mode === "unban"
                    ? "Наказание будет помечено отменённым, а на игровые серверы уйдёт разбан игрока."
                    : "Наказание будет помечено отменённым и перестанет учитываться как активное. Запись из истории не удаляется."}
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Назад</AlertDialogCancel>
            <AlertDialogAction
              disabled={voidPunishmentMutation.isPending || unbanPunishmentMutation.isPending}
              onClick={(e) => {
                // Диалог закрывается по onSuccess мутации, а не мгновенно:
                // иначе провалившийся запрос выглядел бы как выполненный.
                e.preventDefault()
                if (!revokeTarget) return
                if (revokeTarget.mode === "unban") unbanPunishmentMutation.mutate(revokeTarget.row.id)
                else voidPunishmentMutation.mutate(revokeTarget.row.id)
              }}
            >
              {revokeTarget?.mode === "unban" ? "Разбанить" : "Отменить наказание"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={linkDialogOpen} onOpenChange={setLinkDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Связь с пользователем Nexus Data</DialogTitle>
            <DialogDescription>
              Привяжите этого игрока к аккаунту администратора и/или задайте принудительное отображаемое имя.
              Это имя будет показываться в рейтинге и наказаниях вместо игрового ника, независимо от того,
              откуда пришло наказание — с сайта или через API игрового клиента.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="mb-1.5 block">Привязанный администратор</Label>
              <Select value={linkAdminId} onValueChange={setLinkAdminId}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Без привязки</SelectItem>
                  {adminsData?.admins.map((a) => (
                    <SelectItem key={a.id} value={String(a.id)}>{a.discord_username}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="mb-1.5 block">Принудительное отображаемое имя</Label>
              <Input
                value={linkForcedName}
                onChange={(e) => setLinkForcedName(e.target.value)}
                placeholder={player.player_last_time_name}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLinkDialogOpen(false)}>Отмена</Button>
            <Button disabled={linkAdminMutation.isPending} onClick={() => linkAdminMutation.mutate()}>Сохранить</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={forcedNameDialogOpen} onOpenChange={setForcedNameDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Принудительное имя</DialogTitle>
            <DialogDescription>
              Задайте отображаемое имя без привязки к аккаунту администратора. Оно будет показываться
              в рейтинге и наказаниях вместо игрового ника. Оставьте поле пустым, чтобы вернуть игровой ник.
            </DialogDescription>
          </DialogHeader>
          <div>
            <Label className="mb-1.5 block">Отображаемое имя</Label>
            <Input
              value={forcedNameDraft}
              onChange={(e) => setForcedNameDraft(e.target.value)}
              placeholder={player.player_last_time_name}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setForcedNameDialogOpen(false)}>Отмена</Button>
            <Button disabled={forcedNameMutation.isPending} onClick={() => forcedNameMutation.mutate()}>Сохранить</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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

function NoteRow({ note, canEdit, onSave, onDelete, saving, deleting }: {
  note: Note; canEdit: boolean; onSave: (content: string) => void; onDelete: () => void; saving: boolean; deleting: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(note.content)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const startEdit = () => { setDraft(note.content); setEditing(true) }
  const save = () => {
    if (!draft.trim()) return
    onSave(draft.trim())
    setEditing(false)
  }

  return (
    <div className="rounded-md border p-3 text-sm">
      {editing ? (
        <div className="space-y-2">
          <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus rows={3} />
          <div className="flex gap-1.5">
            <Button size="sm" disabled={!draft.trim() || saving} onClick={save}><Check className="size-3.5" /> Сохранить</Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)}><X className="size-3.5" /> Отмена</Button>
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-start justify-between gap-2">
            <div className="whitespace-pre-wrap">{note.content}</div>
            {canEdit && (
              <div className="flex shrink-0 gap-0.5">
                <Button size="icon-xs" variant="ghost" onClick={startEdit} title="Редактировать">
                  <Pencil className="size-3.5" />
                </Button>
                <Button size="icon-xs" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => setConfirmDelete(true)} title="Удалить">
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            )}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {note.admin_name ? `Оставил(а): ${note.admin_name} · ` : ""}{note.updated_at ? formatBrowserDate(note.updated_at) : "-"}
          </div>
        </>
      )}

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить заметку?</AlertDialogTitle>
            <AlertDialogDescription>Действие необратимо. Заметка будет удалена из личного дела игрока безвозвратно.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction disabled={deleting} onClick={() => { onDelete(); setConfirmDelete(false) }}>Удалить</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
