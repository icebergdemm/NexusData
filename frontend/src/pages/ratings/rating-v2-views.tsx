import { Badge } from "@/components/ui/badge"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { formatBrowserDate } from "@/lib/format-date"

type UnknownRecord = Record<string, unknown>

const ROLE_LABELS: Record<string, string> = {
  captain: "Капитан", mechanic: "Механик", medic: "Врач", security: "СБ", engineer: "Инженер",
}

const EVENT_LABELS: Record<string, string> = {
  "combat.player_kill": "Убийство игрока", "combat.npc_kill": "Убийство NPC", "combat.monster_kill": "Убийство монстра",
  "combat.player_damage": "Урон игроку", "combat.monster_damage": "Урон монстру", "medical.revive": "Реанимация",
  "medical.heal": "Лечение", "engineering.repair": "Ремонт корпуса", "engineering.device_repair": "Ремонт устройства",
  "engineering.craft": "Создание предмета", "engineering.deconstruct": "Разбор предмета", "engineering.wire": "Работа с проводкой",
  "captain.pilot": "Управление подлодкой", "captain.navigate": "Прибытие к цели", "round.finished": "Завершение раунда",
}

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {}
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null
}

function displayNumber(value: unknown, digits = 2): string {
  const number = asNumber(value)
  return number == null ? "—" : number.toLocaleString("ru-RU", { maximumFractionDigits: digits })
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—"
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value)
  return "—"
}

function modeLabel(mode: unknown): string {
  return mode === "pvp" ? "PvP" : mode === "mission" ? "Mission" : displayValue(mode)
}

function roleLabel(role: unknown): string {
  const value = asString(role)
  return value ? ROLE_LABELS[value] ?? value : "Роль не указана"
}

function dataQualityLabel(value: unknown): string {
  const quality = asString(value)
  if (quality === "legacy") return "Legacy-данные"
  if (quality === "sufficient_data" || quality === "eligible") return "Достаточно данных"
  if (quality === "insufficient_data") return "Мало данных"
  if (quality === "no_data") return "Нет данных"
  return quality ?? "Мало данных"
}

function eventLabel(type: unknown): string {
  const value = asString(type)
  return value ? EVENT_LABELS[value] ?? value.replace(/[._]/g, " ") : "Событие без типа"
}

function eventRows(value: unknown): UnknownRecord[] {
  if (Array.isArray(value)) return value.map(asRecord).filter((event) => Object.keys(event).length > 0)
  const response = asRecord(value)
  const events = Array.isArray(response.events) ? response.events : Array.isArray(response.history) ? response.history : Array.isArray(response.items) ? response.items : []
  return events.map(asRecord).filter((event) => Object.keys(event).length > 0)
}

function metric(data: UnknownRecord, ...keys: string[]): unknown {
  for (const key of keys) if (data[key] !== undefined && data[key] !== null) return data[key]
  return null
}

