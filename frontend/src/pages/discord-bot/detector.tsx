import { useEffect, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Activity, CircleAlert, CircleCheck, Power, RadioTower, TerminalSquare } from "lucide-react"

interface DetectorEffective {
  enabled: boolean
  monitored_channel_count: number
  signals_channel_configured: boolean
  moderator_role_configured: boolean
  emoji_configured: boolean
  analyzer_mode: string
  model_configured: boolean
}

interface DetectorResponse {
  effective: DetectorEffective | null
  runtime: {
    active: boolean
    queue_depth: number
    queue_capacity?: number
    counters?: Record<string, number>
    events?: { at: string; event: string; count: number }[]
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

function eventTone(event: string): string {
  if (event.includes("error") || event.includes("timeout") || event.includes("overflow")) return "text-amber-300"
  if (event.includes("likely") || event.includes("uncertain")) return "text-red-300"
  if (event.includes("clean") || event.includes("removed")) return "text-emerald-300"
  return "text-zinc-300"
}

export function AddressDetectorTab({ data, onSave }: Props) {
  const detector = data?.address_detector
  const effective = detector?.effective
  const canEdit = !!data?.can_manage_config
  const enabled = configured(data, "moderation_address_detector_enabled", effective?.enabled ? "true" : "false") === "true"
  const active = !!detector?.runtime.active
  const statusText = detector?.config_error ? "Ошибка конфигурации" : active ? "Активен" : enabled ? "Ожидает запуска" : "Выключен"
  const statusClass = detector?.config_error ? "border-destructive/50 bg-destructive/5" : active ? "border-emerald-500/40 bg-emerald-500/5" : "border-border"

  return (
    <div className="space-y-4">
      <Card className={statusClass}>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
          <div className="flex items-center gap-3">
            {detector?.config_error ? <CircleAlert className="size-5 text-destructive" /> : active ? <CircleCheck className="size-5 text-emerald-500" /> : <Power className="size-5 text-muted-foreground" />}
            <div>
              <p className="font-medium">Детектор обращений</p>
              <p className="text-xs text-muted-foreground">{statusText}. Бот только сигнализирует модераторам и ничего не применяет к участнику.</p>
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
            <ConfigField
              label="Разрешённые каналы (Discord ID)"
              configKey="monitored_channel_ids"
              value={configured(data, "monitored_channel_ids", "")}
              disabled={!canEdit}
              hint="Только эти channel/thread ID проверяются. Пустой список выключает мониторинг, даже если переключатель включён."
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
              label="Категории (справочно)"
              configKey="monitored_category_ids"
              value={configured(data, "monitored_category_ids", "")}
              disabled={!canEdit}
              hint="Сохраняется для совместимости конфигурации, но само по себе не добавляет каналы в мониторинг."
              onSave={onSave}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-sm">Доставка сигнала</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <ConfigField label="Канал / ветка сигналов" configKey="moderation_signals_channel_id" value={configured(data, "moderation_signals_channel_id", "")} disabled={!canEdit} onSave={onSave} />
            <ConfigField
              label="Роль модераторов (ID для пинга)"
              configKey="moderator_role_id"
              value={configured(data, "moderator_role_id", "")}
              disabled={!canEdit}
              hint="Discord: User Settings → Advanced → Developer Mode. Затем правой кнопкой по роли → Copy Role ID. Вставьте только число, не @название."
              onSave={onSave}
            />
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
              <div key={`${entry.at}-${entry.event}-${entry.count}-${index}`} className={eventTone(entry.event)}>
                <span className="mr-3 text-zinc-600">{new Date(entry.at).toLocaleTimeString()}</span>
                <span className="mr-3 text-zinc-500">detector</span>
                <span>{entry.event}</span>
                <span className="ml-3 text-zinc-600">count={entry.count}</span>
              </div>
            ))}
            {!detector?.runtime.events?.length && (
              <div className="text-zinc-600">— runtime пока не оставил событий —</div>
            )}
          </div>
          <div className="border-t border-zinc-800 px-4 py-2 font-mono text-[10px] text-zinc-600">
            {active ? "watching gateway events" : "runtime offline; проверьте enabled, whitelist и startup validation"}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
