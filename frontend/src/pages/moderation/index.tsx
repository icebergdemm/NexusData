import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ShieldAlert, MapPin, Ship, Target, Users2, Copy, IdCard, Terminal, Circle } from "lucide-react"
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
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import { AppContextMenu } from "@/components/layout/AppContextMenu"
import { AdaptiveIcon } from "@/components/layout/PlatformIcon"
import { PlayerFiltersToolbar, type PlayerFilterSortOption } from "@/components/layout/PlayerFiltersSheet"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { filterAndSortPlayers, resetPlayerFilters, type DiscordLinkFilter, type PlayerSort } from "@/lib/player-filters"
import type { ModerationOverview, OfflinePlayer, OnlinePlayer } from "./types"
import { AnomaliesTab } from "./AnomaliesTab"
import { ConsoleTab } from "./ConsoleTab"

function copyToClipboard(text: string, label: string) {
  navigator.clipboard.writeText(text).then(() => toast.success(`${label} скопирован`)).catch(() => toast.error("Не удалось скопировать"))
}
function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}
function fmtPlayTime(hours: number | null | undefined): string {
  if (hours == null) return "-"
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}
function onlineDisplayName(p: OnlinePlayer): string {
  return p.player_name || p.player_steamid
}
function offlineDisplayName(p: OfflinePlayer): string {
  return p.player_last_time_name
}

function ForcedNameBadge({ forcedName }: { forcedName: string | null }) {
  if (!forcedName) return null
  return (
    <span
      className="ml-1 inline-flex items-center text-[11px]"
      title={`Закреплённое имя: ${forcedName}`}
      aria-label={`Закреплённое имя: ${forcedName}`}
    >
      🔗
    </span>
  )
}

function RankMismatchBadge({ nickname, rankId, ranks }: { nickname: string | null; rankId: number | null | undefined; ranks: Record<string, string> | undefined }) {
  if (!nickname || rankId == null || !ranks) return null
  const rankName = ranks[String(rankId)]
  if (!rankName) return null
  const nickTrim = nickname.trim()
  if (!nickTrim) return null
  // Токенизация по буквам/цифрам — унифицирует "Ст.", "Ст", "Капитан-лейтенант" vs "Капитан Лейтенант",
  // "1-ст" vs "1 ст" и т.п. Звание может быть из 1-3 токенов (например "Ст. Матрос" -> ["ст","матрос"],
  // "Старшина 1-ст" -> ["старшина","1","ст"], "Капитан-лейтенант" -> ["капитан","лейтенант"]).
  const tokenRe = /[a-zа-яё0-9]+/gi
  const rankTokens = (rankName.toLowerCase().match(tokenRe) ?? []) as string[]
  const nickTokens = (nickTrim.toLowerCase().match(tokenRe) ?? []) as string[]
  if (!rankTokens.length) return null
  if (nickTokens.length < rankTokens.length) {
    const prefix = nickTrim.split(/\s+/).slice(0, rankTokens.length).join(" ") || nickTrim.split(/\s+/)[0]!
    return (
      <Badge variant="outline" className="ml-1.5 border-destructive/40 bg-destructive/10 text-destructive text-[10px]" title={`Звание: ${rankName}, ник начинается с «${prefix}» — несоответствие`}>
        ⚠︎ звание
      </Badge>
    )
  }
  const isMatch = rankTokens.every((t, i) => t === nickTokens[i])
  if (isMatch) return null
  // Для подсказки показываем столько слов ника, сколько токенов в звании (минимум 1)
  const nickWords = nickTrim.split(/\s+/)
  // Оценка количества слов ника, покрывающих rankTokens: считаем токены пословно
  let usedWords = 0
  let consumed = 0
  for (const w of nickWords) {
    const c = (w.toLowerCase().match(tokenRe) ?? []).length || 1
    consumed += c
    usedWords += 1
    if (consumed >= rankTokens.length) break
  }
  const prefix = nickWords.slice(0, Math.max(1, usedWords)).join(" ")
  return (
    <Badge variant="outline" className="ml-1.5 border-destructive/40 bg-destructive/10 text-destructive text-[10px]" title={`Звание: ${rankName}, ник начинается с «${prefix}» — несоответствие`}>
      ⚠︎ звание
    </Badge>
  )
}

type ActionType = "kick" | "ban" | "message"

const ONLINE_SORT_OPTIONS: readonly PlayerFilterSortOption[] = [
  { key: "name", label: "Имя" },
  { key: "server", label: "Сервер" },
  { key: "join_time", label: "Дата входа" },
  { key: "play_time", label: "Наиграно" },
]

