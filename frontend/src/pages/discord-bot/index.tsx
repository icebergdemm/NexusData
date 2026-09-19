import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Checkbox } from "@/components/ui/checkbox"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Bot, MessageSquare, RefreshCw, Send, Settings } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { DiscordViewerTab } from "./viewer"
import { AddressDetectorTab } from "./detector"

const KNOWN_KEYS: { key: string; label: string }[] = [
  { key: "forward_channel_id", label: "Пересылка сообщений" },
  { key: "rating_channel_id", label: "Публикация рейтинга" },
  { key: "dispatch_channel_asper1_id", label: "Диспетчерская ASPER1" },
  { key: "dispatch_channel_asper2_id", label: "Диспетчерская ASPER2" },
  { key: "round_status_channel_asper1_id", label: "Статус раунда ASPER1" },
  { key: "round_status_channel_asper2_id", label: "Статус раунда ASPER2" },
  { key: "trap_channel_id", label: "Уведомления о ловушках" },
  { key: "punishment_channel_id", label: "Все наказания" },
  { key: "mute_manager_role_ids", label: "Роли для /mute и /unmute (ID через запятую)" },
  { key: "ban_channel_id", label: "Блокировки игроков (баны, предупреждения, выговоры)" },
  { key: "sub_projects_channel_id", label: "Публикация Sub-карт" },
]

interface AntiRaidEvent { event_type: string; discord_id: string | null; username: string | null; occurred_at: string | null; resolved: boolean }
interface TwinkSuspect { new_discord_id: string; new_username: string | null; new_display_name: string | null; similar_player_name: string | null }
interface FailedRoleSync {
  id: number; discord_id: string | null; add_role_id: string | null; remove_role_id: string | null
  error_message: string | null; executed_at: string | null
}
interface DiscordBotResponse {
  members_count: number; bot_online: boolean; last_sync: string | null
  config: Record<string, string>; antiraid_events: AntiRaidEvent[]; twink_suspects: TwinkSuspect[]
  failed_role_syncs: FailedRoleSync[]
  can_edit: boolean
  can_manage_config: boolean
  address_detector?: {
    known_keys: string[]
    effective: {
      enabled: boolean
      monitored_channel_count: number
      monitored_channel_ids: string[]
      excluded_channel_ids: string[]
      signals_channel_id: string
      moderator_role_id: string
      signals_channel_configured: boolean
      moderator_role_configured: boolean
      emoji_configured: boolean
      analyzer_mode: string
      model_configured: boolean
    } | null
    config_error: string | null
    runtime: {
      active: boolean
      queue_depth: number
      queue_capacity?: number
      counters?: Record<string, number>
      events?: { at: string; event: string; count: number; detail?: string }[]
    }
  }
}

interface RoleSyncRow {
  admin_id: number; admin_name: string | null
  department_id: number; department_name: string
  add_role_ids: string[]; remove_role_ids: string[]
}

function rowKey(r: RoleSyncRow): string { return `${r.admin_id}:${r.department_id}` }