function MetricGrid({ data }: { data: UnknownRecord }) {
  const rows = [
    ["Рейтинг", displayNumber(metric(data, "rating", "current_rating"))],
    ["Полезный вклад", displayNumber(metric(data, "cumulative_points", "points", "contribution"))],
    ["Активность", displayNumber(metric(data, "activity_factor", "activity"))],
    ["Часы", displayNumber(metric(data, "play_time_hours", "playtime_hours"))],
    ["Квалифицированные раунды", displayValue(metric(data, "qualified_rounds"))],
    ["Достаточность данных", dataQualityLabel(metric(data, "data_quality", "eligibility"))],
    ["Последняя активность", formatBrowserDate(asString(metric(data, "last_activity", "last_activity_date", "last_event_at")))],
  ]
  return <div className="grid gap-2 sm:grid-cols-2">{rows.map(([label, value]) => <div key={label} className="rounded-md border bg-muted/20 p-3"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 font-medium">{value}</div></div>)}</div>
}

function ContributionRows({ explanation }: { explanation: UnknownRecord }) {
  const contributions = asRecord(explanation.contributions ?? explanation.action_contributions)
  const rows = Object.entries(contributions).map(([type, value]) => {
    const item = asRecord(value)
    return { type, count: asNumber(item.count ?? item.raw_value), points: asNumber(item.points ?? item.points_delta) ?? asNumber(value) }
  })
  if (!rows.length) return <p className="text-sm text-muted-foreground">Нет расшифровки вклада по действиям.</p>
  return <div className="divide-y rounded-md border">{rows.map((row) => <div key={row.type} className="flex items-center justify-between gap-3 p-3 text-sm"><div><div className="font-medium">{eventLabel(row.type)}</div><div className="text-xs text-muted-foreground">{row.type}{row.count != null ? ` · ${displayNumber(row.count, 0)} раз` : ""}</div></div><span className={row.points != null && row.points < 0 ? "text-destructive" : "text-primary"}>{row.points == null ? "—" : `${row.points >= 0 ? "+" : ""}${displayNumber(row.points)}`}</span></div>)}</div>
}

function Explanation({ explanation }: { explanation: unknown }) {
  const data = asRecord(explanation)
  if (!Object.keys(data).length) return <p className="text-sm text-muted-foreground">Нет данных о расчёте.</p>
  const penalties = asNumber(metric(data, "penalties", "penalty_points"))
  const adjustments = asNumber(metric(data, "adjustments", "adjustment_points"))
  return <div className="space-y-4"><MetricGrid data={data} /><div className="space-y-2"><h3 className="text-sm font-semibold">Вклад по действиям</h3><ContributionRows explanation={data} /></div><div className="grid gap-2 sm:grid-cols-2">{penalties != null && <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm"><div className="text-xs text-muted-foreground">Штрафы</div><div className="font-medium text-destructive">{displayNumber(penalties)}</div></div>}{adjustments != null && <div className="rounded-md border bg-muted/20 p-3 text-sm"><div className="text-xs text-muted-foreground">Ручные корректировки</div><div className="font-medium">{displayNumber(adjustments)}</div></div>}</div><div className="text-xs text-muted-foreground">Формула: {displayValue(metric(data, "formula_version", "formula"))} · конфигурация: {displayValue(metric(data, "config_version"))}</div></div>
}

function PlayerProfile({ player }: { player: unknown }) {
  const wrapper = asRecord(player)
  const rating = asRecord(wrapper.rating)
  const data = Object.keys(rating).length
    ? { ...wrapper, ...rating, rating: rating.rating ?? rating.current_rating }
    : wrapper
  if (!Object.keys(data).length) return <p className="text-sm text-muted-foreground">Нет данных о профиле игрока.</p>
  const source = asString(metric(data, "source", "data_source"))
  return <div className="space-y-4"><MetricGrid data={data} />{source && <Badge variant="outline">Источник: {source === "legacy" ? "legacy-миграция" : source}</Badge>}</div>
}

function HistoryTable({ history }: { history: unknown }) {
  const rows = eventRows(history)
  if (!rows.length) return <p className="text-sm text-muted-foreground">История появится после первого периодического снимка.</p>
  return <div className="overflow-x-auto"><Table><TableHeader><TableRow><TableHead>Время</TableHead><TableHead>Место</TableHead><TableHead>Рейтинг</TableHead></TableRow></TableHeader><TableBody>{rows.map((entry, index) => <TableRow key={String(entry.id ?? entry.captured_at ?? index)}><TableCell>{formatBrowserDate(asString(entry.captured_at ?? entry.created_at))}</TableCell><TableCell>{displayValue(entry.rank)}</TableCell><TableCell>{displayNumber(entry.rating ?? entry.current_rating)}</TableCell></TableRow>)}</TableBody></Table></div>
}

function RatingJournal({ events, onEventSelect }: { events: unknown; onEventSelect?: (event: UnknownRecord) => void }) {
  const rows = eventRows(events)
  if (!rows.length) return <p className="text-sm text-muted-foreground">Журнал пока пуст.</p>
  return <div className="overflow-x-auto"><Table><TableHeader><TableRow><TableHead>Время</TableHead><TableHead>Событие</TableHead><TableHead>Результат</TableHead><TableHead>Изменение</TableHead></TableRow></TableHeader><TableBody>{rows.map((event, index) => { const status = asString(event.mapping_status ?? event.status); const delta = asNumber(event.points_delta ?? event.delta ?? event.rating_delta); return <TableRow key={String(event.id ?? event.event_id ?? index)} className={onEventSelect ? "cursor-pointer align-top hover:bg-muted/40" : "align-top"} onClick={() => onEventSelect?.(event)}><TableCell className="whitespace-nowrap">{formatBrowserDate(asString(event.created_at ?? event.timestamp ?? event.occurred_at))}</TableCell><TableCell><div className="font-medium">{eventLabel(event.event_type ?? event.type ?? event.kind)}</div><div className="text-xs text-muted-foreground">{asString(event.role) ? roleLabel(event.role) : "Роль не указана"}</div></TableCell><TableCell><Badge variant={status === "rejected" || status === "unmapped" ? "destructive" : "secondary"}>{status === "mapped" ? "учтено" : status === "unmapped" ? "не сопоставлено" : status ?? "получено"}</Badge></TableCell><TableCell className={delta != null && delta < 0 ? "text-destructive" : "text-primary"}>{delta == null ? "—" : `${delta >= 0 ? "+" : ""}${displayNumber(delta)}`}</TableCell></TableRow> })}</TableBody></Table></div>
}

function DeliverySummary({ delivery }: { delivery: unknown }) {
  const response = asRecord(delivery)
  const summary = asRecord(response.summary)
  const entries = eventRows(response.entries ?? response)
  const allCounts: Array<[string, unknown]> = [["Принято", metric(summary, "accepted", "accepted_count")] as [string, unknown], ["Дубли", metric(summary, "duplicate", "duplicates", "duplicate_count")] as [string, unknown], ["Отклонено", metric(summary, "rejected", "rejected_count")] as [string, unknown], ["Ошибки", metric(summary, "failed", "errors", "failed_count")] as [string, unknown]]
  const counts = allCounts.filter(([, value]) => value != null)
  if (!counts.length && !entries.length) return null
  return <section className="space-y-2"><h3 className="text-sm font-semibold">Доставка событий</h3>{counts.length ? <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{counts.map(([label, value]) => <div key={label} className="rounded-md border bg-muted/20 p-3"><div className="text-xs text-muted-foreground">{label}</div><div className="font-semibold">{displayValue(value)}</div></div>)}</div> : <p className="text-sm text-muted-foreground">Получено записей: {entries.length}. Сводка статусов недоступна.</p>}</section>
}

export function RatingV2Investigation({ player, explanation, events, history, delivery, onEventSelect }: { player: unknown; explanation: unknown; events: unknown; history?: unknown; delivery?: unknown; onEventSelect?: (event: UnknownRecord) => void }) {
  return <div className="space-y-5"><section className="space-y-2"><h2 className="text-sm font-semibold">Профиль игрока</h2><PlayerProfile player={player} /></section><section className="space-y-2"><h2 className="text-sm font-semibold">Почему такой рейтинг</h2><Explanation explanation={explanation} /></section><section className="space-y-2"><h2 className="text-sm font-semibold">История позиции</h2><HistoryTable history={history} /></section><section className="space-y-2"><div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold">Журнал рейтинга</h2><span className="text-xs text-muted-foreground">Нажмите на событие для деталей</span></div><RatingJournal events={events} onEventSelect={onEventSelect} /></section><DeliverySummary delivery={delivery} /><details className="rounded-md border"><summary className="cursor-pointer px-3 py-2 text-sm font-semibold">Технические данные</summary><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words border-t bg-muted/20 p-3 text-xs">{JSON.stringify({ player, explanation, events, history }, null, 2)}</pre></details></div>
}

export function RatingEventDrawer({ open, event, onClose }: { open: boolean; event?: UnknownRecord; onClose: () => void }) {
  const data = asRecord(event)
  const delta = asNumber(data.points_delta ?? data.delta ?? data.rating_delta)
  return <Sheet open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}><SheetContent side="right" className="flex w-[min(96vw,38rem)] flex-col gap-0 p-0 sm:max-w-none">{!event ? <div className="p-5 text-sm text-muted-foreground">Детали события недоступны.</div> : <><SheetHeader className="border-b px-5 py-4 text-left"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{modeLabel(data.mode)}</Badge><Badge variant="secondary">{roleLabel(data.role)}</Badge></div><SheetTitle className="text-base">{eventLabel(data.event_type ?? data.type ?? data.kind)}</SheetTitle><SheetDescription>{formatBrowserDate(asString(data.occurred_at ?? data.created_at ?? data.timestamp))}</SheetDescription></SheetHeader><div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5"><div className="grid grid-cols-2 gap-2"><div className="rounded-md border bg-muted/20 p-3"><div className="text-xs text-muted-foreground">Изменение рейтинга</div><div className={delta != null && delta < 0 ? "font-semibold text-destructive" : "font-semibold text-primary"}>{delta == null ? "—" : `${delta >= 0 ? "+" : ""}${displayNumber(delta)}`}</div></div><div className="rounded-md border bg-muted/20 p-3"><div className="text-xs text-muted-foreground">Статус обработки</div><div className="font-semibold">{asString(data.mapping_status ?? data.status) === "mapped" ? "учтено" : asString(data.mapping_status ?? data.status) ?? "получено"}</div></div></div><section className="space-y-2"><h3 className="text-sm font-semibold">Что произошло</h3><p className="text-sm leading-6">{eventLabel(data.event_type ?? data.type ?? data.kind)} зафиксировано для роли {roleLabel(data.role)} в режиме {modeLabel(data.mode)}. Исходное значение: {displayNumber(data.raw_value, 2)}.</p></section><details className="rounded-md border"><summary className="cursor-pointer px-3 py-2 text-sm font-semibold">Технические данные</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t bg-muted/20 p-3 text-xs">{JSON.stringify(event, null, 2)}</pre></details></div></>}</SheetContent></Sheet>
}

export function RatingEventDetails({ event }: { event: UnknownRecord }) {
  const data = asRecord(event)
  const delta = asNumber(data.points_delta ?? data.delta ?? data.rating_delta)
  return <div className="space-y-5"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{modeLabel(data.mode)}</Badge><Badge variant="secondary">{roleLabel(data.role)}</Badge></div><h2 className="text-base font-semibold">{eventLabel(data.event_type ?? data.type ?? data.kind)}</h2><div className="grid grid-cols-2 gap-2"><div className="rounded-md border bg-muted/20 p-3"><div className="text-xs text-muted-foreground">Изменение рейтинга</div><div className={delta != null && delta < 0 ? "font-semibold text-destructive" : "font-semibold text-primary"}>{delta == null ? "—" : `${delta >= 0 ? "+" : ""}${displayNumber(delta)}`}</div></div><div className="rounded-md border bg-muted/20 p-3"><div className="text-xs text-muted-foreground">Статус обработки</div><div className="font-semibold">{asString(data.mapping_status ?? data.status) === "mapped" ? "учтено" : asString(data.mapping_status ?? data.status) ?? "получено"}</div></div></div><section className="space-y-2"><h3 className="text-sm font-semibold">Что произошло</h3><p className="text-sm leading-6">{eventLabel(data.event_type ?? data.type ?? data.kind)} зафиксировано для роли {roleLabel(data.role)} в режиме {modeLabel(data.mode)}. Исходное значение: {displayNumber(data.raw_value, 2)}.</p></section><details className="rounded-md border"><summary className="cursor-pointer px-3 py-2 text-sm font-semibold">Технические данные</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t bg-muted/20 p-3 text-xs">{JSON.stringify(event, null, 2)}</pre></details></div>
}