const OFFLINE_SORT_OPTIONS: readonly PlayerFilterSortOption[] = [
  { key: "name", label: "Имя" },
  { key: "first_login", label: "Первый вход" },
  { key: "last_login", label: "Последний вход" },
  { key: "play_time", label: "Наиграно" },
]

const DEFAULT_ONLINE_SORT: PlayerSort = { key: "join_time", direction: "desc" }
const DEFAULT_OFFLINE_SORT: PlayerSort = { key: "last_login", direction: "desc" }

export function ModerationPage() {
  const [search, setSearch] = useState("")
  const [searchQuery, setSearchQuery] = useState<string | undefined>()
  const [actionTarget, setActionTarget] = useState<{ steamid: string; name: string; type: ActionType } | null>(null)
  const [actionReason, setActionReason] = useState("")
  const [actionServer, setActionServer] = useState("")
  const [summaryPlayerId, setSummaryPlayerId] = useState<number | null>(null)
  const [activeTab, setActiveTab] = useState("status")
  const [discordFilter, setDiscordFilter] = useState<DiscordLinkFilter>("all")
  const [labelId, setLabelId] = useState("all")
  const [rankId, setRankId] = useState("all")
  const [onlineSort, setOnlineSort] = useState<PlayerSort>(DEFAULT_ONLINE_SORT)
  const [offlineSort, setOfflineSort] = useState<PlayerSort>(DEFAULT_OFFLINE_SORT)
  // Быстрая выдача предупреждения прямо из списка онлайна: отдельно от
  // actionTarget, потому что это НЕ действие на сервере, а запись наказания
  // (сервер получит из неё окно с причиной автоматически).
  const [warnTarget, setWarnTarget] = useState<{ steamid: string; name: string } | null>(null)
  const [warnReason, setWarnReason] = useState("")
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

  const warnMutation = useMutation({
    mutationFn: () => api.post("/api/v1/admin/punishments", {
      player_steamid: warnTarget?.steamid,
      ptype: "warning",
      reason: warnReason,
    }),
    onSuccess: () => {
      toast.success("Предупреждение выдано — игрок увидит окно с причиной")
      setWarnTarget(null)
      setWarnReason("")
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
  const playerFilters = { discordFilter, labelId, rankId }
  const filteredOnlinePlayers = filterAndSortPlayers(data.online_players, playerFilters, onlineSort)
  const filteredOfflineRows = filterAndSortPlayers(offlineRows, playerFilters, offlineSort)
  const labelOptions = [
    { value: "all", label: "Все метки" },
    ...data.label_categories.map((category) => ({ value: String(category.id), label: category.name })),
  ]
  const rankOptions = [
    { value: "all", label: "Все звания" },
    ...Object.entries(data.ranks).map(([id, name]) => ({ value: id, label: name })),
  ]

  return (
    <div>
      <PageHeader title="Модерирование" description="Онлайн-статус серверов, поиск игроков и события античита" icon={ShieldAlert} />

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="status" className="gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-success" /> Онлайн ({filteredOnlinePlayers.length})
          </TabsTrigger>
          <TabsTrigger value="banhammer">Вышедшие игроки</TabsTrigger>
          <TabsTrigger value="anticheat" className="gap-1.5">
            Античит {unreviewedCount > 0 && <Badge variant="destructive" className="h-4 px-1.5 text-[10px]">{unreviewedCount}</Badge>}
          </TabsTrigger>
          <TabsTrigger value="anomalies" className="gap-1.5">
            <ShieldAlert className="size-3.5" /> Аномалии
          </TabsTrigger>
          <TabsTrigger value="console" className="gap-1.5">
            <Terminal className="size-3.5" /> Консоль
          </TabsTrigger>
        </TabsList>

        {activeTab === "status" && (
          <PlayerFiltersToolbar
            tabLabel="Онлайн"
            discordFilter={discordFilter}
            labelId={labelId}
            labelOptions={labelOptions}
            rankId={rankId}
            rankOptions={rankOptions}
            sort={onlineSort}
            defaultSort={DEFAULT_ONLINE_SORT}
            sortOptions={ONLINE_SORT_OPTIONS}
            onDiscordFilterChange={setDiscordFilter}
            onLabelIdChange={setLabelId}
            onRankIdChange={setRankId}
            onSortChange={setOnlineSort}
            onReset={() => { const reset = resetPlayerFilters(); setDiscordFilter(reset.discordFilter); setLabelId(reset.labelId); setRankId(reset.rankId); setOnlineSort(DEFAULT_ONLINE_SORT) }}
          />
        )}
        {activeTab === "banhammer" && (
          <PlayerFiltersToolbar
            tabLabel="Вышедшие игроки"
            discordFilter={discordFilter}
            labelId={labelId}
            labelOptions={labelOptions}
            rankId={rankId}
            rankOptions={rankOptions}
            sort={offlineSort}
            defaultSort={DEFAULT_OFFLINE_SORT}
            sortOptions={OFFLINE_SORT_OPTIONS}
            onDiscordFilterChange={setDiscordFilter}
            onLabelIdChange={setLabelId}
            onRankIdChange={setRankId}
            onSortChange={setOfflineSort}
            onReset={() => { const reset = resetPlayerFilters(); setDiscordFilter(reset.discordFilter); setLabelId(reset.labelId); setRankId(reset.rankId); setOfflineSort(DEFAULT_OFFLINE_SORT) }}
          />
        )}

        <TabsContent value="status" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            {Object.entries(data.round_states).map(([sn, rs]) => (
              <Card key={sn} className="overflow-hidden py-0">
                <CardHeader className="flex flex-row items-center justify-between space-y-0 border-b bg-muted/30 py-3">
                  <CardTitle className="text-sm font-semibold">{sn}</CardTitle>
                  <Badge className={rs.active ? "bg-success text-white" : ""} variant={rs.active ? "default" : "secondary"}>
                    <AdaptiveIcon emoji={rs.active ? "🟢" : "⚪"} icon={Circle} className={rs.active ? "fill-current" : ""} />
                    {rs.active ? "Раунд идёт" : "Раунд не идёт"}
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
                    <TableHead>Наиграно</TableHead>
                    {(data.can_edit || data.can_issue_punishment) && <TableHead className="text-right">Действия</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredOnlinePlayers.map((p) => {
                    const name = onlineDisplayName(p)
                    return (
                    <AppContextMenu
                      key={p.player_steamid}
                      actions={[
                        ...(p.player_id ? [{ label: "Открыть профиль", icon: ShieldAlert, onSelect: () => setSummaryPlayerId(p.player_id as number) }] : []),
                        { label: "Скопировать SteamID", icon: IdCard, onSelect: () => copyToClipboard(p.player_steamid, "SteamID") },
                        { label: "Скопировать имя", icon: Copy, onSelect: () => copyToClipboard(name, "Имя") },
                        ...(data.can_issue_punishment ? [
                          { label: "Выдать предупреждение", separatorBefore: true, onSelect: () => setWarnTarget({ steamid: p.player_steamid, name }) },
                        ] : []),
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
                            <PlayerHoverLink playerId={p.player_id}>{name}</PlayerHoverLink>
                          ) : name}
                          {p.is_new_player && <Badge variant="outline" className="ml-1.5 border-warning/40 text-[10px] text-warning">Новый</Badge>}
                          <ForcedNameBadge forcedName={p.forced_display_name} />
                          <RankMismatchBadge nickname={p.player_name} rankId={p.rank_id} ranks={data.ranks} />
                        </TableCell>
                        <TableCell><Badge variant="secondary">{p.server_name}</Badge></TableCell>
                        <TableCell>{p.job_id ?? "-"}</TableCell>
                        <TableCell>{p.ping ?? "-"}</TableCell>
                        <TableCell className="text-sm text-muted-foreground">{fmtDate(p.join_time)}</TableCell>
                        <TableCell className="text-sm text-muted-foreground">{fmtPlayTime(p.total_play_time != null ? p.total_play_time / 3600 : null)}</TableCell>
                        {(data.can_edit || data.can_issue_punishment) && (
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1.5 whitespace-nowrap">
                              {data.can_issue_punishment && (
                                <Button size="sm" variant="outline" onClick={() => setWarnTarget({ steamid: p.player_steamid, name })}>Предупреждение</Button>
                              )}
                              {data.can_edit && <>
                                <Button size="sm" variant="outline" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "message" })}>Сообщение</Button>
                                <Button size="sm" variant="outline" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "kick" })}>Кик</Button>
                                <Button size="sm" variant="destructive" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "ban" })}>Бан</Button>
                              </>}
                            </div>
                          </TableCell>
                        )}
                      </TableRow>
                    </AppContextMenu>
                    )
                  })}
                  {!filteredOnlinePlayers.length && (
                    <TableRow><TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                      {data.online_players.length ? "Нет игроков по выбранным фильтрам" : "Никого нет онлайн"}
                    </TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="banhammer" className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
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
                    {(data.can_edit || data.can_issue_punishment) && <TableHead className="text-right">Действия</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredOfflineRows.map((p) => {
                    const name = offlineDisplayName(p)
                    return (
                    <AppContextMenu
                      key={p.player_id}
                      actions={[
                        { label: "Открыть профиль", icon: ShieldAlert, onSelect: () => setSummaryPlayerId(p.player_id) },
                        { label: "Скопировать SteamID", icon: IdCard, onSelect: () => copyToClipboard(p.player_steamid, "SteamID") },
                        { label: "Скопировать имя", icon: Copy, onSelect: () => copyToClipboard(name, "Имя") },
                        ...(data.can_issue_punishment ? [
                          { label: "Выдать предупреждение", separatorBefore: true, onSelect: () => setWarnTarget({ steamid: p.player_steamid, name }) },
                        ] : []),
                        ...(data.can_edit ? [
                          { label: "Сообщение", separatorBefore: true, onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "message" as const }) },
                          { label: "Кик", onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "kick" as const }) },
                          { label: "Бан", destructive: true, onSelect: () => setActionTarget({ steamid: p.player_steamid, name, type: "ban" as const }) },
                        ] : []),
                      ]}
                    >
                      <TableRow className={p.is_new_player ? "bg-warning/10 hover:bg-warning/15" : undefined}>
                        <TableCell>
                          <PlayerHoverLink playerId={p.player_id} className="font-medium text-primary underline-offset-2 hover:underline">{name}</PlayerHoverLink>
                          {p.is_new_player && <Badge variant="outline" className="ml-1.5 border-warning/40 text-[10px] text-warning">Новый</Badge>}
                          <ForcedNameBadge forcedName={p.forced_display_name} />
                          <RankMismatchBadge nickname={p.player_last_time_name} rankId={p.rank_id} ranks={data.ranks} />
                          <div className="font-mono text-xs text-muted-foreground">{p.player_steamid}</div>
                        </TableCell>
                        <TableCell>{fmtPlayTime(p.play_time_hours)}</TableCell>
                        <TableCell className="text-sm text-muted-foreground">{fmtDate(p.first_login)}</TableCell>
                        <TableCell className="text-sm text-muted-foreground">{fmtDate(p.last_login_time)}</TableCell>
                        {(data.can_edit || data.can_issue_punishment) && (
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-1.5 whitespace-nowrap">
                              {data.can_issue_punishment && (
                                <Button size="sm" variant="outline" onClick={() => setWarnTarget({ steamid: p.player_steamid, name })}>Предупреждение</Button>
                              )}
                              {data.can_edit && <>
                                <Button size="sm" variant="outline" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "message" })}>Сообщение</Button>
                                <Button size="sm" variant="outline" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "kick" })}>Кик</Button>
                                <Button size="sm" variant="destructive" onClick={() => setActionTarget({ steamid: p.player_steamid, name, type: "ban" })}>Бан</Button>
                              </>}
                            </div>
                          </TableCell>
                        )}
                      </TableRow>
                    </AppContextMenu>
                    )
                  })}
                  {!filteredOfflineRows.length && (
                    <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                      {offlineRows.length ? "Нет игроков по выбранным фильтрам" : "Нет результатов"}
                    </TableCell></TableRow>
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
                          <PlayerHoverLink playerId={ev.player_id}>{ev.player_name || ev.player_steamid}</PlayerHoverLink>
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

        <TabsContent value="console">
          <ConsoleTab />
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

      <Dialog open={!!warnTarget} onOpenChange={(open) => !open && setWarnTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Выдать предупреждение - {warnTarget?.name}</DialogTitle>
            <DialogDescription>
              Предупреждение попадёт в историю наказаний игрока. Если он в игре - сразу увидит окно с причиной,
              иначе окно покажется при следующем входе.
            </DialogDescription>
          </DialogHeader>
          <div>
            <Label className="mb-1.5 block">Причина</Label>
            <Textarea value={warnReason} onChange={(e) => setWarnReason(e.target.value)} placeholder="За что выдано предупреждение" />
          </div>
          <DialogFooter>
            <Button
              onClick={() => warnMutation.mutate()}
              disabled={warnMutation.isPending || !warnReason.trim()}
            >
              Выдать
            </Button>
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