function RoleSyncCard() {
  const [rows, setRows] = useState<RoleSyncRow[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const previewMutation = useMutation({
    mutationFn: () => api.get<{ rows: RoleSyncRow[] }>("/api/v1/admin/discord-bot/role-sync/preview"),
    onSuccess: (res) => { setRows(res.rows); setSelected(new Set(res.rows.map(rowKey))) },
    onError: (e: Error) => toast.error(e.message),
  })

  const applyMutation = useMutation({
    mutationFn: () => api.post<{ status: string; queued: number }>("/api/v1/admin/discord-bot/role-sync/apply", {
      selections: (rows ?? []).filter((r) => selected.has(rowKey(r))).map((r) => ({
        admin_id: r.admin_id, add_role_ids: r.add_role_ids, remove_role_ids: r.remove_role_ids,
      })),
    }),
    onSuccess: (res) => {
      toast.success(`Поставлено в очередь: ${res.queued}`)
      setRows(null)
      setSelected(new Set())
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const toggle = (key: string) => {
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">Ручная синхронизация ролей отделов (сайт → Discord)</CardTitle>
        <Button size="sm" variant="outline" disabled={previewMutation.isPending} onClick={() => previewMutation.mutate()}>
          <RefreshCw className="size-3.5" /> Найти расхождения
        </Button>
      </CardHeader>
      <CardContent>
        <p className="mb-2 text-xs text-muted-foreground">
          Сравнивает то, что положено сотрудникам по сайту, с их фактическими ролями в Discord (по последнему снимку) — сайт всегда источник истины.
          Отметьте, что синхронизировать, и нажмите «Синхронизировать выбранные».
        </p>
        {rows === null ? (
          <p className="text-sm text-muted-foreground">Нажмите «Найти расхождения», чтобы посмотреть текущее состояние.</p>
        ) : !rows.length ? (
          <p className="text-sm text-muted-foreground">Расхождений не найдено — всё синхронизировано.</p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    <Checkbox
                      checked={selected.size === rows.length}
                      onCheckedChange={(c) => setSelected(c ? new Set(rows.map(rowKey)) : new Set())}
                    />
                  </TableHead>
                  <TableHead>Сотрудник</TableHead>
                  <TableHead>Отдел</TableHead>
                  <TableHead>Изменение</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={rowKey(r)}>
                    <TableCell><Checkbox checked={selected.has(rowKey(r))} onCheckedChange={() => toggle(rowKey(r))} /></TableCell>
                    <TableCell>{r.admin_name ?? `#${r.admin_id}`}</TableCell>
                    <TableCell>{r.department_name}</TableCell>
                    <TableCell className="text-xs">
                      {r.add_role_ids.map((id) => <span key={id} className="mr-1 text-emerald-500">+{id}</span>)}
                      {r.remove_role_ids.map((id) => <span key={id} className="mr-1 text-destructive">-{id}</span>)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div className="mt-3 flex justify-end">
              <Button size="sm" disabled={!selected.size || applyMutation.isPending} onClick={() => applyMutation.mutate()}>
                <Send className="size-3.5" /> Синхронизировать выбранные ({selected.size})
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function DiscordBotPage() {
  const [newKey, setNewKey] = useState({ key: "", value: "" })
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ["discord-bot"],
    queryFn: () => api.get<DiscordBotResponse>("/api/v1/admin/discord-bot"),
    refetchInterval: 5000,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["discord-bot"] })

  const setConfig = useMutation({
    mutationFn: (body: { key: string; value: string; source?: "picker" | "advanced" }) => api.post("/api/v1/admin/discord-bot/config", body),
    onSuccess: () => { toast.success("Сохранено"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div>
      <PageHeader title="Discord бот" description="Синхронизация участников, каналы уведомлений, анти-рейд" icon={Bot} />
      <Tabs defaultValue="settings">
        <TabsList className="mb-4">
          <TabsTrigger value="settings"><Settings className="size-3.5" /> Настройки</TabsTrigger>
          <TabsTrigger value="viewer"><MessageSquare className="size-3.5" /> Чат и участники</TabsTrigger>
          <TabsTrigger value="address-detector"><Bot className="size-3.5" /> Детектор обращений</TabsTrigger>
        </TabsList>
        <TabsContent value="viewer">
          <DiscordViewerTab />
        </TabsContent>
        <TabsContent value="address-detector">
          <AddressDetectorTab data={data} onSave={(key, value, source) => setConfig.mutate({ key, value, source })} />
        </TabsContent>
        <TabsContent value="settings">
      <div className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <Card><CardContent className="text-center"><div className="text-2xl font-bold">{data?.members_count ?? "-"}</div><div className="text-xs text-muted-foreground">Участников Discord в базе данных (не актуальное кол-во, а общее)</div></CardContent></Card>
        <Card><CardContent className="text-center"><div className="text-2xl font-bold">{data?.bot_online ? "Онлайн" : "Оффлайн"}</div><div className="text-xs text-muted-foreground">Подключение бота</div></CardContent></Card>
        <Card><CardContent className="text-center"><div className="text-sm font-bold">{data?.last_sync ? formatBrowserDate(data.last_sync) : "-"}</div><div className="text-xs text-muted-foreground">Последняя синхронизация</div></CardContent></Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-sm">Каналы</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {KNOWN_KEYS.map(({ key, label }) => (
            <ConfigRow key={key} label={label} configKey={key} value={data?.config[key] ?? ""} canEdit={!!data?.can_edit} onSave={(v) => setConfig.mutate({ key, value: v })} />
          ))}
          {data?.can_edit && (
            <div className="flex items-end gap-2 border-t pt-3">
              <div className="flex-1"><Label className="mb-1.5 block">Ключ</Label><Input value={newKey.key} onChange={(e) => setNewKey((f) => ({ ...f, key: e.target.value }))} /></div>
              <div className="flex-1"><Label className="mb-1.5 block">Значение</Label><Input value={newKey.value} onChange={(e) => setNewKey((f) => ({ ...f, value: e.target.value }))} /></div>
              <Button disabled={!newKey.key.trim()} onClick={() => { setConfig.mutate(newKey); setNewKey({ key: "", value: "" }) }}>Добавить</Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">Анти-рейд лог</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Тип</TableHead><TableHead>Пользователь</TableHead><TableHead>Время</TableHead><TableHead>Статус</TableHead></TableRow></TableHeader>
            <TableBody>
              {data?.antiraid_events.map((e, i) => (
                <TableRow key={i}>
                  <TableCell>{e.event_type}</TableCell>
                  <TableCell>{e.username ?? e.discord_id ?? "-"}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{e.occurred_at ? formatBrowserDate(e.occurred_at) : "-"}</TableCell>
                  <TableCell><Badge variant={e.resolved ? "secondary" : "destructive"}>{e.resolved ? "Решено" : "Активно"}</Badge></TableCell>
                </TableRow>
              ))}
              {!data?.antiraid_events.length && <TableRow><TableCell colSpan={4} className="text-center text-muted-foreground">Событий нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {data?.can_manage_config && <RoleSyncCard />}

      {!!data?.failed_role_syncs.length && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader><CardTitle className="text-sm text-destructive">Ошибки синхронизации ролей отделов (сайт → Discord)</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-2 text-xs text-muted-foreground">
              Роль не удалось выдать/снять в Discord — обычно значит, что роль бота стоит ниже целевой роли в иерархии сервера,
              либо у бота нет права «Управление ролями». Сайт продолжит переотправлять эти изменения, пока проблема не будет устранена в Discord.
            </p>
            <Table>
              <TableHeader><TableRow><TableHead>Discord ID</TableHead><TableHead>Изменение</TableHead><TableHead>Ошибка</TableHead><TableHead>Время</TableHead></TableRow></TableHeader>
              <TableBody>
                {data.failed_role_syncs.map((f) => (
                  <TableRow key={f.id}>
                    <TableCell className="font-mono text-xs">{f.discord_id ?? "-"}</TableCell>
                    <TableCell className="text-xs">
                      {f.add_role_id && <span className="text-emerald-500">+{f.add_role_id}</span>}
                      {f.add_role_id && f.remove_role_id && " / "}
                      {f.remove_role_id && <span className="text-destructive">-{f.remove_role_id}</span>}
                    </TableCell>
                    <TableCell className="max-w-xs truncate text-xs text-muted-foreground" title={f.error_message ?? undefined}>{f.error_message ?? "-"}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{f.executed_at ? formatBrowserDate(f.executed_at) : "-"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle className="text-sm">Подозрения на твинков (по Discord)</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Новый участник</TableHead><TableHead>Похож на</TableHead></TableRow></TableHeader>
            <TableBody>
              {data?.twink_suspects.map((t, i) => (
                <TableRow key={i}>
                  <TableCell>{t.new_display_name ?? t.new_username}</TableCell>
                  <TableCell>{t.similar_player_name}</TableCell>
                </TableRow>
              ))}
              {!data?.twink_suspects.length && <TableRow><TableCell colSpan={2} className="text-center text-muted-foreground">Подозрений нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function ConfigRow({ label, value, canEdit, onSave }: { label: string; configKey: string; value: string; canEdit: boolean; onSave: (v: string) => void }) {
  const [draft, setDraft] = useState(value)
  return (
    <div className="flex items-center gap-2">
      <Label className="w-56 shrink-0 text-xs text-muted-foreground">{label}</Label>
      <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Channel ID" disabled={!canEdit} />
      {canEdit && draft !== value && <Button size="sm" onClick={() => onSave(draft)}>Сохранить</Button>}
    </div>
  )
}
