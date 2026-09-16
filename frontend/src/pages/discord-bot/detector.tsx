import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Checkbox } from "@/components/ui/checkbox"
import { Activity, CircleAlert, CircleCheck, GitBranch, Hash, Power, RadioTower, Search, Save, TerminalSquare } from "lucide-react"
import { api } from "@/lib/api"

interface DetectorChannel {
  id: string
  name: string
  position: number
  can_send: boolean
  category_id: string | null
  parent_id: string | null
  type: "text" | "thread"
}

interface DetectorCategory { id: string; name: string; position: number }
interface DetectorChannelTree { categories: DetectorCategory[]; channels: DetectorChannel[] }
interface DetectorRole { id: string; name: string; color: string | null; position: number; managed: boolean }

interface DetectorEffective {
  enabled: boolean
  monitored_channel_count: number
  signals_channel_configured: boolean
  moderator_role_configured: boolean
  emoji_configured: boolean
  analyzer_mode: string
  model_configured: boolean
  monitored_channel_ids: string[]
  excluded_channel_ids: string[]
  signals_channel_id: string
  moderator_role_id: string
}

interface DetectorResponse {
  effective: DetectorEffective | null
  runtime: {
    active: boolean
    queue_depth: number
    queue_capacity?: number
    counters?: Record<string, number>
    events?: { at: string; event: string; count: number; level?: "info" | "warning" | "error"; detail?: string }[]
  }
  config_error: string | null
}

interface DetectorTabData {
  config: Record<string, string>
  can_manage_config: boolean
  address_detector?: DetectorResponse
}

interface Props {
  data?: DetectorTabData
  onSave: (key: string, value: string) => void
}

function ConfigField({ label, configKey, value, disabled, hint, onSave }: {
  label: string
  configKey: string
  value: string
  disabled: boolean
  hint?: string
  onSave: (key: string, value: string) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <div className="flex gap-2">
        <Input className="font-mono text-xs" value={draft} disabled={disabled} onChange={(e) => setDraft(e.target.value)} />
        {!disabled && draft !== value && <Button size="sm" onClick={() => onSave(configKey, draft)}>Сохранить</Button>}
      </div>
      {hint && <p className="text-[11px] leading-relaxed text-muted-foreground">{hint}</p>}
    </div>
  )
}

function configured(data: DetectorTabData | undefined, key: string, fallback: string): string {
  return data?.config[key] ?? fallback
}

function eventTone(event: string, level?: "info" | "warning" | "error"): string {
  if (level === "error") return "text-red-300"
  if (level === "warning") return "text-amber-300"
  if (event.includes("error") || event.includes("timeout") || event.includes("overflow")) return "text-amber-300"
  if (event.includes("warning")) return "text-amber-300"
  if (event.includes("likely") || event.includes("uncertain")) return "text-red-300"
  if (event.includes("clean") || event.includes("removed") || event.includes("loaded")) return "text-emerald-300"
  return "text-zinc-300"
}

function parseIds(value: string | undefined): string[] {
  if (!value?.trim()) return []
  try {
    const parsed = JSON.parse(value)
    if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean)
  } catch { /* comma-separated advanced input is supported below */ }
  return value.split(",").map((item) => item.trim()).filter(Boolean)
}

