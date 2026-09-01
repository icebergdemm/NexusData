import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Checkbox } from "@/components/ui/checkbox"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Bot, MessageSquare, Plus, RefreshCw, Save, Send, Settings, Ticket as TicketIcon, Trash2 } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { PlatformIconField } from "@/components/layout/PlatformIcon"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { DiscordViewerTab } from "./viewer"

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

// ── Настройки тикетов (config/tickets.json) ─────────────────────────────────
// Раньше правилось только Discord-командами бота (!setuptickets,
// !setticketcategory, !setticketstatus) или прямой правкой файла на сервере —
// теперь один редактор на вкладке «Discord бот».

interface TicketTypeConfig {
  name: string
  emoji: string
  color: string
  ping_roles: string[]
  template: string
  allow_cooldown?: boolean
}
interface TicketsConfig {
  types: Record<string, TicketTypeConfig>
  category_id: string
  status_channel_id: string
  /** Роли, которым доступны /close и /ticket_add — в дополнение к ping_roles
   * каждого типа (сохранена прежняя логика бота: обладатель роли уведомления
   * ЛЮБОГО типа тоже может управлять тикетами) и администраторам сервера. */
  manager_role_ids: string[]
}
interface TicketsConfigResponse { config: TicketsConfig; can_manage_config: boolean }

const EMPTY_TICKET_TYPE: TicketTypeConfig = { name: "", emoji: "🎫", color: "2b7af0", ping_roles: [], template: "", allow_cooldown: false }

/** Список Discord ID (ролей и т.п.) как строка через запятую — тот же приём,
 * что и у CORS/Allowed IPs на странице «Администрирование»: отдельный
 * компонент-тегредактор под это в проекте не заводился. */
function IdListInput({ value, onChange, disabled, placeholder }: {
  value: string[]; onChange: (v: string[]) => void; disabled: boolean; placeholder?: string
}) {
  const [draft, setDraft] = useState(value.join(", "))
  useEffect(() => { setDraft(value.join(", ")) }, [value])
  return (
    <Input
      value={draft}
      disabled={disabled}
      placeholder={placeholder ?? "ID роли, ID роли, ..."}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => onChange(draft.split(",").map((s) => s.trim()).filter(Boolean))}
    />
  )
}