function DiscordChannelPicker({
  tree,
  selectedIds,
  multiple,
  disabled,
  error,
  label,
  hint,
  onSave,
}: {
  tree?: DetectorChannelTree
  selectedIds: string[]
  multiple: boolean
  disabled: boolean
  error?: boolean
  label: string
  hint: string
  onSave: (value: string) => void
}) {
  const [search, setSearch] = useState("")
  const [selection, setSelection] = useState(selectedIds)
  const selectedKey = selectedIds.join(",")
  useEffect(() => setSelection(selectedIds), [selectedKey])

  const selected = new Set(selection)
  const selectionKey = selection.join(",")
  const query = search.trim().toLocaleLowerCase()
  const visible = useMemo(() => {
    if (!tree) return []
    return tree.channels.filter((channel) =>
      (channel.type === "text" || channel.type === "thread") &&
      (!query || channel.name.toLocaleLowerCase().includes(query) || channel.id.includes(query)),
    )
  }, [tree, query])
  const grouped = useMemo(() => {
    const categoryNames = new Map((tree?.categories ?? []).map((category) => [category.id, category.name]))
    const groups = new Map<string, DetectorChannel[]>()
    for (const channel of visible) {
      if (selected.has(channel.id)) continue
      const key = channel.category_id ?? "__uncategorized__"
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key)!.push(channel)
    }
    return [...groups.entries()].map(([key, channels]) => ({
      key,
      name: categoryNames.get(key) ?? "Без категории",
      channels: channels.sort((a, b) => a.position - b.position),
    }))
  }, [tree, visible, selectionKey])
  const filteredIds = visible.map((channel) => channel.id)
  const selectedChannels = selection
    .map((id) => tree?.channels.find((channel) => channel.id === id))
    .filter((channel): channel is DetectorChannel => !!channel && (channel.type === "text" || channel.type === "thread"))
  const unresolvedIds = selection.filter((id) => !selectedChannels.some((channel) => channel.id === id))
  const dirty = selection.join(",") !== selectedIds.join(",")
  const clearUnresolved = () => setSelection(selectedChannels.map((channel) => channel.id))
  const saveSelection = () => {
    if (multiple) {
      // Live picker selections must never carry stale/hidden IDs forward.
      // The advanced ID field remains available for an intentional exception.
      onSave(JSON.stringify(selectedChannels.map((channel) => channel.id)))
      return
    }
    onSave(selection[0] ?? "")
  }

  const toggle = (id: string) => {
    if (disabled) return
    if (!multiple) {
      setSelection([id])
      return
    }
    setSelection((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  }
  const selectFiltered = () => setSelection((current) => [...new Set([...current, ...filteredIds])])
  const clearFiltered = () => setSelection((current) => current.filter((id) => !filteredIds.includes(id)))

  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 p-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-medium">{label}</p>
          <p className="text-[11px] text-muted-foreground">{hint}</p>
        </div>
        {dirty && <Button size="sm" disabled={disabled} onClick={saveSelection}><Save className="size-3.5" /> Сохранить</Button>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-52 flex-1">
          <Search className="absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" />
          <Input className="h-8 pl-8 text-xs" placeholder="Поиск по названию или ID…" value={search} disabled={disabled} onChange={(event) => setSearch(event.target.value)} />
        </div>
        {multiple && <>
          <Button size="sm" variant="outline" disabled={disabled || !visible.length} onClick={selectFiltered}>Выбрать найденные</Button>
          <Button size="sm" variant="ghost" disabled={disabled || !visible.length} onClick={clearFiltered}>Снять найденные</Button>
          {unresolvedIds.length > 0 && <Button size="sm" variant="ghost" className="text-amber-300 hover:text-amber-200" disabled={disabled} onClick={clearUnresolved}>Очистить недоступные</Button>}
        </>}
      </div>
      {selection.length > 0 && (
        <div className="text-[11px] text-muted-foreground">
          <p>Выбрано: <span className="font-medium text-foreground">{selection.length}</span>{multiple ? " каналов/threads" : " объект"}</p>
          <p className="truncate text-zinc-400">
            {selection.map((id) => tree?.channels.find((channel) => channel.id === id)?.name ?? id).join(", ")}
          </p>
        </div>
      )}
      {selection.length > 0 && (
        <div className="rounded border border-primary/30 bg-primary/5 p-2">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-primary">Выбранные — сверху</p>
          <div className="space-y-1">
            {selectedChannels.map((channel) => {
              const parent = channel.parent_id ? tree?.channels.find((item) => item.id === channel.parent_id) : undefined
              return (
                <div key={channel.id} className="flex items-center gap-2 rounded bg-background/50 px-2 py-1.5 text-xs">
                  {channel.type === "thread" ? <GitBranch className="size-3.5 shrink-0 text-primary" /> : <Hash className="size-3.5 shrink-0 text-primary" />}
                  <span className="min-w-0 flex-1 truncate">{channel.name}</span>
                  {parent && <span className="max-w-36 truncate text-[10px] text-muted-foreground">в #{parent.name}</span>}
                  <span className="font-mono text-[10px] text-muted-foreground">{channel.id}</span>
                  <Button size="sm" variant="ghost" className="h-6 px-1.5 text-[10px]" disabled={disabled} onClick={() => setSelection((current) => current.filter((id) => id !== channel.id))}>Снять</Button>
                </div>
              )
            })}
            {unresolvedIds.map((id) => (
              <div key={id} className="flex items-center gap-2 rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-xs text-amber-200">
                <CircleAlert className="size-3.5 shrink-0" />
                <span className="min-w-0 flex-1"><span className="font-mono">{id}</span> — не найден в текущем Discord tree</span>
                <Button size="sm" variant="ghost" className="h-6 px-1.5 text-[10px] text-amber-200 hover:text-amber-100" disabled={disabled} onClick={() => setSelection((current) => current.filter((item) => item !== id))}>Снять</Button>
              </div>
            ))}
          </div>
          {unresolvedIds.length > 0 && <p className="mt-1 text-[10px] text-amber-300">Это может быть архивная/удалённая thread или объект, скрытый от бота. Для мониторинга оставляйте только доступные каналы.</p>}
        </div>
      )}
      <div className="max-h-64 overflow-y-auto rounded border border-border/60 bg-background/60 p-1">
        {!tree && !error && <p className="p-2 text-xs text-muted-foreground">Получаю список Discord…</p>}
        {error && <p className="p-2 text-xs text-amber-300">Список Discord недоступен. Используйте «Расширенный режим: ID».</p>}
        {tree && !visible.length && <p className="p-2 text-xs text-muted-foreground">Ничего не найдено.</p>}
        {grouped.map((group) => (
          <div key={group.key} className="mb-2 last:mb-0">
            <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{group.name}</div>
            {group.channels.map((channel) => {
              const parent = channel.parent_id ? tree?.channels.find((item) => item.id === channel.parent_id) : undefined
              return (
                <label key={channel.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-muted">
                  <Checkbox checked={selected.has(channel.id)} disabled={disabled} onCheckedChange={() => toggle(channel.id)} />
                  {channel.type === "thread" ? <GitBranch className="size-3.5 shrink-0 text-muted-foreground" /> : <Hash className="size-3.5 shrink-0 text-muted-foreground" />}
                  <span className="min-w-0 flex-1 truncate">{channel.name}</span>
                  {parent && <span className="max-w-36 truncate text-[10px] text-muted-foreground">в #{parent.name}</span>}
                  <span className="font-mono text-[10px] text-muted-foreground">{channel.id}</span>
                </label>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

function DiscordRolePicker({ roles, selectedId, disabled, error, onSave }: {
  roles?: DetectorRole[]
  selectedId: string
  disabled: boolean
  error?: boolean
  onSave: (value: string) => void
}) {
  const [search, setSearch] = useState("")
  const [selection, setSelection] = useState(selectedId)
  useEffect(() => setSelection(selectedId), [selectedId])
  const query = search.trim().toLocaleLowerCase()
  const visible = (roles ?? []).filter((role) => !query || role.name.toLocaleLowerCase().includes(query) || role.id.includes(query))
  const dirty = selection !== selectedId
  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 p-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-medium">Роль для ping</p>
          <p className="text-[11px] text-muted-foreground">Выберите роль из текущего Discord-сервера.</p>
        </div>
        {dirty && <Button size="sm" disabled={disabled} onClick={() => onSave(selection)}><Save className="size-3.5" /> Сохранить</Button>}
      </div>
      <div className="relative">
        <Search className="absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" />
        <Input className="h-8 pl-8 text-xs" placeholder="Поиск роли…" value={search} disabled={disabled} onChange={(event) => setSearch(event.target.value)} />
      </div>
      <div className="max-h-48 overflow-y-auto rounded border border-border/60 bg-background/60 p-1">
        {!roles && !error && <p className="p-2 text-xs text-muted-foreground">Получаю роли Discord…</p>}
        {error && <p className="p-2 text-xs text-amber-300">Роли Discord недоступны. Используйте «Расширенный режим: ID».</p>}
        {roles && !visible.length && <p className="p-2 text-xs text-muted-foreground">Роль не найдена.</p>}
        {visible.map((role) => (
          <button key={role.id} type="button" disabled={disabled} onClick={() => setSelection(role.id)} className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs ${selection === role.id ? "bg-primary/15 text-foreground" : "hover:bg-muted"}`}>
            <span className="size-2.5 rounded-full" style={{ background: role.color ?? "#99aab5" }} />
            <span className="min-w-0 flex-1 truncate">{role.name}</span>
            <span className="font-mono text-[10px] text-muted-foreground">{role.id}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export function AddressDetectorTab({ data, onSave }: Props) {
  const channelTreeQuery = useQuery({
    queryKey: ["discord-detector-channel-tree"],
    queryFn: () => api.get<DetectorChannelTree>("/api/v1/admin/discord-bot/viewer/tree"),
    retry: false,
  })
  const rolesQuery = useQuery({
    queryKey: ["discord-detector-roles"],
    queryFn: () => api.get<{ roles: DetectorRole[] }>("/api/v1/admin/discord-bot/viewer/roles"),
    retry: false,
  })
  const detector = data?.address_detector
  const effective = detector?.effective
  const canEdit = !!data?.can_manage_config
  const enabled = configured(data, "moderation_address_detector_enabled", effective?.enabled ? "true" : "false") === "true"
  const active = !!detector?.runtime.active
  const monitoredIds = effective?.monitored_channel_ids ?? parseIds(configured(data, "monitored_channel_ids", ""))
  const signalChannelId = effective?.signals_channel_id ?? configured(data, "moderation_signals_channel_id", "")
  const moderatorRoleId = effective?.moderator_role_id ?? configured(data, "moderator_role_id", "")
  const diagnostic = [...(detector?.runtime.events ?? [])].reverse().find((entry) =>
    entry.event === "startup_validation_error" || entry.event === "detector_disabled",
  )
  const warning = [...(detector?.runtime.events ?? [])].reverse().find((entry) =>
    entry.event === "startup_validation_warning" || entry.event === "model_degraded",
  )
  const statusReason = detector?.config_error ?? diagnostic?.detail ?? (!monitoredIds.length && enabled ? "whitelist каналов пуст" : undefined)
  const statusText = statusReason ? "Не запущен" : active && warning ? "Активен с предупреждением" : active ? "Активен" : enabled ? "Ожидает запуска" : "Выключен"
  const statusClass = statusReason ? "border-destructive/50 bg-destructive/5" : warning ? "border-amber-500/40 bg-amber-500/5" : active ? "border-emerald-500/40 bg-emerald-500/5" : "border-border"

  return (
    <div className="space-y-4">
      <Card className={statusClass}>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
          <div className="flex items-center gap-3">
            {statusReason ? <CircleAlert className="size-5 text-destructive" /> : warning ? <CircleAlert className="size-5 text-amber-300" /> : active ? <CircleCheck className="size-5 text-emerald-500" /> : <Power className="size-5 text-muted-foreground" />}
            <div>
              <p className="font-medium">Детектор обращений</p>
              <p className="text-xs text-muted-foreground">{statusText}. {statusReason ?? warning?.detail ?? "Бот только сигнализирует модераторам и ничего не применяет к участнику."}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Badge variant={active ? "default" : "secondary"}>{statusText}</Badge>
            <Switch checked={enabled} disabled={!canEdit} onCheckedChange={(checked) => onSave("moderation_address_detector_enabled", checked ? "true" : "false")} aria-label="Включить детектор обращений" />
          </div>
        </CardContent>
      </Card>

      {!canEdit && <p className="text-xs text-muted-foreground">Только просмотр — нужно право `discord_bot.manage_config` для изменения детектора.</p>}

      <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-sm"><RadioTower className="size-4" /> Область мониторинга</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <DiscordChannelPicker
              tree={channelTreeQuery.data}
              selectedIds={monitoredIds}
              multiple
              disabled={!canEdit}
              error={channelTreeQuery.isError}
              label="Разрешённые каналы и активные threads"
              hint="Мониторинг работает только по выбранным ID. Пустой выбор явно выключает detector."
              onSave={(value) => onSave("monitored_channel_ids", value)}
            />
            <details className="rounded-md border border-border/60 px-3 py-2">
              <summary className="cursor-pointer text-xs font-medium">Расширенный режим: ID и исключения</summary>
              <div className="mt-3 space-y-4">
                <ConfigField
                  label="Whitelist вручную (JSON-массив или ID через запятую)"
                  configKey="monitored_channel_ids"
                  value={configured(data, "monitored_channel_ids", "")}
                  disabled={!canEdit}
                  hint="Используйте для новых или скрытых Discord-объектов. Сохранение пустого значения превращает whitelist в []."
                  onSave={onSave}
                />
                <ConfigField
                  label="Исключённые каналы (Discord ID)"
                  configKey="excluded_channel_ids"
                  value={configured(data, "excluded_channel_ids", "")}
                  disabled={!canEdit}
                  hint="Исключение имеет приоритет над whitelist. Значения через запятую или JSON-массив."
                  onSave={onSave}
                />
                <ConfigField
                  label="Категории (совместимость)"
                  configKey="monitored_category_ids"
                  value={configured(data, "monitored_category_ids", "")}
                  disabled={!canEdit}
                  hint="Само по себе не добавляет каналы в мониторинг."
                  onSave={onSave}
                />
              </div>
            </details>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-sm">Доставка сигнала</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <DiscordChannelPicker
              tree={channelTreeQuery.data}
              selectedIds={signalChannelId ? [signalChannelId] : []}
              multiple={false}
              disabled={!canEdit}
              error={channelTreeQuery.isError}
              label="Канал / ветка сигналов"
              hint="Выберите уже существующий текстовый канал или thread, куда бот отправляет сигналы."
              onSave={(value) => onSave("moderation_signals_channel_id", value)}
            />
            <DiscordRolePicker roles={rolesQuery.data?.roles} selectedId={moderatorRoleId} disabled={!canEdit} error={rolesQuery.isError} onSave={(value) => onSave("moderator_role_id", value)} />
            <details className="rounded-md border border-border/60 px-3 py-2">
              <summary className="cursor-pointer text-xs font-medium">Расширенный режим: ручные ID</summary>
              <div className="mt-3 space-y-4">
                <ConfigField
                  label="Канал / ветка сигналов (Discord ID)"
                  configKey="moderation_signals_channel_id"
                  value={configured(data, "moderation_signals_channel_id", "")}
                  disabled={!canEdit}
                  hint="Только числовой channel/thread ID."
                  onSave={onSave}
                />
                <ConfigField
                  label="Роль модераторов (Discord ID)"
                  configKey="moderator_role_id"
                  value={configured(data, "moderator_role_id", "")}
                  disabled={!canEdit}
                  hint="Ручной режим нужен только для роли, которую viewer не видит. Вставьте числовой Role ID, не @название."
                  onSave={onSave}
                />
              </div>
            </details>
            <ConfigField label=":redadmin: emoji ID" configKey="redadmin_emoji_id" value={configured(data, "redadmin_emoji_id", "")} disabled={!canEdit} onSave={onSave} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-sm">Контекст и обработка</CardTitle></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <ConfigField label="Сообщений контекста (1–5)" configKey="address_context_messages" value={configured(data, "address_context_messages", "4")} disabled={!canEdit} onSave={onSave} />
          <ConfigField label="Окно incident, секунд" configKey="address_incident_window_seconds" value={configured(data, "address_incident_window_seconds", "120")} disabled={!canEdit} onSave={onSave} />
          <ConfigField label="Режим" configKey="address_analyzer_mode" value={configured(data, "address_analyzer_mode", "hybrid")} disabled={!canEdit} hint="rules или hybrid" onSave={onSave} />
          <ConfigField label="Timeout анализа, секунд" configKey="address_analysis_timeout_seconds" value={configured(data, "address_analysis_timeout_seconds", "15")} disabled={!canEdit} onSave={onSave} />
          <ConfigField label="Порог вероятного нарушения" configKey="address_likely_threshold" value={configured(data, "address_likely_threshold", "0.75")} disabled={!canEdit} onSave={onSave} />
          <ConfigField label="Порог чистого сообщения" configKey="address_clean_threshold" value={configured(data, "address_clean_threshold", "0.80")} disabled={!canEdit} onSave={onSave} />
          <ConfigField label="Путь к ONNX модели" configKey="onnx_model_path" value={configured(data, "onnx_model_path", "")} disabled={!canEdit} hint="Файл размещается оператором вне Git." onSave={onSave} />
          <ConfigField label="Путь к tokenizer.json" configKey="onnx_tokenizer_path" value={configured(data, "onnx_tokenizer_path", "")} disabled={!canEdit} onSave={onSave} />
        </CardContent>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card><CardContent className="py-4"><p className="font-mono text-xl">{effective?.monitored_channel_count ?? 0}</p><p className="text-xs text-muted-foreground">каналов в effective whitelist</p></CardContent></Card>
        <Card><CardContent className="py-4"><p className="font-mono text-xl">{detector?.runtime.queue_depth ?? 0}</p><p className="text-xs text-muted-foreground">задач в очереди</p></CardContent></Card>
        <Card><CardContent className="py-4"><p className="font-mono text-xl">{detector?.runtime.counters?.candidate_detected ?? 0}</p><p className="text-xs text-muted-foreground">candidate detected</p></CardContent></Card>
        <Card><CardContent className="py-4"><p className="font-mono text-xl">{effective?.analyzer_mode ?? "-"}</p><p className="text-xs text-muted-foreground">режим анализатора</p></CardContent></Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="text-sm">Проверка перед тестом</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-2 text-xs">
          <Badge variant={monitoredIds.length ? "default" : "destructive"}>{monitoredIds.length ? `whitelist: ${monitoredIds.length}` : "whitelist пуст"}</Badge>
          <Badge variant={signalChannelId ? "default" : "destructive"}>{signalChannelId ? "target channel задан" : "target channel не задан"}</Badge>
          <Badge variant={moderatorRoleId ? "default" : "destructive"}>{moderatorRoleId ? "роль задана" : "роль не задана"}</Badge>
          <Badge variant={active ? "default" : "secondary"}>{active ? "gateway worker активен" : "worker не активен"}</Badge>
        </CardContent>
      </Card>

      <Card className="overflow-hidden border-zinc-800 bg-[#111413]">
        <CardHeader className="border-b border-zinc-800 bg-[#0b0e0c] py-3">
          <CardTitle className="flex items-center gap-2 font-mono text-sm font-normal text-zinc-200">
            <TerminalSquare className="size-4 text-emerald-400" />
            Журнал detector runtime
            <span className="ml-auto flex items-center gap-1.5 text-[11px] text-zinc-500">
              <Activity className="size-3" /> обновляется вместе со статусом
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="max-h-72 min-h-28 overflow-auto px-4 py-3 font-mono text-[11px] leading-6">
            {[...(detector?.runtime.events ?? [])].reverse().map((entry, index) => (
              <div key={`${entry.at}-${entry.event}-${entry.count}-${index}`} className={eventTone(entry.event, entry.level)}>
                <span className="mr-3 text-zinc-600">{new Date(entry.at).toLocaleTimeString()}</span>
                <span className="mr-3 text-zinc-500">detector</span>
                <span>{entry.event}</span>
                <span className="ml-3 text-zinc-600">count={entry.count}</span>
                {entry.detail && <span className="ml-3 text-zinc-500">{entry.detail}</span>}
              </div>
            ))}
            {!detector?.runtime.events?.length && (
              <div className="text-zinc-600">— runtime пока не оставил событий —</div>
            )}
          </div>
          <div className="border-t border-zinc-800 px-4 py-2 font-mono text-[10px] text-zinc-600">
            {statusReason ?? (active ? "watching gateway events" : "runtime offline; проверьте enabled, whitelist и startup validation")}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