function TicketTypeEditor({ typeKey, type, onChange, onRename, onRemove, disabled }: {
  typeKey: string
  type: TicketTypeConfig
  onChange: (key: string, type: TicketTypeConfig) => void
  /** Ключ типа (идентификатор в конфиге) фиксируется ТОЛЬКО по потере фокуса,
   * а не на каждый символ — иначе поле, у которого меняется React key на
   * каждое нажатие, теряло бы фокус посреди набора. */
  onRename: (oldKey: string, newKey: string) => void
  onRemove: () => void
  disabled: boolean
}) {
  const [keyDraft, setKeyDraft] = useState(typeKey)
  useEffect(() => { setKeyDraft(typeKey) }, [typeKey])

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="h-8 max-w-[160px] font-mono text-xs"
          value={keyDraft}
          disabled={disabled}
          title="Идентификатор типа — используется в имени канала тикета"
          onChange={(e) => setKeyDraft(e.target.value)}
          onBlur={() => { if (keyDraft.trim() && keyDraft !== typeKey) onRename(typeKey, keyDraft.trim()) }}
        />
        <PlatformIconField
          className="h-8 max-w-[80px]"
          v2ClassName="h-8 max-w-[170px]"
          value={type.emoji}
          disabled={disabled}
          onChange={(emoji) => onChange(typeKey, { ...type, emoji })}
          placeholder="🎫"
        />
        <Input
          className="h-8 min-w-[200px] flex-1"
          value={type.name}
          disabled={disabled}
          onChange={(e) => onChange(typeKey, { ...type, name: e.target.value })}
          placeholder="Название типа обращения"
        />
        <Input
          className="h-8 max-w-[110px] font-mono"
          value={type.color}
          disabled={disabled}
          onChange={(e) => onChange(typeKey, { ...type, color: e.target.value.replace(/^#/, "") })}
          placeholder="цвет hex"
        />
        {!disabled && (
          <Button size="icon" variant="ghost" className="size-8 shrink-0" onClick={onRemove}>
            <Trash2 className="size-4" />
          </Button>
        )}
      </div>
      <div>
        <Label className="mb-1 block text-xs text-muted-foreground">Роли уведомления (пингуются при создании тикета этого типа)</Label>
        <IdListInput
          value={type.ping_roles}
          disabled={disabled}
          onChange={(v) => onChange(typeKey, { ...type, ping_roles: v })}
        />
      </div>
      <div>
        <Label className="mb-1 block text-xs text-muted-foreground">Шаблон (подсказка для заполнения при создании)</Label>
        <Textarea
          className="min-h-[80px] text-sm"
          value={type.template}
          disabled={disabled}
          onChange={(e) => onChange(typeKey, { ...type, template: e.target.value })}
        />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox checked={!!type.allow_cooldown} disabled={disabled} onCheckedChange={(c)=>onChange(typeKey,{...type, allow_cooldown: c===true})} />
        Разрешить кулдаун для этого типа (устанавливается через /close)
      </label>
    </div>
  )
}

function slugifyTypeKey(name: string): string {
  const base = name.trim().toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "_").replace(/^_+|_+$/g, "")
  return base || `type_${Date.now()}`
}

function TicketsSettingsCard() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<TicketsConfig | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["discord-bot-tickets-config"],
    queryFn: () => api.get<TicketsConfigResponse>("/api/v1/admin/discord-bot/tickets-config"),
  })

  useEffect(() => {
    if (data?.config) setDraft(data.config)
  }, [data?.config])

  const save = useMutation({
    mutationFn: (config: TicketsConfig) => api.put<TicketsConfigResponse>("/api/v1/admin/discord-bot/tickets-config", { config }),
    onSuccess: (res) => {
      toast.success("Настройки тикетов сохранены")
      queryClient.setQueryData(["discord-bot-tickets-config"], res)
      setDraft(res.config)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const dirty = useMemo(
    () => !!draft && !!data && JSON.stringify(draft) !== JSON.stringify(data.config),
    [draft, data],
  )
  const canEdit = !!data?.can_manage_config

  if (isLoading || !draft) {
    return <Card><CardHeader><CardTitle className="text-sm">Настройки тикетов</CardTitle></CardHeader><CardContent><p className="text-sm text-muted-foreground">Загрузка…</p></CardContent></Card>
  }

  const setType = (key: string, type: TicketTypeConfig) => setDraft({ ...draft, types: { ...draft.types, [key]: type } })
  const removeType = (key: string) => {
    const types = { ...draft.types }
    delete types[key]
    setDraft({ ...draft, types })
  }
  const addType = () => {
    let key = "new_type"
    let i = 2
    while (draft.types[key]) key = `new_type_${i++}`
    setDraft({ ...draft, types: { ...draft.types, [key]: { ...EMPTY_TICKET_TYPE } } })
  }
  const renameType = (oldKey: string, rawNewKey: string) => {
    const newKey = slugifyTypeKey(rawNewKey)
    if (newKey === oldKey) return
    if (draft.types[newKey]) {
      toast.error(`Тип с ключом «${newKey}» уже существует`)
      return
    }
    const types: Record<string, TicketTypeConfig> = {}
    for (const [k, v] of Object.entries(draft.types)) types[k === oldKey ? newKey : k] = v
    setDraft({ ...draft, types })
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle className="flex items-center gap-1.5 text-sm"><TicketIcon className="size-4" /> Настройки тикетов</CardTitle>
        {canEdit && (
          <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate(draft)}>
            <Save className="size-3.5" /> Сохранить
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {!canEdit && <p className="text-xs text-muted-foreground">Только просмотр — нет права на изменение конфигурации бота.</p>}

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label className="mb-1 block text-xs text-muted-foreground">Категория для тикет-каналов</Label>
            <Input
              value={draft.category_id}
              disabled={!canEdit}
              placeholder="Discord ID категории"
              onChange={(e) => setDraft({ ...draft, category_id: e.target.value })}
            />
          </div>
          <div>
            <Label className="mb-1 block text-xs text-muted-foreground">Канал статуса обращений</Label>
            <Input
              value={draft.status_channel_id}
              disabled={!canEdit}
              placeholder="Discord ID канала"
              onChange={(e) => setDraft({ ...draft, status_channel_id: e.target.value })}
            />
          </div>
          <div className="sm:col-span-2">
            <Label className="mb-1 block text-xs text-muted-foreground">
              Роли персонала (доступ к /close и /ticket_add — независимо от типа обращения)
            </Label>
            <IdListInput
              value={draft.manager_role_ids}
              disabled={!canEdit}
              onChange={(v) => setDraft({ ...draft, manager_role_ids: v })}
            />
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-xs text-muted-foreground">Типы обращений</Label>
            {canEdit && (
              <Button size="sm" variant="outline" onClick={addType}>
                <Plus className="size-3.5" /> Добавить тип
              </Button>
            )}
          </div>
          {Object.entries(draft.types).map(([key, type]) => (
            <TicketTypeEditor
              key={key}
              typeKey={key}
              type={type}
              disabled={!canEdit}
              onChange={setType}
              onRename={renameType}
              onRemove={() => removeType(key)}
            />
          ))}
          {!Object.keys(draft.types).length && <p className="text-sm text-muted-foreground">Типов обращений нет.</p>}
        </div>
      </CardContent>
    </Card>
  )
}

export function DiscordBotPage() {
  const [newKey, setNewKey] = useState({ key: "", value: "" })
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ["discord-bot"], queryFn: () => api.get<DiscordBotResponse>("/api/v1/admin/discord-bot") })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["discord-bot"] })

  const setConfig = useMutation({
    mutationFn: (body: { key: string; value: string }) => api.post("/api/v1/admin/discord-bot/config", body),
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
        </TabsList>
        <TabsContent value="viewer">
          <DiscordViewerTab />
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

      <TicketsSettingsCard />

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
