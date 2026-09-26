import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Bar, Line } from "react-chartjs-2"
import {
  Activity, Anchor, Ban, Bot, BrainCircuit, Check, ChevronRight,
  Copy, Crosshair, Eye, EyeOff, Files, Filter, FlaskConical, Gauge, Info, Layers, LocateFixed,
  MapPin, Pencil, Plus, Radar, Save, Search, ShipWheel, Skull, SlidersHorizontal, Sparkles,
  Target, Trash2, TrendingDown, TrendingUp, Wand2, X, Zap,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { PageHeader } from "@/components/layout/PageHeader"
import { canDo, useCurrentAdmin } from "@/lib/auth"
import { baseChartOptions, barDatasetDefaults, CHART_COLORS, lineDatasetDefaults } from "@/lib/chart-theme"
import { formatBrowserDate } from "@/lib/format-date"
import { worldStateApi } from "@/pages/world-overview/api"
import { WorldCanvas, type WorldMarker } from "@/pages/world-overview/WorldCanvas"
import {
  monsterSpawnApi,
  telemetryList,
  type ActiveWaveTelemetry,
  type CreatureInfo,
  type HeldVanillaTelemetry,
  type LearnedWaveTelemetry,
  type LLMRunRecord,
  type LLMRuntime,
  type MonsterSpawnAnalysis,
  type MonsterSpawnShipLog,
  type MonsterSpawnState,
  type MonsterSpawnWaveLog,
  type PendingWaveTelemetry,
  type PlannedActionTelemetry,
  type RecentWaveTelemetry,
  type SpawnPlan,
  type SpawnScheme,
  type SpawnWave,
  type SpeciesBaselineTelemetry,
  type SpeciesThreatTelemetry,
  type WaveOverride,
  type RoundReport,
} from "./api"

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const number = (value: string) => Number.isFinite(Number(value)) ? Number(value) : 0
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)

const emptyWave = (): SpawnWave => ({
  name: "Новая волна", min_progress: 0, max_progress: 0, min_round_time: 0, min_players: 0,
  max_sub_health: 0, max_crew_health: 0, chance: 1, commonness: 1, spawn_type: "around",
  difficulty: -1, delay_between_spawns: 0.1, scatter: 500, offset: 0, spawn_distance: 0,
  spawn_point_tag: "", reset_time: 0, play_dead_probability: -1, non_blocking: null,
  danger_override: null, chain_wave: null, chain_delay_seconds: 0, chain_same_position: true, chain_chance: 1,
  monsters: [{ species: "crawler", count: 1, amount_min: 0, amount_max: 0, max_amount_per_level: 0,
    scale_with_players: 0, hp_multiplier: 1, hp_scale_with_progress: 0, mobility: 1, threat_override: null }],
})

function Metric({ icon: Icon, label, value, detail, tone = "primary" }: { icon: LucideIcon; label: string; value: string; detail: string; tone?: string }) {
  const toneClasses: Record<string, { bar: string; icon: string }> = { primary: { bar: "bg-primary", icon: "bg-primary/15 text-primary" }, warning: { bar: "bg-warning", icon: "bg-warning/15 text-warning" }, success: { bar: "bg-success", icon: "bg-success/15 text-success" }, info: { bar: "bg-info", icon: "bg-info/15 text-info" } }
  const colors = toneClasses[tone] ?? toneClasses.primary
  return <Card className="relative overflow-hidden py-0">
    <div className={`absolute inset-y-0 left-0 w-1 ${colors.bar}`} />
    <CardContent className="flex min-h-28 items-center gap-3 p-4 pl-5">
      <div className={`flex size-10 shrink-0 items-center justify-center rounded-xl ${colors.icon}`}><Icon className="size-5" /></div>
      <div className="min-w-0"><div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</div><div className="mt-1 truncate text-2xl font-semibold tracking-tight">{value}</div><div className="mt-1 truncate text-xs text-muted-foreground">{detail}</div></div>
    </CardContent>
  </Card>
}

function RoundReportItem({ report, defaultOpen, retrying, onRetry, onDownload }: {
  report: RoundReport
  defaultOpen?: boolean
  retrying: boolean
  onRetry: (id: number) => void
  onDownload: (report: RoundReport) => void
}) {
  return <details open={defaultOpen} className="rounded-xl border bg-muted/10 p-3 text-xs">
    <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2">
      <span className="font-medium">{report.submarine_name ?? "Корабль не указан"}</span>
      {report.controller_mode === "learning" && <Badge variant="outline">Обучение · ванильный спавн</Badge>}
      <Badge variant="outline">{report.round_start_utc ? formatBrowserDate(report.round_start_utc) : "без даты"}</Badge>
      <span className="text-muted-foreground">{Math.round(Number(report.round_duration_seconds ?? 0) / 60)} мин · волн: {Array.isArray(report.waves) ? report.waves.length : 0} · событий: {Array.isArray(report.events) ? report.events.length : 0}</span>
      <Badge variant={report.analysis_status === "failed" ? "destructive" : "secondary"}>{report.analysis_status === "succeeded" ? "LLM: готово" : report.analysis_status === "running" ? "LLM: анализирует" : report.analysis_status === "failed" ? "LLM: ошибка" : "LLM: ожидает"}</Badge>
      <Button className="ml-auto" size="sm" variant="outline" onClick={(event) => { event.preventDefault(); onDownload(report) }}>JSON</Button>
    </summary>
    <div className="mt-3 space-y-3 border-t pt-3">
      {report.analysis_text && <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 leading-relaxed">{report.analysis_text}</div>}
      {report.analysis_error && <div className="flex flex-wrap items-center gap-2 text-destructive">{report.analysis_error}{report.id && <Button size="sm" variant="outline" disabled={retrying} onClick={() => onRetry(report.id!)}>Повторить анализ</Button>}</div>}
      <div className="grid gap-2 sm:grid-cols-4">{Object.entries(report.summary ?? {}).map(([key, value]) => <div key={key} className="rounded-lg bg-background/70 p-2"><div className="text-[10px] text-muted-foreground">{key}</div><div className="font-mono">{String(value)}</div></div>)}</div>
      <div className="space-y-2">{(report.waves ?? []).map((wave, waveIndex) => <div key={waveIndex} className="rounded-lg border bg-background p-3"><div className="font-medium">{String(wave.wave_name ?? wave.name ?? `Волна ${waveIndex + 1}`)}</div><div className="mt-1 grid gap-x-4 gap-y-1 text-muted-foreground sm:grid-cols-3">{Object.entries(wave).filter(([key]) => key !== "wave_name" && key !== "name").map(([key, value]) => <div key={key}><span className="font-medium text-foreground">{key}:</span> {typeof value === "object" ? JSON.stringify(value) : String(value)}</div>)}</div></div>)}</div>
      <details><summary className="cursor-pointer text-muted-foreground">Хронология событий · {(report.events ?? []).length}</summary><div className="mt-2 max-h-72 overflow-auto rounded-lg bg-background p-2 font-mono text-[10px]">{(report.events ?? []).map((event, eventIndex) => <div key={eventIndex} className="border-b border-border/50 py-1 last:border-0"><span className="text-muted-foreground">{Number(event.time_seconds ?? 0).toFixed(1)}s</span> <b>{event.direction}</b> {event.kind} <span className="break-all text-muted-foreground">{event.payload}</span></div>)}</div></details>
    </div>
  </details>
}

function RoundReports({ reports, server }: { reports: RoundReport[]; server: string }) {
  const queryClient = useQueryClient()
  const [allReportsOpen, setAllReportsOpen] = useState(false)
  const retry = useMutation({
    mutationFn: (id: number) => monsterSpawnApi.retryRoundAnalysis(server, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["monster-spawn-reports", server] }),
    onError: (error: Error) => toast.error(error.message),
  })
  const download = (report: RoundReport) => {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = `monster-round-${String(report.round_start_utc ?? "report").replace(/[:.]/g, "-")}.json`
    link.click()
    URL.revokeObjectURL(url)
  }
  const latest = reports[reports.length - 1]
  return <>
    <Card className="overflow-hidden py-0">
      <CardHeader className="border-b bg-muted/20 px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm"><Files className="size-4 text-info" /> Последний отчёт · {server}</CardTitle>
            <CardDescription className="text-xs">Отчёт последнего завершённого раунда выбранного сервера.</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => setAllReportsOpen(true)} disabled={!reports.length}>Все отчёты · {reports.length}</Button>
        </div>
      </CardHeader>
      <CardContent className="p-4">
        {latest ? <RoundReportItem report={latest} defaultOpen retrying={retry.isPending} onRetry={(id) => retry.mutate(id)} onDownload={download} /> : <div className="rounded-xl border border-dashed p-5 text-center text-xs text-muted-foreground">Для {server} отчётов пока нет. Они появятся после завершения и отправки раунда.</div>}
      </CardContent>
    </Card>
    <Dialog open={allReportsOpen} onOpenChange={setAllReportsOpen}>
      <DialogContent className="max-h-[90vh] max-w-5xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Все отчёты раундов · {server}</DialogTitle>
          <DialogDescription>{reports.length} сохранённых отчётов. Список обновляется автоматически.</DialogDescription>
        </DialogHeader>
        <div className="max-h-[70vh] space-y-2 overflow-y-auto pr-1">
          {reports.slice().reverse().map((report, index) => <RoundReportItem key={String(report.id ?? report.round_start_utc ?? index)} report={report} defaultOpen={index === 0} retrying={retry.isPending} onRetry={(id) => retry.mutate(id)} onDownload={download} />)}
        </div>
      </DialogContent>
    </Dialog>
  </>
}

function ChartCard({ title, description, children, className = "" }: { title: string; description?: string; children: React.ReactNode; className?: string }) {
  return <Card className={`overflow-hidden py-0 ${className}`}>
    <CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">{title}</CardTitle>{description && <CardDescription className="text-xs">{description}</CardDescription>}</CardHeader>
    <CardContent className="h-72 p-4"><div className="h-full">{children}</div></CardContent>
  </Card>
}

/** 5: понятные состояния карты раунда (ошибки, нет раунда, загрузка). */
function MapPlaceholder({ children, height = "380px" }: { children: React.ReactNode; height?: string }) {
  return <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground" style={{ minHeight: height }}>{children}</div>
}

function SliderField({ label, value, min, max, step, suffix = "", onChange }: { label: string; value: number; min: number; max: number; step: number; suffix?: string; onChange: (value: number) => void }) {
  const safe = Math.max(min, Math.min(max, Number.isFinite(value) ? value : min))
  const percent = ((safe - min) / (max - min)) * 100
  return <div className="space-y-2 rounded-xl border bg-muted/10 p-3">
    <div className="flex items-center justify-between gap-3"><Label className="text-xs font-medium">{label}</Label><span className="rounded-md bg-background px-2 py-1 font-mono text-xs text-primary">{safe.toFixed(step < 1 ? 2 : 0)}{suffix}</span></div>
    <input aria-label={label} type="range" min={min} max={max} step={step} value={safe} onChange={(event) => onChange(number(event.target.value))} className="h-2 w-full cursor-pointer accent-primary" style={{ background: `linear-gradient(to right, hsl(var(--primary)) ${percent}%, hsl(var(--muted)) ${percent}%)` }} />
    <div className="flex justify-between text-[10px] text-muted-foreground"><span>{min}{suffix}</span><span>{max}{suffix}</span></div>
  </div>
}

function NumberField({ label, value, onChange, step = "any" }: { label: string; value: number; onChange: (value: number) => void; step?: string }) {
  return <Label className="space-y-1 text-xs"><span className="text-muted-foreground">{label}</span><Input type="number" step={step} value={value} onChange={(event) => onChange(number(event.target.value))} /></Label>
}

/** Редактор постоянной пометки типа волны: сложность (абсолют/корректировка),
 *  неблокирующая, вкл/выкл. Хранится в memory.wave_overrides и применяется
 *  плагином к конфиг-волнам, изученным и пойманным ванильным волнам. */
function OverrideEditor({ override, onChange, waveName }: { override: WaveOverride; onChange: (override: WaveOverride) => void; waveName: string }) {
  const set = <K extends keyof WaveOverride>(key: K, value: WaveOverride[K]) => onChange({ ...override, [key]: value })
  const absolute = override.difficulty_mode === "absolute"
  return <div className="space-y-2 rounded-xl border border-warning/30 bg-warning/5 p-3">
    <div className="flex flex-wrap items-center gap-2 text-xs"><Wand2 className="size-3.5 text-warning" /><span className="font-medium">Постоянная пометка волны</span><Badge variant="outline" className="font-mono text-[10px]">{waveName}</Badge><span className="text-muted-foreground">применяется к текущим и будущим волнам этого типа</span></div>
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Сложность: режим</span>
        <Select value={override.difficulty_mode} onValueChange={(value) => set("difficulty_mode", value)}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="adjust">Корректировка (×)</SelectItem>
            <SelectItem value="absolute">Абсолютная</SelectItem>
          </SelectContent>
        </Select></Label>
      <NumberField label={absolute ? "Целевая сложность" : "Множитель сложности"} value={override.difficulty_value} onChange={(value) => set("difficulty_value", value)} step="0.05" />
      <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Блокирует другие волны</span>
        <Select value={override.non_blocking == null ? "auto" : override.non_blocking ? "no" : "yes"} onValueChange={(value) => set("non_blocking", value === "auto" ? null : value === "no")}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">Авто (по названию)</SelectItem>
            <SelectItem value="yes">Да, блокирует</SelectItem>
            <SelectItem value="no">Нет, неблокирующая</SelectItem>
          </SelectContent>
        </Select></Label>
      <div className="flex items-end"><div className="flex w-full items-center justify-between gap-3 rounded-xl border bg-muted/10 p-2.5 text-xs"><span>Волна включена</span><Switch checked={override.enabled !== false} onCheckedChange={(value) => set("enabled", value)} /></div></div>
    </div>
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Опасность: режим</span>
        <Select value={override.danger_mode ?? "adjust"} onValueChange={(value) => set("danger_mode", value)}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="adjust">Корректировка (×)</SelectItem>
            <SelectItem value="absolute">Абсолютная</SelectItem>
          </SelectContent>
        </Select></Label>
      <NumberField label="Опасность (множитель веса, пусто = авто)" value={override.danger_value ?? NaN} onChange={(value) => set("danger_value", Number.isFinite(value) ? value : null)} step="0.05" />
    </div>
    <div className="text-[11px] text-muted-foreground">Корректировка — множитель к текущей сложности волны (HP монстров масштабируется). Абсолютная — целевое значение сложности волны. Опасность — только частота выбора волны, HP не меняет.</div>
  </div>
}

function WaveEditor({ wave, onChange, override, onOverrideChange }: {
  wave: SpawnWave
  onChange: (wave: SpawnWave) => void
  override?: WaveOverride
  onOverrideChange?: (override: WaveOverride) => void
}) {
  const set = <K extends keyof SpawnWave>(key: K, value: SpawnWave[K]) => onChange({ ...wave, [key]: value })
  return <div className="space-y-4">
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Label className="col-span-2 space-y-1 text-xs"><span className="text-muted-foreground">Название</span><Input value={wave.name} onChange={(event) => set("name", event.target.value)} /></Label>
      <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Тип позиции</span><Select value={wave.spawn_type} onValueChange={(value) => set("spawn_type", value)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{[["around", "Вокруг"], ["ahead", "Впереди"], ["below", "Снизу"], ["mainpath", "Main path"], ["sidepath", "Side path"], ["cave", "Пещера"], ["ruin", "Руина"], ["wreck", "Затонувший корабль"], ["beaconstation", "Маяк"], ["abyss", "Бездна"], ["abysscave", "Пещера бездны"]].map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></Label>
      <NumberField label="Сложность (-1 = авто)" value={wave.difficulty} onChange={(value) => set("difficulty", value)} />
      <NumberField label="Commonness" value={wave.commonness} onChange={(value) => set("commonness", value)} />
      <NumberField label="Прогресс от" value={wave.min_progress} onChange={(value) => set("min_progress", value)} step="0.05" />
      <NumberField label="Прогресс до" value={wave.max_progress} onChange={(value) => set("max_progress", value)} step="0.05" />
      <NumberField label="Время раунда от, с" value={wave.min_round_time} onChange={(value) => set("min_round_time", value)} />
      <NumberField label="Игроков от" value={wave.min_players} onChange={(value) => set("min_players", value)} />
      <NumberField label="Здоровье субмарины до, %" value={wave.max_sub_health} onChange={(value) => set("max_sub_health", value)} />
      <NumberField label="Здоровье экипажа до, %" value={wave.max_crew_health} onChange={(value) => set("max_crew_health", value)} />
      <NumberField label="Шанс 0..1" value={wave.chance} onChange={(value) => set("chance", value)} step="0.05" />
      <NumberField label="Задержка между монстрами, с" value={wave.delay_between_spawns} onChange={(value) => set("delay_between_spawns", value)} />
      <NumberField label="Scatter, px" value={wave.scatter} onChange={(value) => set("scatter", value)} />
      <NumberField label="Offset вперёд, px" value={wave.offset} onChange={(value) => set("offset", value)} />
      <NumberField label="Дистанция триггера, px" value={wave.spawn_distance} onChange={(value) => set("spawn_distance", value)} />
      <NumberField label="Reset time, с" value={wave.reset_time} onChange={(value) => set("reset_time", value)} />
      <NumberField label="Play dead (-1 = авто)" value={wave.play_dead_probability} onChange={(value) => set("play_dead_probability", value)} step="0.05" />
      <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Тег spawn point</span><Input value={wave.spawn_point_tag ?? ""} onChange={(event) => set("spawn_point_tag", event.target.value)} placeholder="не задан" /></Label>
      <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Блокирует другие волны</span>
        <Select value={wave.non_blocking == null ? "auto" : wave.non_blocking ? "no" : "yes"} onValueChange={(value) => set("non_blocking", value === "auto" ? null : value === "no")}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">Авто (abyss/moloch/cave)</SelectItem>
            <SelectItem value="yes">Да, блокирует</SelectItem>
            <SelectItem value="no">Нет, неблокирующая</SelectItem>
          </SelectContent>
        </Select></Label>
      <NumberField label="Опасность волны (×вес, пусто = авто)" value={wave.danger_override ?? NaN} onChange={(value) => set("danger_override", Number.isFinite(value) ? value : null)} step="0.05" />
    </div>
    <div className="space-y-2 rounded-xl border bg-muted/10 p-3">
      <div className="flex items-center gap-2 text-xs font-medium"><Layers className="size-3.5 text-primary" /> Связка волн <span className="font-normal text-muted-foreground">— после этой волны заспавнится указанная, в том же месте</span></div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Следующая волна (имя)</span><Input value={wave.chain_wave ?? ""} onChange={(event) => set("chain_wave", event.target.value.trim() ? event.target.value : null)} placeholder="не связана" /></Label>
        <NumberField label="Задержка связки, с" value={wave.chain_delay_seconds ?? 0} onChange={(value) => set("chain_delay_seconds", value)} />
        <NumberField label="Шанс связки 0..1" value={wave.chain_chance ?? 1} onChange={(value) => set("chain_chance", value)} step="0.05" />
        <div className="flex items-end"><div className="flex w-full items-center justify-between gap-3 rounded-xl border bg-muted/10 p-2.5 text-xs"><span>В том же месте</span><Switch checked={wave.chain_same_position !== false} onCheckedChange={(value) => set("chain_same_position", value)} /></div></div>
      </div>
    </div>
    {override && onOverrideChange && <OverrideEditor override={override} onChange={onOverrideChange} waveName={wave.name} />}
    <div className="space-y-2"><div className="flex items-center justify-between"><h4 className="text-sm font-medium">Состав волны</h4><Button size="sm" variant="outline" onClick={() => set("monsters", [...wave.monsters, clone(emptyWave().monsters[0])])}><Plus className="size-3.5" /> Существо</Button></div>
      {wave.monsters.map((monster, index) => <div key={index} className="grid grid-cols-2 gap-2 rounded-xl border bg-muted/20 p-3 lg:grid-cols-5">
        <Label className="space-y-1 text-xs"><span>Вид</span><Input value={monster.species} onChange={(event) => { const monsters = [...wave.monsters]; monsters[index] = { ...monster, species: event.target.value }; set("monsters", monsters) }} /></Label>
        {(["count", "amount_min", "amount_max", "max_amount_per_level", "scale_with_players", "hp_multiplier", "hp_scale_with_progress", "mobility", "threat_override"] as const).map((key) => <NumberField key={key} label={key.replaceAll("_", " ")} value={monster[key] ?? NaN} onChange={(value) => { const monsters = [...wave.monsters]; monsters[index] = { ...monster, [key]: key === "threat_override" ? (Number.isFinite(value) ? value : null) : value }; set("monsters", monsters) }} />)}
        <Button className="lg:col-start-5" size="sm" variant="ghost" disabled={wave.monsters.length === 1} onClick={() => set("monsters", wave.monsters.filter((_, itemIndex) => itemIndex !== index))}><Trash2 className="size-3.5" /> Удалить</Button>
      </div>)}
    </div>
  </div>
}

/** 5: универсальный диалог редактирования — всё правится во всплывающем окне. */
function EditorDialog({ open, onOpenChange, title, icon: Icon, description, children, footer }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: React.ReactNode
  icon?: LucideIcon
  description?: React.ReactNode
  children: React.ReactNode
  footer?: React.ReactNode
}) {
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-3xl">
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">{Icon && <span className="flex size-8 items-center justify-center rounded-xl bg-primary/15 text-primary"><Icon className="size-4" /></span>}{title}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>
      <div className="min-w-0">{children}</div>
      <DialogFooter className="items-center gap-2 sm:justify-between">
        <span className="text-[11px] text-muted-foreground">Изменения попадут на сервер после «Сохранить» на странице</span>
        {footer ?? <Button variant="outline" onClick={() => onOpenChange(false)}>Готово</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

/** 2: диалог правки сложности активной волны — абсолют или корректировка,
 *  с предпросмотром итоговой сложности и управлением пометками. */
function WaveOverrideDialog({ open, onOpenChange, waveName, currentDifficulty, monstersAlive, engaged, initial, onApply, onRemove }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  waveName: string
  currentDifficulty: number
  monstersAlive: number
  engaged: boolean
  initial: WaveOverride | null
  onApply: (override: WaveOverride) => void
  onRemove: () => void
}) {
  const [local, setLocal] = useState<WaveOverride>(initial ?? { difficulty_mode: "adjust", difficulty_value: 1, non_blocking: null, enabled: true })
  useEffect(() => {
    if (open) setLocal(initial ?? { difficulty_mode: "adjust", difficulty_value: 1, non_blocking: null, enabled: true })
  }, [open, initial])
  const absolute = local.difficulty_mode === "absolute"
  const set = <K extends keyof WaveOverride>(key: K, value: WaveOverride[K]) => setLocal({ ...local, [key]: value })
  const preview = absolute
    ? local.difficulty_value
    : Math.round(currentDifficulty * (local.difficulty_value || 1) * 100) / 100
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2"><span className="flex size-8 items-center justify-center rounded-xl bg-warning/15 text-warning"><Wand2 className="size-4" /></span>Правка волны</DialogTitle>
        <DialogDescription><span className="font-mono">{waveName}</span> — пометка применится к этой волне сейчас и ко всем будущим волнам этого типа</DialogDescription>
      </DialogHeader>
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div className="rounded-xl border bg-muted/10 p-2.5"><div className="font-mono text-sm">{currentDifficulty.toFixed(1)}</div><div className="text-[10px] text-muted-foreground">текущая сложность</div></div>
        <div className="rounded-xl border bg-muted/10 p-2.5"><div className="font-mono text-sm">{monstersAlive}</div><div className="text-[10px] text-muted-foreground">живых монстров</div></div>
        <div className="rounded-xl border bg-muted/10 p-2.5"><div className={`font-mono text-sm ${engaged ? "text-success" : "text-warning"}`}>{engaged ? "да" : "нет"}</div><div className="text-[10px] text-muted-foreground">в бою</div></div>
      </div>
      <div className="space-y-3">
        <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Режим изменения сложности</span>
          <Select value={local.difficulty_mode} onValueChange={(value) => set("difficulty_mode", value)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="adjust">Корректировка — множитель к текущей (×)</SelectItem>
              <SelectItem value="absolute">Абсолютная — задать значение</SelectItem>
            </SelectContent>
          </Select></Label>
        {absolute
          ? <NumberField label="Целевая сложность волны" value={local.difficulty_value} onChange={(value) => set("difficulty_value", value)} step="0.1" />
          : <SliderField label="Множитель сложности" value={local.difficulty_value || 1} min={0.1} max={5} step={0.05} suffix="x" onChange={(value) => set("difficulty_value", value)} />}
        <div className="rounded-xl border border-primary/30 bg-primary/5 p-3 text-xs"><span className="text-muted-foreground">Итоговая сложность: </span><span className="font-mono text-base font-semibold text-primary">{Number.isFinite(preview) ? preview.toFixed(1) : "—"}</span><span className="text-muted-foreground"> · HP живых монстров масштабируется в момент применения</span></div>
        <SliderField label="Опасность волны (частота выбора, 1 = авто)" value={local.danger_value ?? 1} min={0.1} max={5} step={0.05} suffix="x" onChange={(value) => set("danger_value", Math.abs(value - 1) < 0.001 ? null : value)} />
        <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Блокирует другие волны</span>
          <Select value={local.non_blocking == null ? "auto" : local.non_blocking ? "no" : "yes"} onValueChange={(value) => set("non_blocking", value === "auto" ? null : value === "no")}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Авто (abyss/moloch/cave в названии)</SelectItem>
              <SelectItem value="yes">Да, блокирует следующие</SelectItem>
              <SelectItem value="no">Нет, неблокирующая</SelectItem>
            </SelectContent>
          </Select></Label>
        <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Пометка активна</span><Switch checked={local.enabled !== false} onCheckedChange={(value) => set("enabled", value)} /></div>
      </div>
      <DialogFooter className="flex-row items-center gap-2 sm:justify-between">
        {initial
          ? <Button variant="ghost" onClick={() => { onRemove(); onOpenChange(false) }}><Trash2 className="size-3.5" /> Убрать пометку</Button>
          : <span />}
        <span className="flex gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button onClick={() => { onApply(local); onOpenChange(false) }}><Check className="size-3.5" /> Применить</Button>
        </span>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

/** 3.1/3.5: редактор списков исключений (подстроки в названиях волн). */
function TagListEditor({ label, description, tags, onChange, addPlaceholder }: {
  label: string
  description: string
  tags: string[]
  onChange: (tags: string[]) => void
  addPlaceholder: string
}) {
  const [value, setValue] = useState("")
  const add = () => {
    const trimmed = value.trim()
    if (!trimmed) return
    if (tags.some((t) => t.toLowerCase() === trimmed.toLowerCase())) { setValue(""); return }
    onChange([...tags, trimmed])
    setValue("")
  }
  return <div className="flex h-full flex-col gap-2 rounded-xl border bg-muted/10 p-3 text-xs">
    <div className="flex items-center gap-2 font-medium"><Ban className="size-3.5 text-warning" /> {label}</div>
    <p className="text-muted-foreground">{description}</p>
    <div className="flex min-h-8 flex-wrap gap-1">
      {tags.map((tag, index) => <Badge key={index} variant="secondary" className="gap-1 font-mono text-[10px]">{tag}<button className="rounded-full p-0.5 hover:bg-destructive/20" onClick={() => onChange(tags.filter((_, itemIndex) => itemIndex !== index))}><X className="size-3" /></button></Badge>)}
      {!tags.length && <span className="text-muted-foreground">список пуст</span>}
    </div>
    <div className="mt-auto flex gap-2">
      <Input className="h-8 text-xs" value={value} placeholder={addPlaceholder} onChange={(event) => setValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); add() } }} />
      <Button size="sm" variant="outline" className="h-8" onClick={add}><Plus className="size-3.5" /> Добавить</Button>
    </div>
  </div>
}

function WaveLogs({ logs }: { logs: MonsterSpawnWaveLog[] }) {
  return <div className="overflow-x-auto rounded-xl border"><table className="w-full min-w-[680px] text-left text-xs"><thead className="bg-muted/30 text-muted-foreground"><tr><th className="px-3 py-2 font-medium">Волна</th><th className="px-3 py-2 font-medium">Наблюдений</th><th className="px-3 py-2 font-medium">Среднее время</th><th className="px-3 py-2 font-medium">Эффективность</th><th className="px-3 py-2 font-medium">Потери экипажа</th><th className="px-3 py-2 font-medium">Урон субмарине</th></tr></thead><tbody>{logs.map((log) => <tr key={log.name} className="border-t"><td className="max-w-56 truncate px-3 py-3 font-medium">{log.name}</td><td className="px-3 py-3">{log.times_spawned}<span className="ml-1 text-warning">{log.times_unresolved ? `(${log.times_unresolved} незав.)` : ""}</span></td><td className="px-3 py-3 font-mono">{log.avg_repel_seconds ? `${Math.round(log.avg_repel_seconds)} с` : "—"}</td><td className={`px-3 py-3 font-mono ${log.avg_performance >= 1.2 ? "text-success" : log.avg_performance > 0 && log.avg_performance < .8 ? "text-warning" : ""}`}>{log.avg_performance ? log.avg_performance.toFixed(2) : "—"}</td><td className="px-3 py-3">{log.avg_crew_deaths.toFixed(2)}</td><td className="px-3 py-3">{log.avg_sub_health_loss.toFixed(1)}%</td></tr>)}{!logs.length && <tr><td colSpan={6} className="px-3 py-10 text-center text-muted-foreground">Завершённых волн пока нет</td></tr>}</tbody></table></div>
}

function ShipLogs({ logs }: { logs: MonsterSpawnShipLog[] }) {
  return <div className="grid gap-2 md:grid-cols-2">{logs.map((log) => <div key={log.key} className="rounded-xl border bg-muted/10 p-4"><div className="flex items-start gap-3"><div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary"><ShipWheel className="size-4" /></div><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="truncate text-sm font-semibold">{log.name}</span><Badge variant="secondary" className="ml-auto font-mono">{log.performance_ema.toFixed(2)}</Badge></div><div className="mt-1 text-xs text-muted-foreground">{log.type} · {log.rounds_played} раундов · {log.times_spawned} волн</div></div></div><div className="mt-4 grid grid-cols-3 gap-2 text-center"><div><div className="font-mono text-sm">{log.avg_repel_seconds ? `${Math.round(log.avg_repel_seconds)}с` : "—"}</div><div className="text-[10px] text-muted-foreground">зачистка</div></div><div><div className="font-mono text-sm">{log.avg_crew_deaths.toFixed(2)}</div><div className="text-[10px] text-muted-foreground">потери</div></div><div><div className="font-mono text-sm">{log.avg_sub_health_loss.toFixed(1)}%</div><div className="text-[10px] text-muted-foreground">урон</div></div></div><div className="mt-3 truncate font-mono text-[10px] text-muted-foreground/60">{log.key}</div></div>)}{!logs.length && <div className="rounded-xl border border-dashed p-8 text-center text-xs text-muted-foreground md:col-span-2">Память по кораблям появится после завершённых наблюдений.</div>}</div>
}

type PlanStatus = SpawnPlan["status"] | "all"

const OUTCOME_LABELS: Record<string, { label: string; tone: string }> = {
  repelled: { label: "Отбита в бою", tone: "text-success border-success/30 bg-success/5" },
  bypassed: { label: "Обойдена", tone: "text-warning border-warning/30 bg-warning/5" },
  afk: { label: "Афк (без боя)", tone: "text-muted-foreground border-border bg-muted/20" },
  timeout_engaged: { label: "Таймаут (был бой)", tone: "text-info border-info/30 bg-info/5" },
  unresolved: { label: "Не разрешена", tone: "text-muted-foreground border-border bg-muted/30" },
}

const REASON_LABELS: Record<string, string> = {
  config: "Из конфига",
  learned: "Изученная волна",
  vanilla: "Ванильное событие",
  pace_intercept: "Перехват темпа",
  idle_horde: "Орда простоя",
}

function monsterCountsText(counts: Record<string, number>): string {
  return Object.entries(counts ?? {}).map(([species, count]) => `${species} x${count}`).join(", ") || "состав неизвестен"
}

function reasonLabel(reason?: string | null): string {
  if (!reason) return "—"
  if (reason.startsWith("chain:")) return `Связка (${reason.slice("chain:".length)})`
  if (reason.startsWith("plan:")) return `План (${reason.slice("plan:".length)})`
  return REASON_LABELS[reason] ?? reason
}

function ModificationsList({ modifications }: { modifications: string[] | string }) {
  const list = Array.isArray(modifications) ? modifications : modifications ? [modifications] : []
  if (!list.length) return <div className="text-[11px] text-muted-foreground">Модификаций не было — волна применилась в исходном виде</div>
  return <ul className="space-y-1">{list.map((item, index) => <li key={index} className="font-mono text-[11px] text-warning">{item}</li>)}</ul>
}

function ActiveWaveCard({ wave, override, onToggleNonBlocking, onOpenOverride }: {
  wave: ActiveWaveTelemetry
  override?: WaveOverride
  onToggleNonBlocking?: () => void
  onOpenOverride?: () => void
}) {
  const modified = wave.modifications?.length > 0
  const engaged = wave.engaged === true
  const bypassed = wave.bypassed === true
  const blocking = wave.blocking === true && !bypassed
  const trackingTimeout = Math.max(wave.tracking_timeout_seconds ?? 600, 1)
  const timeoutProgress = Math.max(0, Math.min(wave.elapsed_seconds / trackingTimeout, 1))
  const timeoutRemaining = Math.max(0, Math.ceil(trackingTimeout - wave.elapsed_seconds))
  const timerColor = `hsl(${120 * (1 - timeoutProgress)} 82% 50%)`
  return <div className="space-y-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <Radar className="size-4 text-destructive" />
      <span className="font-semibold">{wave.wave_name}</span>
      <Badge variant="outline" className="text-[10px]">ID {wave.wave_id}</Badge>
      <Badge variant="secondary" className="text-[10px]">{reasonLabel(wave.spawn_reason)}</Badge>
      {bypassed && <Badge variant="outline" className="text-[10px] text-warning border-warning/40">обойдена — бой окончен</Badge>}
      {wave.non_blocking ? <Badge variant="outline" className="text-[10px] text-muted-foreground">неблокирующая</Badge>
        : blocking ? <Badge variant="outline" className="text-[10px] text-success border-success/40">блокирует спавн</Badge>
        : <Badge variant="outline" className="text-[10px] text-warning">не блокирует (афк)</Badge>}
      {wave.monsters_alive > 0 && <Badge variant="outline" className="text-[10px] text-success border-success/40">жива</Badge>}
      {engaged && wave.damage_taken != null && wave.damage_taken > 0 && <Badge variant="outline" className="text-[10px] text-info">урон монстрам {wave.damage_taken.toFixed(0)}</Badge>}
    </div>
    <div className="text-muted-foreground">Состав: <span className="text-foreground">{monsterCountsText(wave.monster_counts)}</span>{wave.spawn_scheme && <span> · схема <b className="text-foreground">{wave.spawn_scheme}</b>{wave.spawn_zone ? ` (${wave.spawn_zone})` : ""}</span>}</div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      <div>
        <div className="font-mono text-sm transition-colors duration-500" style={{ color: timerColor }}>{Math.round(wave.elapsed_seconds)}с</div>
        <div className="text-[10px] text-muted-foreground">Существует уже:</div>
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted/60"><div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${timeoutProgress * 100}%`, background: "linear-gradient(90deg, #22c55e, #eab308, #ef4444)" }} /></div>
        <div className="mt-1 text-[10px] text-muted-foreground">таймаут через {timeoutRemaining}с</div>
      </div>
      <div><div className="font-mono text-sm">{wave.monsters_alive}/{wave.monsters_total}</div><div className="text-[10px] text-muted-foreground">живых монстров</div></div>
      <div><div className="font-mono text-sm">{wave.effective_difficulty.toFixed(1)}</div><div className="text-[10px] text-muted-foreground">сложность</div></div>
    </div>
    {wave.distance_to_sub_meters != null && <div className="text-[11px] text-muted-foreground">Дистанция до корабля: <span className="font-mono text-foreground">{wave.distance_to_sub_meters.toFixed(0)} м</span>{wave.min_distance_meters != null && <> (минимум {wave.min_distance_meters.toFixed(0)} м)</>}</div>}
    {(onOpenOverride || onToggleNonBlocking) && <div className="flex flex-wrap items-center gap-2 border-t pt-2">
      {onOpenOverride && <Button size="sm" variant="outline" onClick={onOpenOverride}><Wand2 className="size-3.5" /> Изменить сложность</Button>}
      {onToggleNonBlocking && <Button size="sm" variant={override?.non_blocking === true ? "secondary" : "outline"} onClick={onToggleNonBlocking}><EyeOff className="size-3.5" /> Неблокирующая</Button>}
      {override && <span className="font-mono text-[10px] text-muted-foreground">пометка: {override.enabled === false ? "выкл" : "вкл"} · {override.difficulty_mode === "absolute" ? `абс=${override.difficulty_value}` : `×${override.difficulty_value}`}{override.non_blocking != null ? ` · ${override.non_blocking ? "неблок." : "блок."}` : ""}{override.danger_value != null ? ` · опасность ×${override.danger_value}` : ""}</span>}
    </div>}
    {modified && <ModificationsList modifications={wave.modifications} />}
  </div>
}

/** Фиксированное HP по видам (как в Директоре волн): каталог + множитель
 *  вместо волнового скейла. Итог = база × множитель, иначе — больший из
 *  нижних порогов. Множитель применяется плагином в течение ~10 секунд. */
function SpeciesHpCard({ creatures, baseline, overrides, baseOverrides, minMult, minFixed, setSpeciesHp, setBaseHp, setMinMult, setMinFixed }: {
  creatures: CreatureInfo[]
  baseline: SpeciesBaselineTelemetry[]
  overrides: Record<string, number>
  baseOverrides: Record<string, number>
  minMult: number
  minFixed: number
  setSpeciesHp: (species: string, value: number | null) => void
  setBaseHp: (species: string, value: number | null) => void
  setMinMult: (value: number) => void
  setMinFixed: (value: number) => void
}) {
  const [search, setSearch] = useState("")
  const byId = useMemo(() => {
    const map = new Map<string, CreatureInfo>()
    for (const creature of creatures) map.set(creature.id.toLowerCase(), creature)
    return map
  }, [creatures])
  const rows = useMemo(() => {
    const keys = new Map<string, string>()
    for (const creature of creatures) if (creature.group !== "human") keys.set(creature.id.toLowerCase(), creature.id)
    for (const entry of baseline) keys.set(entry.species.toLowerCase(), entry.species)
    for (const key of Object.keys(overrides ?? {})) { const lower = key.toLowerCase(); if (!keys.has(lower)) keys.set(lower, key) }
    for (const key of Object.keys(baseOverrides ?? {})) { const lower = key.toLowerCase(); if (!keys.has(lower)) keys.set(lower, key) }
    const query = search.trim().toLowerCase()
    return [...keys.entries()]
      .map(([lower, original]) => {
        const info = byId.get(lower)
        const base = baseline.find((entry) => entry.species.toLowerCase() === lower)
        const overrideKey = Object.keys(overrides ?? {}).find((key) => key.toLowerCase() === lower)
        const raw = overrideKey != null ? Number(overrides[overrideKey]) : 0
        return {
          id: info?.id ?? base?.species ?? original,
          nameRu: info?.name_ru ?? "",
          group: info?.group ?? "",
          baseHp: Number(info?.base_hp ?? base?.base_hp ?? 0),
          threat: Number(base?.effective_threat ?? base?.base_threat ?? Math.max(0.3, Math.min(2.5, Number(info?.combat_strength ?? 100) / 100))),
          baseOverride: Number(baseOverrides[original] ?? baseOverrides[info?.id ?? ""] ?? 0),
          override: Number.isFinite(raw) && raw > 0 ? raw : 0,
          overrideKey: overrideKey ?? original,
        }
      })
      .filter((row) => !query || row.id.toLowerCase().includes(query) || row.nameRu.toLowerCase().includes(query))
      .sort((a, b) => b.threat - a.threat || b.baseHp - a.baseHp)
  }, [baseline, overrides, baseOverrides, byId, creatures, search])
  const effective = (baseHp: number, override: number) =>
    override > 0 ? Math.round(baseHp * override) : Math.max(Math.round(baseHp * minMult), Math.round(minFixed), baseHp)
  return <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Каталог ванильных монстров</CardTitle><CardDescription className="text-xs">Базовая опасность рассчитана по боевой силе вида; адаптивность корректирует её по наблюдениям. Базовое HP можно изменить вручную.</CardDescription></CardHeader><CardContent className="space-y-3 p-4">
    <div className="flex flex-wrap items-end gap-3">
      <div><p className="mb-1 text-xs text-muted-foreground">Мин. множитель HP</p><Input className="w-36" type="number" min="0" step="0.05" value={String(minMult)} onChange={(event) => setMinMult(number(event.target.value))} /></div>
      <div><p className="mb-1 text-xs text-muted-foreground">Мин. фиксированный HP</p><Input className="w-36" type="number" min="0" step="1" value={String(minFixed)} onChange={(event) => setMinFixed(number(event.target.value))} /></div>
      <div className="relative min-w-52 flex-1"><Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-7" placeholder="Поиск вида" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
    </div>
    <p className="text-[11px] text-muted-foreground">Используется больший из двух минимумов. Изменения попадут на сервер после «Сохранить».</p>
    <div className="max-h-[560px] overflow-auto rounded-xl border"><table className="w-full min-w-[700px] text-left text-xs"><thead className="sticky top-0 bg-muted text-muted-foreground"><tr><th className="px-3 py-2 font-medium">Вид</th><th className="px-3 py-2 font-medium">Ванильное HP</th><th className="px-3 py-2 font-medium">Базовое HP</th><th className="px-3 py-2 font-medium">Множитель</th><th className="px-3 py-2 font-medium">Итог HP</th><th className="px-3 py-2 font-medium">Угроза</th></tr></thead><tbody>{rows.map((row) => {
      const hp = row.baseOverride > 0 ? row.baseOverride : row.baseHp
      const total = effective(hp, row.override)
      return <tr key={row.id.toLowerCase()} className="border-t"><td className="px-3 py-2"><span className="font-medium">{row.nameRu || row.id}</span><span className="ml-2 text-muted-foreground">{row.id}</span>{row.group && <p className="text-[10px] text-muted-foreground">{row.group}</p>}</td><td className="px-3 py-2 font-mono">{row.baseHp}</td><td className="px-3 py-2"><Input type="number" min="1" step="1" className="h-7 w-24 text-xs" placeholder={String(row.baseHp)} value={row.baseOverride > 0 ? String(row.baseOverride) : ""} onChange={(event) => { const raw = event.target.value.trim(); setBaseHp(row.id, raw === "" ? null : number(raw)) }} /></td><td className="px-3 py-2"><Input type="number" min="0" step="0.1" className="h-7 w-24 text-xs" placeholder="авто" value={row.override > 0 ? String(row.override) : ""} onChange={(event) => { const raw = event.target.value.trim(); setSpeciesHp(row.overrideKey, raw === "" ? null : number(raw)) }} /></td><td className={`px-3 py-2 font-mono ${row.override > 0 || row.baseOverride > 0 ? "text-warning" : ""}`}>{total} HP</td><td className={`px-3 py-2 font-mono ${row.threat >= 1.5 ? "text-warning" : row.threat <= 0.8 ? "text-success" : ""}`}>{row.threat.toFixed(2)}</td></tr>
    })}{!rows.length && <tr><td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">Виды не найдены</td></tr>}</tbody></table></div>
  </CardContent></Card>
}

function RecentWaveCard({ wave }: { wave: RecentWaveTelemetry }) {
  const outcome = OUTCOME_LABELS[wave.outcome] ?? { label: wave.outcome, tone: "text-muted-foreground border-border bg-muted/30" }
  // 7: статус судьбы волны — уничтожена или отслеживание прекращено.
  const FATE_LABELS: Record<string, { label: string; tone: string }> = {
    destroyed: { label: "Уничтожена", tone: "text-success border-success/30 bg-success/5" },
    tracking_stopped: { label: "Отслеживание прекращено", tone: "text-muted-foreground border-border bg-muted/20" },
  }
  const fate = wave.fate ? FATE_LABELS[wave.fate] ?? null : null
  const modified = wave.modifications && wave.modifications.length > 0
  return <div className="space-y-2 rounded-xl border bg-muted/10 p-3 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-semibold">{wave.wave_name}</span>
      <Badge variant="outline" className="text-[10px]">ID {wave.wave_id}</Badge>
      {fate && <Badge variant="outline" className={`text-[10px] ${fate.tone}`}>{fate.label}</Badge>}
      <Badge variant="outline" className={`text-[10px] ${outcome.tone}`}>{outcome.label}</Badge>
      <Badge variant="secondary" className="text-[10px]">{reasonLabel(wave.spawn_reason)}</Badge>
    </div>
    <div className="text-muted-foreground">Состав: <span className="text-foreground">{monsterCountsText(wave.monster_counts)}</span></div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div><div className="font-mono text-sm">{Math.round(wave.repel_seconds)}с</div><div className="text-[10px] text-muted-foreground">зачистка (ожидалось {Math.round(wave.expected_repel_seconds)}с)</div></div>
      <div><div className="font-mono text-sm">{wave.performance.toFixed(2)}</div><div className="text-[10px] text-muted-foreground">performance</div></div>
      <div><div className="font-mono text-sm">{wave.crew_deaths}</div><div className="text-[10px] text-muted-foreground">потери экипажа</div></div>
      <div><div className="font-mono text-sm">{wave.sub_health_loss.toFixed(1)}%</div><div className="text-[10px] text-muted-foreground">урон лодке</div></div>
    </div>
    {modified ? <ModificationsList modifications={wave.modifications} /> : <div className="text-[11px] text-muted-foreground">Без модификаций</div>}
  </div>
}

function PlannedActionCard({ planned }: { planned: PlannedActionTelemetry }) {
  const probabilityPercent = Math.round((planned.probability ?? 0) * 100)
  const checkLabels: Record<string, string> = { mode: "Режим", round_active: "Раунд", grace_active: "Стартовая задержка", active_waves: "Активные волны", wave_limit: "Лимит волн", pending_waves: "Ожидают спавна", short_term_difficulty: "Текущая сложность", difficulty_budget: "Бюджет", pace_ratio: "Темп", idle_seconds: "Простой, с" }
  return <Card className="py-0"><CardContent className="space-y-2 p-4">
    <div className="flex items-center gap-2 text-sm font-medium"><Zap className="size-4 text-primary" /> Что планирует контроллер</div>
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant="outline" className="font-mono text-xs">{planned.action}</Badge>
      <span className="text-xs text-muted-foreground">вероятность ближайшего спавна:</span>
      <span className="font-mono text-sm text-primary">{probabilityPercent}%</span>
    </div>
    <div className="text-xs text-muted-foreground">{planned.detail}</div>
    {planned.checks && <div className="grid gap-1.5 border-t pt-3 text-xs sm:grid-cols-2 xl:grid-cols-5">{Object.entries(planned.checks).map(([key, value]) => <div key={key} className="rounded-md bg-muted/35 px-2.5 py-2"><span className="text-muted-foreground">{checkLabels[key] ?? key}</span><span className="ml-2 font-medium">{typeof value === "boolean" ? value ? "да" : "нет" : String(value)}</span></div>)}</div>}
  </CardContent></Card>
}

function ExpressTuning({ global, vanilla, setGlobal, setVanilla }: {
  global: Record<string, unknown>
  vanilla: Record<string, unknown>
  setGlobal: (key: string, value: unknown) => void
  setVanilla: (key: string, value: unknown) => void
}) {
  return <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="flex items-center gap-2 text-sm"><SlidersHorizontal className="size-4 text-primary" /> Экспресс-настройка сложности</CardTitle><CardDescription className="text-xs">Ключевые ориентиры применяются сразу после «Сохранить» — плагин подхватит их в течение ~10 секунд</CardDescription></CardHeader>
    <CardContent className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
      <div className="col-span-full border-b pb-1 text-xs font-semibold text-muted-foreground">Темп и интенсивность</div><SliderField label="Целевая сложность в минуту" value={Number(global.target_difficulty_per_minute ?? 0.8)} min={0.1} max={3} step={0.05} onChange={(value) => setGlobal("target_difficulty_per_minute", value)} />
      <SliderField label="Пауза между спавнами, с" value={Number(global.min_seconds_between_spawns ?? 90)} min={15} max={600} step={5} suffix="с" onChange={(value) => setGlobal("min_seconds_between_spawns", value)} />
      <SliderField label="Пауза между abyss-волн, мин" value={Number(global.abyss_wave_cooldown_seconds ?? 1800) / 60} min={30} max={120} step={5} suffix=" мин" onChange={(value) => setGlobal("abyss_wave_cooldown_seconds", value * 60)} />
      <SliderField label="Бюджет краткоср. сложности" value={Number(global.max_short_term_difficulty ?? 6)} min={1} max={30} step={0.5} onChange={(value) => setGlobal("max_short_term_difficulty", value)} />
      <SliderField label="Порог интенсивности" value={Number(global.intensity_threshold ?? 0.2)} min={0.05} max={0.9} step={0.01} onChange={(value) => setGlobal("intensity_threshold", value)} />
      <div className="col-span-full border-b pb-1 pt-2 text-xs font-semibold text-muted-foreground">Ванильные события</div><SliderField label="Vanilla: количество" value={Number(vanilla.amount_multiplier ?? 1)} min={0.1} max={3} step={0.05} suffix="x" onChange={(value) => setVanilla("amount_multiplier", value)} />
      <SliderField label="Vanilla: сила (HP)" value={Number(vanilla.difficulty_multiplier ?? 1)} min={0.3} max={2.5} step={0.05} suffix="x" onChange={(value) => setVanilla("difficulty_multiplier", value)} />
      <SliderField label="Vanilla: удержание спавна, с" value={Number(vanilla.hold_seconds ?? 30)} min={0} max={120} step={5} suffix="с" onChange={(value) => setVanilla("hold_seconds", value)} />
      <div className="col-span-full border-b pb-1 pt-2 text-xs font-semibold text-muted-foreground">Столкновения и размещение</div><SliderField label="Смещение к опасным видам" value={Number(global.danger_bias_strength ?? 0.5)} min={0} max={1.5} step={0.05} onChange={(value) => setGlobal("danger_bias_strength", value)} />
      <SliderField label="Эскалация обхода волн" value={Number(global.bypass_difficulty_escalation ?? 0.5)} min={0} max={1} step={0.05} onChange={(value) => setGlobal("bypass_difficulty_escalation", value)} />
       <SliderField label="Макс. одновременных волн" value={Number(global.max_simultaneous_waves ?? 1)} min={0} max={4} step={1} onChange={(value) => setGlobal("max_simultaneous_waves", value)} />
       <SliderField label="Таймаут отслеживания волны, с" value={Number(global.max_wave_tracking_seconds ?? 600)} min={60} max={1800} step={30} suffix="с" onChange={(value) => setGlobal("max_wave_tracking_seconds", value)} />
      <SliderField label="Таймаут без боя, с" value={Number(global.unengaged_block_timeout_seconds ?? 60)} min={0} max={300} step={5} suffix="с" onChange={(value) => setGlobal("unengaged_block_timeout_seconds", value)} />
      <SliderField label="Дистанция обхода, м" value={Number(global.bypass_distance_meters ?? 200)} min={50} max={500} step={10} suffix="м" onChange={(value) => setGlobal("bypass_distance_meters", value)} />
       <SliderField label="Мин. дистанция спавна, м" value={Number(global.spawn_min_distance_meters ?? 100)} min={30} max={400} step={10} suffix="м" onChange={(value) => setGlobal("spawn_min_distance_meters", value)} />
       <SliderField label="Стартовая зона спавна, % уровня" value={Number(global.spawn_start_zone_progress ?? 0.1) * 100} min={0} max={30} step={1} suffix="%" onChange={(value) => setGlobal("spawn_start_zone_progress", value / 100)} />
       <SliderField label="Сдвиг из стартовой зоны, м" value={Number(global.spawn_start_zone_forward_shift_min_meters ?? 50)} min={0} max={300} step={10} suffix="м" onChange={(value) => setGlobal("spawn_start_zone_forward_shift_min_meters", value)} />
       <SliderField label="Макс. сдвиг из стартовой зоны, м" value={Number(global.spawn_start_zone_forward_shift_max_meters ?? 100)} min={0} max={500} step={10} suffix="м" onChange={(value) => setGlobal("spawn_start_zone_forward_shift_max_meters", value)} />
      <div className="col-span-full border-b pb-1 pt-2 text-xs font-semibold text-muted-foreground">Стартовая защита и сила экипажа</div><SliderField label="Стартовая задержка: прогресс, %" value={Number(global.initial_grace_min_progress ?? 0.05) * 100} min={0} max={50} step={1} suffix="%" onChange={(value) => setGlobal("initial_grace_min_progress", value / 100)} />
      <SliderField label="Стартовая задержка: время, мин" value={Number(global.initial_grace_min_round_seconds ?? 1200) / 60} min={0} max={60} step={1} suffix=" мин" onChange={(value) => setGlobal("initial_grace_min_round_seconds", value * 60)} />
      <SliderField label="Скейл количества за игрока, %" value={Number(global.player_count_scale_per_extra ?? 0.15) * 100} min={0} max={50} step={1} suffix="%" onChange={(value) => setGlobal("player_count_scale_per_extra", value / 100)} />
      <SliderField label="Скейл HP за игрока, %" value={Number(global.player_hp_scale_per_extra ?? 0.08) * 100} min={0} max={30} step={1} suffix="%" onChange={(value) => setGlobal("player_hp_scale_per_extra", value / 100)} />
      <SliderField label="Экспонента силы экипажа" value={Number(global.player_power_exponent ?? 0.85)} min={0.3} max={1.5} step={0.05} onChange={(value) => setGlobal("player_power_exponent", value)} />
      <NumberField label="Baseline игроков" value={Number(global.balance_baseline_players ?? 4)} onChange={(value) => setGlobal("balance_baseline_players", Math.round(value))} step="1" />
      <NumberField label="Глубина связок волн" value={Number(global.chain_max_depth ?? 3)} onChange={(value) => setGlobal("chain_max_depth", Math.round(value))} step="1" />
      <div className="rounded-xl border bg-muted/10 p-3 text-[11px] text-muted-foreground sm:col-span-2">Стартовая задержка: волны запрещены, пока корабль не продвинется дальше заданного прогресса{global.initial_grace_require_both !== false ? " и " : " или "}не пройдёт заданное время раунда. Сила экипажа = (игроков)<sup>экспонента</sup> — ожидаемое время зачистки делится на неё.</div>
      <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Стартовая задержка</span><Switch checked={global.initial_grace_enabled !== false} onCheckedChange={(value) => setGlobal("initial_grace_enabled", value)} /></div>
      <div className="rounded-xl border border-info/30 bg-info/5 p-3 text-xs text-muted-foreground"><span className="font-medium text-foreground">Стартовая защита снимается по первому порогу</span><br />Достаточно пройти заданный процент уровня или дождаться заданного времени раунда.</div>
      <div className="rounded-xl border bg-muted/10 p-3 text-[11px] text-muted-foreground sm:col-span-2">Политика столкновения: волна считается в бою, если монстрам нанесён хоть какой-то урон. Без боя волна блокирует спавн только первые <b className="text-foreground">{Math.round(Number(global.unengaged_block_timeout_seconds ?? 60))}с</b>, а при отходе корабля дальше <b className="text-foreground">{Math.round(Number(global.bypass_distance_meters ?? 200))} м</b> считается обойдённой. Волны с abyss/moloch/cave в названии неблокирующие по умолчанию.</div>
       <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Схемы позиционирования спавна</span><Switch checked={global.spawn_scheme_enabled !== false} onCheckedChange={(value) => setGlobal("spawn_scheme_enabled", value)} /></div>
       <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Запрет покинутой стартовой зоны</span><Switch checked={global.spawn_start_zone_enabled !== false} onCheckedChange={(value) => setGlobal("spawn_start_zone_enabled", value)} /></div>
      <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Перехват темпа</span><Switch checked={global.pace_intercept_enabled !== false} onCheckedChange={(value) => setGlobal("pace_intercept_enabled", value)} /></div>
      <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Изучение ванильных волн</span><Switch checked={global.learned_waves_enabled !== false} onCheckedChange={(value) => setGlobal("learned_waves_enabled", value)} /></div>
    </CardContent></Card>
}

const newScheme = (): SpawnScheme => ({
  name: "новая схема", weight: 1, distance_min_meters: 100, distance_max_meters: 200,
  zones: { front: 1, top: 0.3, bottom: 0.3 },
})

const SCHEME_ZONE_LABELS: Record<string, string> = { front: "Вес «перед»", top: "Вес «сверху»", bottom: "Вес «снизу»" }

/** Визуальная схема спавна: корабль в центре, сектора перед/сверху/снизу,
 *  сектор «сзади» всегда запрещён. Интенсивность сектора = вес зоны. */
function SchemeDiagram({ scheme, minMeters }: { scheme: SpawnScheme; minMeters: number }) {
  const zoneWeight = (zone: string) => {
    if (zone === "behind") return 0
    const value = scheme.zones?.[zone]
    if (typeof value === "number") return Math.max(value, 0)
    return zone === "front" ? 1 : zone === "top" || zone === "bottom" ? 0.25 : 0
  }
  const cx = 190
  const cy = 78
  const wedge = (fromDeg: number, toDeg: number, radius: number, weight: number, muted = false) => {
    const toXY = (deg: number): [number, number] => [cx + radius * Math.cos((deg * Math.PI) / 180), cy - radius * Math.sin((deg * Math.PI) / 180)]
    const [x1, y1] = toXY(fromDeg)
    const [x2, y2] = toXY(toDeg)
    return <path key={`${fromDeg}-${toDeg}`} d={`M ${cx} ${cy} L ${x1.toFixed(1)} ${y1.toFixed(1)} A ${radius} ${radius} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)} Z`} fill={muted ? "hsl(var(--muted-foreground))" : "hsl(var(--primary))"} fillOpacity={muted ? 0.12 : Math.min(0.1 + weight * 0.4, 0.85)} stroke="hsl(var(--border))" strokeWidth="0.5" />
  }
  return <svg viewBox="0 0 300 160" className="w-full">
    {wedge(135, 225, 58, 0, true)}
    {wedge(-55, 55, 92, zoneWeight("front"))}
    {wedge(55, 125, 70, zoneWeight("top"))}
    {wedge(-125, -55, 70, zoneWeight("bottom"))}
    <ellipse cx={cx} cy={cy} rx={24} ry={8} className="fill-foreground" opacity={0.9} />
    <polygon points={`${cx + 28},${cy} ${cx + 20},${cy - 5} ${cx + 20},${cy + 5}`} className="fill-foreground" />
    <text x={cx} y={cy + 20} textAnchor="middle" className="fill-muted-foreground" fontSize="8">корабль</text>
    <text x={262} y={cy - 4} textAnchor="middle" className="fill-muted-foreground" fontSize="9">перед</text>
    <text x={cx + 66} y={cy + 3} textAnchor="middle" className="fill-foreground" fontSize="8" fontFamily="monospace">{Math.round(Math.max(scheme.distance_min_meters || minMeters, minMeters))}–{Math.round(scheme.distance_max_meters || minMeters)} м</text>
    <text x={cx} y={12} textAnchor="middle" className="fill-muted-foreground" fontSize="9">сверху</text>
    <text x={cx} y={154} textAnchor="middle" className="fill-muted-foreground" fontSize="9">снизу</text>
    <text x={70} y={cy + 4} textAnchor="middle" className="fill-muted-foreground" fontSize="9">сзади — запрещено</text>
  </svg>
}

/** 6: тело редактора схемы (используется в диалоге). */
function SchemeEditor({ scheme, onChange, minMeters }: {
  scheme: SpawnScheme
  onChange: (scheme: SpawnScheme) => void
  minMeters: number
}) {
  const set = <K extends keyof SpawnScheme>(key: K, value: SpawnScheme[K]) => onChange({ ...scheme, [key]: value })
  const setZone = (zone: string, value: number) => onChange({ ...scheme, zones: { ...(scheme.zones ?? {}), [zone]: value } })
  return <div className="space-y-3">
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Название</span><Input value={scheme.name} onChange={(event) => set("name", event.target.value)} /></Label>
      <NumberField label="Вес выбора (0..5)" value={scheme.weight} onChange={(value) => set("weight", value)} step="0.1" />
      <NumberField label="Дистанция от, м" value={scheme.distance_min_meters} onChange={(value) => set("distance_min_meters", value)} />
      <NumberField label="Дистанция до, м" value={scheme.distance_max_meters} onChange={(value) => set("distance_max_meters", value)} />
    </div>
    <div className="grid gap-3 md:grid-cols-[minmax(260px,340px)_1fr]">
      <div className="rounded-xl border bg-muted/10 p-2"><SchemeDiagram scheme={scheme} minMeters={minMeters} /></div>
      <div className="grid gap-2 sm:grid-cols-3">
        {["front", "top", "bottom"].map((zone) => <SliderField key={zone} label={SCHEME_ZONE_LABELS[zone]} value={Math.max(scheme.zones?.[zone] ?? (zone === "front" ? 1 : 0.25), 0)} min={0} max={2} step={0.05} onChange={(value) => setZone(zone, value)} />)}
        <div className="rounded-xl border border-dashed p-3 text-[11px] text-muted-foreground sm:col-span-3">Сектор «сзади» жёстко запрещён: волны никогда не спавнятся позади корабля. Дистанции в схеме не могут быть меньше минимальной дистанции спавна ({Math.round(minMeters)} м).</div>
      </div>
    </div>
  </div>
}

/** 6: компактная строка схемы в списке. */
function SchemeRow({ scheme, index, onEdit, onDelete, overrideTag }: {
  scheme: SpawnScheme
  index: number
  onEdit: () => void
  onDelete: () => void
  overrideTag?: React.ReactNode
}) {
  const zones = ["front", "top", "bottom"].map((zone) => {
    const value = Math.max(scheme.zones?.[zone] ?? (zone === "front" ? 1 : 0.25), 0)
    const labels: Record<string, string> = { front: "перед", top: "сверху", bottom: "снизу" }
    return `${labels[zone]} ${value.toFixed(2)}`
  }).join(" · ")
  return <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-3 text-xs">
    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary"><Layers className="size-4" /></span>
    <span className="min-w-0 flex-1">
      <span className="flex items-center gap-2"><span className="truncate text-sm font-medium">{scheme.name || `Схема #${index + 1}`}</span><Badge variant="secondary" className="text-[10px]">вес {scheme.weight}</Badge></span>
      <span className="block truncate text-muted-foreground">{Math.round(scheme.distance_min_meters)}–{Math.round(scheme.distance_max_meters)} м · {zones}</span>
    </span>
    {overrideTag}
    <Button size="sm" variant="outline" onClick={onEdit}><Pencil className="size-3.5" /> Править</Button>
    <Button size="sm" variant="ghost" onClick={onDelete}><Trash2 className="size-3.5" /> Удалить</Button>
  </div>
}

function LLMPanel({ server, reports, learning }: { server: string; reports: RoundReport[]; learning: boolean }) {
  const queryClient = useQueryClient()
  const [instruction, setInstruction] = useState("")
  const [mode, setMode] = useState<"analyze" | "apply" | "spawn">("analyze")
  const runtime = useQuery({ queryKey: ["monster-spawn-llm", server], queryFn: () => monsterSpawnApi.llmRuntime(server), refetchInterval: 10000 })
  // Durable run records: полный результат больше не в AppSetting (он
  // обрезался 2000-символьным лимитом и «терялся»), а в таблице циклов.
  const runsQuery = useQuery({ queryKey: ["monster-spawn-llm-runs", server], queryFn: () => monsterSpawnApi.llmRuns(server, 30), refetchInterval: 5000 })
  const saveRuntime = useMutation({
    mutationFn: (body: Partial<Pick<LLMRuntime, "enabled" | "auto_apply" | "auto_interval_minutes" | "live_analysis_enabled" | "live_analysis_interval_seconds">>) => monsterSpawnApi.updateLLMRuntime(server, body),
    onSuccess: () => { toast.success("Настройки ассистента обновлены"); queryClient.invalidateQueries({ queryKey: ["monster-spawn-llm", server] }) },
    onError: (error: Error) => toast.error(error.message),
  })
  const run = useMutation({
    mutationFn: () => monsterSpawnApi.runLLM(server, { mode, instruction: instruction.trim() || undefined }),
    onSuccess: () => { toast.success("Цикл поставлен в очередь — результат появится ниже"); queryClient.invalidateQueries({ queryKey: ["monster-spawn-llm", server] }); queryClient.invalidateQueries({ queryKey: ["monster-spawn-llm-runs", server] }) },
    onError: (error: Error) => toast.error(error.message),
  })
  const cancelRun = useMutation({
    mutationFn: (runId: string) => monsterSpawnApi.cancelLLMRun(server, runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["monster-spawn-llm", server] })
      queryClient.invalidateQueries({ queryKey: ["monster-spawn-llm-runs", server] })
      queryClient.invalidateQueries({ queryKey: ["ai-center-task-log"] })
      queryClient.invalidateQueries({ queryKey: ["ai-llm-queue"] })
      toast.success("LLM-цикл отменён")
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const applyProposal = useMutation({
    mutationFn: (runId: string) => monsterSpawnApi.applyProposal(server, runId),
    onSuccess: () => { toast.success("Предложенные настройки применены"); queryClient.invalidateQueries({ queryKey: ["monster-spawn", server] }) },
    onError: (error: Error) => toast.error(error.message),
  })

  const r = runtime.data?.runtime
  const deployment = runtime.data?.deployment
  const records: LLMRunRecord[] = runsQuery.data?.runs ?? []
  const last = records[0] ?? null
  const latestProposal = records.find((record) => record.source === "auto" && record.instruction?.startsWith("round-report:"))
    ?? records.find((record) => record.source !== "live" && record.status === "succeeded" && (record.result?.proposed_changes?.length ?? 0) > 0)
  const queued = r?.pending_run ?? null
  const active = r?.active_run ?? null
  const busy = run.isPending || queued !== null || active !== null

  return <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3">
    <CardTitle className="flex items-center gap-2 text-sm"><Bot className="size-4 text-primary" /> LLM-ассистент</CardTitle>
    <CardDescription className="text-xs">{deployment?.has_base_url ? `агрегатор: ${deployment.base_url} · модель: ${deployment.model} · t=${deployment.temperature}` : "Не настроен — задайте AI_DOSSIER_URL и AI_DOSSIER_API_KEY в .env (см. docs/MONSTER_LLM_SETUP.md)"}</CardDescription>
  </CardHeader>
    <CardContent className="space-y-4 p-4">
      {learning && <div className="rounded-lg border border-info/30 bg-info/5 p-3 text-xs">Режим обучения: модель анализирует ванильные волны и предлагает настройки для будущего активного режима. Автоцикл не применяет изменения и не создаёт спавны.</div>}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Ассистент включён</span><Switch checked={r?.enabled === true} onCheckedChange={(value) => saveRuntime.mutate({ enabled: value })} /></div>
        <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Автоприменение</span><Switch checked={r?.auto_apply === true} onCheckedChange={(value) => saveRuntime.mutate({ auto_apply: value })} /></div>
        <Label className="space-y-1 rounded-xl border bg-muted/10 p-3 text-xs"><span className="text-muted-foreground">Автоцикл, мин (0 = вручную)</span><Input type="number" min={0} max={1440} value={r?.auto_interval_minutes ?? 0} onChange={(event) => saveRuntime.mutate({ auto_interval_minutes: Math.max(0, Number(event.target.value) || 0) })} /></Label>
        <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Анализ раунда в реальном времени</span><Switch checked={r?.live_analysis_enabled === true} onCheckedChange={(value) => saveRuntime.mutate({ live_analysis_enabled: value })} /></div>
        <Label className="space-y-1 rounded-xl border bg-muted/10 p-3 text-xs"><span className="text-muted-foreground">Интервал live-анализа, сек. (30–600)</span><Input type="number" min={30} max={600} value={r?.live_analysis_interval_seconds ?? 60} onChange={(event) => saveRuntime.mutate({ live_analysis_interval_seconds: Math.max(30, Math.min(600, Number(event.target.value) || 60)) })} /></Label>
      </div>
      <p className="text-[11px] text-muted-foreground">Запускается только при включённом ассистенте, активном раунде и свежем снимке обзора мира. Отчёт сохраняется в журнале; настройки остаются предложениями для оператора.</p>
      <div className="space-y-2">
        <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Промпт для LLM (необязательно; дополняет системный промпт анализа)</span><Textarea rows={3} maxLength={2000} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="Например: удели внимание длительным столкновениям и потерям экипажа" /></Label>
        <div className="flex flex-wrap gap-2">
          <Select value={mode} onValueChange={(value) => setMode(value as typeof mode)}><SelectTrigger className="w-56"><SelectValue /></SelectTrigger><SelectContent>
            <SelectItem value="analyze">analyze · разбор и рекомендации</SelectItem>
            <SelectItem value="apply">apply · применить настройки</SelectItem>
            <SelectItem value="spawn">spawn · настройки + спавн</SelectItem>
          </SelectContent></Select>
          <Button disabled={busy || !runtime.data?.endpoint_configured} onClick={() => run.mutate()}><FlaskConical className="size-4" /> {active ? "Модель думает…" : queued ? "В очереди…" : run.isPending ? "Отправка…" : "Запустить LLM-цикл"}</Button>
        </div>
        {(queued || active) && <div className="text-[11px] text-muted-foreground">
          <div className="flex flex-wrap items-center gap-2">
            <span>{active ? `Выполняется ${active.mode} (запущен ${active.started_at.slice(0, 16).replace("T", " ")}, попытка ${active.attempts}) — страница опрашивает статус, её можно закрыть.` : `В очереди ${queued!.mode} (запрошен ${queued!.requested_at.slice(0, 16).replace("T", " ")}) — выполняется воркером, результат появится ниже.`}</span>
            <Button size="sm" variant="ghost" className="h-6 px-2 text-destructive" onClick={() => cancelRun.mutate((active ?? queued)!.id)} disabled={cancelRun.isPending}><Ban className="mr-1 size-3" />Отменить цикл</Button>
          </div>
        </div>}
      </div>
      <div className="rounded-xl border bg-muted/10 p-3 text-xs">
        <div className="font-medium">Отчёт по последним 15 раундам</div>
        <p className="mt-1 text-muted-foreground">Обновляется автоматически после каждого третьего завершённого раунда. Каждый раунд получает отдельное заключение.</p>
        {latestProposal?.status === "succeeded" && latestProposal.result ? <div className="mt-3 space-y-2">
          <div className="text-muted-foreground">{latestProposal.result?.crew_summary || latestProposal.result?.situation}</div>
          {latestProposal.result?.proposed_changes?.map((change, index) => <div key={index} className="flex flex-wrap items-center gap-2 rounded-lg border bg-background p-2"><span className="font-mono font-medium">{change.key}</span><span>{String(change.old_value ?? "—")} → {String(change.new_value)}</span><span className="text-muted-foreground">{change.reason}</span></div>)}
          {(latestProposal.result.proposed_changes?.length ?? 0) > 0 ? <><details><summary className="cursor-pointer text-muted-foreground">Готовый JSON изменений</summary><pre className="mt-2 overflow-auto rounded-lg bg-background p-2">{JSON.stringify(latestProposal.result.proposed_changes, null, 2)}</pre></details><Button size="sm" disabled={applyProposal.isPending} onClick={() => applyProposal.mutate(latestProposal.run_id)}>Применить предложенные настройки</Button></> : <p className="text-muted-foreground">LLM не предложила менять настройки.</p>}
        </div> : <p className="mt-2 text-muted-foreground">{latestProposal?.status === "running" || latestProposal?.status === "queued" ? "Отчёт формируется в общей очереди LLM…" : latestProposal?.status === "failed" ? `Ошибка отчёта: ${latestProposal.error_message}` : "Предложений пока нет."}</p>}
      </div>
      <RoundReports reports={reports} server={server} />
      {last && <div className="space-y-2 rounded-xl border bg-muted/10 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={last.status === "succeeded" ? "default" : last.status === "failed" ? "destructive" : "secondary"}>{last.status === "succeeded" ? "готово" : last.status === "failed" ? "ошибка" : last.status === "cancelled" ? "отменено" : last.status === "running" ? "выполняется" : "в очереди"}</Badge>
          <Badge variant="outline">{last.mode}</Badge>
          <Badge variant="secondary">{last.source === "live" ? "мониторинг раунда" : last.source === "auto" ? "автоцикл" : "вручную"}</Badge>
          <span className="text-muted-foreground">{formatBrowserDate(last.requested_at)}{last.duration_ms != null ? ` · ${last.duration_ms}мс` : ""}{last.confidence != null ? ` · уверенность ${Math.round(last.confidence * 100)}%` : ""}{last.applied_count ? ` · применено ${last.applied_count}` : ""}{last.spawned_count ? ` · спавн ${last.spawned_count}` : ""}{last.rejected_count ? ` · отклонено ${last.rejected_count}` : ""}</span>
        </div>
        {last.instruction && <div className="text-muted-foreground">Команда оператора: «{last.instruction}»</div>}
        {last.status === "failed" && <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-2 text-destructive">Ошибка: {last.error_message || "неизвестная ошибка"}</div>}
        {last.result && <div className="space-y-2">
          {last.result.situation && <div className="text-foreground">{last.result.situation}</div>}
          {last.result.scores && <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{([
            ["Эффективность экипажа", last.result.scores.crew_effectiveness],
            ["Сила монстров", last.result.scores.monster_strength],
            ["Давление волн", last.result.scores.wave_pressure],
            ["Эффективность корабля", last.result.scores.ship_efficiency],
          ] as const).map(([label, score]) => <div key={label} className="rounded-lg bg-background/60 p-2"><div className="text-muted-foreground">{label}</div><div className="mt-1 text-lg font-semibold">{score == null ? "—" : `${Math.round(score)}/100`}</div></div>)}</div>}
          {last.result.scores?.basis && <div className="text-muted-foreground">Основание оценок: {last.result.scores.basis}</div>}
          {last.result.crew_summary && <div className="rounded-lg bg-background/60 p-3"><div className="mb-1 font-medium text-foreground">Эффективность экипажа</div><div className="text-muted-foreground">{last.result.crew_summary}</div></div>}
          {(last.result.proposed_changes?.length ?? 0) > 0 && <div><div className="font-medium">Предложения настроек (не применены):</div><ul className="mt-1 space-y-1">{last.result.proposed_changes!.map((change, index) => <li key={index} className="rounded-lg bg-background/60 p-2"><span className="font-mono">{change.key}: {String(change.old_value ?? "?")} → {String(change.new_value)}</span>{change.reason && <span className="text-muted-foreground"> — {change.reason}</span>}</li>)}</ul></div>}
          {last.result.live_metrics && <details><summary className="cursor-pointer text-muted-foreground">Показатели снимка мира и расчётной силы волн</summary><pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-background p-2">{JSON.stringify(last.result.live_metrics, null, 2)}</pre></details>}
          {(last.result.recommendations?.length ?? 0) > 0 && <div><div className="font-medium">Рекомендации оператору:</div><ul className="mt-1 space-y-1">{last.result.recommendations!.map((rec, index) => <li key={index} className="rounded-lg bg-background/60 p-2"><span className="font-medium text-foreground">{rec.title}</span>{rec.detail && <span className="text-muted-foreground"> — {rec.detail}</span>}</li>)}</ul></div>}
          {(last.result.applied_changes?.length ?? 0) > 0 && <div><div className="font-medium">Применённые изменения:</div><ul className="mt-1 space-y-1">{last.result.applied_changes!.map((change, index) => <li key={index} className="font-mono text-[11px] text-success">{change.key}: {String(change.old_value ?? "?")} → {String(change.new_value)} {change.reason ? `— ${change.reason}` : ""}</li>)}</ul></div>}
          {(last.result.spawn_requests?.length ?? 0) > 0 && <div><div className="font-medium">Спавн:</div><ul className="mt-1 space-y-1">{last.result.spawn_requests!.map((request, index) => <li key={index} className="font-mono text-[11px] text-info">{request.species} x{request.count} ({request.position_hint}) {request.reason ? `— ${request.reason}` : ""}</li>)}</ul></div>}
          {(last.result.rejected?.length ?? 0) > 0 && <div><div className="font-medium text-warning">Отклонено валидатором:</div><ul className="mt-1 space-y-1">{last.result.rejected!.map((reason, index) => <li key={index} className="font-mono text-[11px] text-warning">{reason}</li>)}</ul></div>}
        </div>}
      </div>}
      {records.length > 1 && <div className="space-y-1 text-[11px] text-muted-foreground">
        <div className="font-medium text-foreground">История запусков (Журнал задач AI — поэтапный лог в AI-центре)</div>
        {records.slice(1, 11).map((record) => <div key={record.id} className="flex flex-wrap gap-2">
          <span className="font-mono">{record.requested_at?.slice(0, 16).replace("T", " ")}</span>
          <Badge variant={record.status === "succeeded" ? "secondary" : record.status === "failed" ? "destructive" : "outline"} className="text-[9px]">{record.status}</Badge>
          <Badge variant="outline" className="text-[9px]">{record.mode}</Badge>
          <span>применено {record.applied_count}, отклонено {record.rejected_count}</span>
          {record.source === "auto" && <span className="text-[9px]">авто</span>}{record.source === "live" && <span className="text-[9px]">live</span>}
          <span className="truncate">{record.status === "failed" ? record.error_message : record.result?.situation}</span>
        </div>)}
      </div>}
    </CardContent></Card>
}

export function MonsterSpawnPage() {
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()
  const [server, setServer] = useState("ASPER1")
  const [draft, setDraft] = useState<MonsterSpawnState | null>(null)
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null)
  const [selectedMapCharacterId, setSelectedMapCharacterId] = useState<number | null>(null)
  const [selectedWave, setSelectedWave] = useState(0)
  const [placing, setPlacing] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [planSearch, setPlanSearch] = useState("")
  const [planStatus, setPlanStatus] = useState<PlanStatus>("all")
  const [showLegend, setShowLegend] = useState(true)
  const [contextMenu, setContextMenu] = useState<{ screenX: number; screenY: number; wx: number; wy: number; markerId: string | null } | null>(null)
  const [activeTab, setActiveTab] = useState("overview")
  const [selectedLearned, setSelectedLearned] = useState<string | null>(null)
  // 5: все правки — во всплывающих окнах.
  const [waveDialogIndex, setWaveDialogIndex] = useState<number | null>(null)
  const [planDialogOpen, setPlanDialogOpen] = useState(false)
  const [learnedDialogOpen, setLearnedDialogOpen] = useState(false)
  const [overrideDialogWave, setOverrideDialogWave] = useState<{ name: string; difficulty: number; alive: number; engaged: boolean } | null>(null)
  const [schemeDialog, setSchemeDialog] = useState<{ index: number; isNew?: boolean } | null>(null)

  // 5: обзор мира — активный раунд выбранного сервера берём напрямую с
  // серверной фильтрацией (самый свежий), без ручного сопоставления имён.
  const rounds = useQuery({ queryKey: ["world-rounds", server], queryFn: () => worldStateApi.listRounds(server), refetchInterval: 5000 })
  const round = rounds.data?.rounds[0] ?? null
  const geometry = useQuery({ queryKey: ["world-geometry", round?.round_id], queryFn: () => worldStateApi.getGeometry(round!.round_id), enabled: !!round, staleTime: Infinity })
  const world = useQuery({ queryKey: ["world-state", round?.round_id], queryFn: () => worldStateApi.getState(round!.round_id), enabled: !!round, refetchInterval: 3000 })
  const state = useQuery({ queryKey: ["monster-spawn", server], queryFn: () => monsterSpawnApi.get(server), refetchInterval: 5000 })
  const creaturesQuery = useQuery({ queryKey: ["monster-spawn-creatures"], queryFn: () => monsterSpawnApi.creatures(), staleTime: Infinity, refetchOnWindowFocus: false })
  const analysis = useQuery({ queryKey: ["monster-spawn-analysis", server], queryFn: () => monsterSpawnApi.analysis(server), refetchInterval: 15000, staleTime: 5000 })
  const reports = useQuery({ queryKey: ["monster-spawn-reports", server], queryFn: () => monsterSpawnApi.reports(server), refetchInterval: 15000 })
  const llmAnalysis = useQuery({ queryKey: ["monster-spawn-llm-runs", server], queryFn: () => monsterSpawnApi.llmRuns(server, 100), refetchInterval: 10000 })
  const latestRoundAnalysis = llmAnalysis.data?.runs.find((item) => item.source === "auto" && item.instruction?.startsWith("round-report:"))
  // История интенсивности за текущий раунд: точка на каждый свежий опрос
  // плагина (~5 c, окно ~6 мин). last_seen_at меняется только когда плагин
  // реально прислал телеметрию — по нему отсекаем дубли одного снапшота,
  // а не по round_seconds (время раунда может стоять, а давление меняться).
  const [intensityHistory, setIntensityHistory] = useState<Array<{ roundKey: string; seconds: number; intensity: number; threshold: number; seenAt: string }>>([])

  useEffect(() => { if (state.data && !dirty) setDraft(clone(state.data)) }, [state.data, dirty])
  useEffect(() => {
    const dashboard = state.data?.dashboard
    if (!dashboard?.round_active) {
      setIntensityHistory([])
      return
    }

    const point = {
      roundKey: `${server}:${dashboard.submarine_key ?? "unknown"}`,
      seconds: Number(dashboard.round_seconds) || 0,
      intensity: Number(dashboard.intensity) || 0,
      threshold: Number(dashboard.event_threshold) || 0,
      seenAt: state.data?.last_seen_at ?? "",
    }
    setIntensityHistory((previous) => {
      const last = previous[previous.length - 1]
      // Новый раунд/корабль или откат времени — начинаем историю заново.
      if (!last || last.roundKey !== point.roundKey || point.seconds < last.seconds - 5) return [point]
      // Тот же снапшот от плагина (опрос без свежих данных) — не плодим дубли.
      if (last.seenAt && point.seenAt && last.seenAt === point.seenAt) return previous
      if (last.seconds === point.seconds && last.intensity === point.intensity && last.threshold === point.threshold && last.seenAt === point.seenAt) return previous
      return [...previous, point].slice(-72)
    })
  }, [state.data?.dashboard, state.data?.last_seen_at, server])
  useEffect(() => { setDirty(false); setSelectedPlanId(null); setDraft(null) }, [server])
  useEffect(() => { const close = () => setContextMenu(null); const escape = (event: KeyboardEvent) => event.key === "Escape" && setContextMenu(null); window.addEventListener("click", close); window.addEventListener("keydown", escape); return () => { window.removeEventListener("click", close); window.removeEventListener("keydown", escape) } }, [])

  const save = useMutation({
    mutationFn: () => monsterSpawnApi.update(server, { config: draft!.config, plans: draft!.plans, memory: draft!.memory }),
    onSuccess: (next) => { setDraft(clone(next)); setDirty(false); queryClient.setQueryData(["monster-spawn", server], next); toast.success("Изменения отправлены плагину") },
    onError: (error: Error) => toast.error(error.message),
  })
  const updateDraft = (fn: (next: MonsterSpawnState) => void) => { if (!draft) return; const next = clone(draft); fn(next); setDraft(next); setDirty(true) }
  const filteredPlans = useMemo(() => (draft?.plans ?? []).filter((plan) => {
    if (planStatus !== "all" && plan.status !== planStatus) return false
    const query = planSearch.trim().toLowerCase()
    return !query || `${plan.wave_name} ${plan.name} ${plan.note ?? ""} ${plan.x},${plan.y} ${plan.status}`.toLowerCase().includes(query)
  }), [draft?.plans, planSearch, planStatus])

  if (!draft) return <div><PageHeader title="Управление монстрами" description="Загрузка состояния плагина" icon={Skull} /><div className="h-64 animate-pulse rounded-xl bg-muted" /></div>

  const dashboard = draft.dashboard ?? { round_active: false, round_seconds: 0, submarine_key: null, submarine_name: null, submarine: null, intensity: 0, event_threshold: 0.2, pending_waves: 0, known_submarines: 0 }
  const telemetry = isRecord(draft.telemetry) ? draft.telemetry : {}
  const activeWaves = telemetryList<ActiveWaveTelemetry>(telemetry.active_waves)
  const recentWaves = telemetryList<RecentWaveTelemetry>(telemetry.recent_waves)
  const learnedWaves = telemetryList<LearnedWaveTelemetry>(telemetry.learned_waves)
  const speciesThreats = telemetryList<SpeciesThreatTelemetry>(telemetry.species_threats)
  const speciesBaseline = telemetryList<SpeciesBaselineTelemetry>(telemetry.species_baseline)
  const creatures: CreatureInfo[] = creaturesQuery.data?.creatures ?? []
  const heldVanilla = telemetryList<HeldVanillaTelemetry>(telemetry.held_vanilla_events)
  const pendingWaveDetails = telemetryList<PendingWaveTelemetry>(telemetry.pending_wave_details)
  const plannedAction: PlannedActionTelemetry = isRecord(telemetry.planned_action)
    ? {
        action: String(telemetry.planned_action.action ?? "unknown"),
        probability: Number(telemetry.planned_action.probability ?? 0),
        detail: String(telemetry.planned_action.detail ?? ""),
        next_spawn_possible_at: Number(telemetry.planned_action.next_spawn_possible_at ?? 0),
        checks: isRecord(telemetry.planned_action.checks) ? telemetry.planned_action.checks as PlannedActionTelemetry["checks"] : undefined,
      }
    : { action: "unknown", probability: 0, detail: "Плагин ещё не прислал план действий", next_spawn_possible_at: 0 }
  const global = isRecord(draft.config.global) ? draft.config.global : {}
  const vanilla = isRecord(global.vanilla_events) ? global.vanilla_events : {}
  const speciesHpOverrides: Record<string, number> = isRecord(global.species_hp_overrides) ? global.species_hp_overrides as Record<string, number> : {}
  const speciesBaseHpOverrides: Record<string, number> = isRecord(global.species_base_hp_overrides) ? global.species_base_hp_overrides as Record<string, number> : {}
  const setSpeciesHp = (species: string, value: number | null) => updateDraft((next) => {
    const current = isRecord(next.config.global.species_hp_overrides) ? { ...(next.config.global.species_hp_overrides as Record<string, number>) } : {}
    if (value == null || !(value > 0)) delete current[species]
    else current[species] = value
    next.config.global.species_hp_overrides = current
  })
  const setSpeciesBaseHp = (species: string, value: number | null) => updateDraft((next) => {
    const current = isRecord(next.config.global.species_base_hp_overrides) ? { ...(next.config.global.species_base_hp_overrides as Record<string, number>) } : {}
    if (value == null || !(value > 0)) delete current[species]
    else current[species] = value
    next.config.global.species_base_hp_overrides = current
  })
  // 6: схемы позиционирования спавна из конфига.
  const schemeList: SpawnScheme[] = Array.isArray(global.spawn_schemes) ? global.spawn_schemes as SpawnScheme[] : []
  const report: MonsterSpawnAnalysis = analysis.data ?? { generated_at: "", read_only: true, global_performance_ema: 1, wave_logs: [], ship_logs: [], recommendations: [{ id: "loading", severity: "info", title: "Собираю рекомендации", reason: "Анализ памяти контроллера выполняется автоматически.", evidence: {}, suggested_adjustment: "Не менять настройки до загрузки анализа.", confidence: 0, read_only: true }] }
  const intensityPercent = Math.max(0, Math.min(100, dashboard.intensity * 100))
  const thresholdPercent = Math.max(0, Math.min(100, dashboard.event_threshold * 100))
  const waveLogs = report.wave_logs
  const shipLogs = report.ship_logs
  const selectedPlan = draft.plans.find((plan) => plan.id === selectedPlanId) ?? null
  const markers: WorldMarker[] = draft.plans.map((plan) => ({ id: plan.id, x: plan.x, y: plan.y, label: plan.wave_name || plan.name, color: plan.source === "manual" ? "#59D6C2" : "#F6C453", muted: plan.status === "cancelled", draggable: true }))
  const wavePalette = ["#F6C453", "#E879F9", "#60A5FA", "#FB7185", "#34D399", "#A78BFA", "#F97316"]
  const waveMapRows = telemetryList<Record<string, unknown>>(telemetry.map_waves)
  const characterColors: Record<number, string> = {}
  const waveMarkers: WorldMarker[] = waveMapRows.map((wave, index) => {
    const waveId = String(wave.wave_id ?? index)
    const color = wavePalette[[...waveId].reduce((sum, character) => sum + character.charCodeAt(0), 0) % wavePalette.length]
    const counts = isRecord(wave.monster_counts) ? Object.entries(wave.monster_counts).map(([species, count]) => `${species} ×${count}`).join(", ") : "Состав неизвестен"
    if (Array.isArray(wave.monster_ids)) for (const id of wave.monster_ids) characterColors[Number(id)] = color
    return { id: `wave:${waveId}`, x: Number(wave.x), y: Number(wave.y), label: String(wave.wave_name ?? "Волна"), color,
      detail: `${wave.observational ? "Ванильная волна (наблюдение)" : "Волна контроллера"}\nПричина: ${String(wave.spawn_reason ?? "—")}\nСущества: ${counts}${wave.outcome ? `\nИсход: ${wave.outcome}` : ""}` }
  }).filter((marker) => Number.isFinite(marker.x) && Number.isFinite(marker.y))
  const plannedMarkers: WorldMarker[] = [
    ...draft.plans.filter((plan) => plan.status === "planned").map((plan) => ({ id: `planned:${plan.id}`, x: plan.x, y: plan.y, label: plan.wave_name || plan.name, color: "#59D6C2", pulse: true, detail: `Запланирована вручную\n${plan.monsters.map((monster) => `${monster.species} ×${monster.count}`).join(", ") || "Состав пока не задан"}` })),
    ...pendingWaveDetails.filter((wave) => wave.x != null && wave.y != null && Number.isFinite(Number(wave.x)) && Number.isFinite(Number(wave.y))).map((wave, index) => ({ id: `pending:${index}`, x: Number(wave.x), y: Number(wave.y), label: wave.wave_name ?? "Ожидаемая волна", color: "#F6C453", pulse: true, detail: `Ожидает спавна\n${wave.wave_name ?? "Волна"}` })),
  ]
  const roundMapMarkers = [...waveMarkers, ...plannedMarkers]
  const waveChart = { labels: waveLogs.slice(0, 8).map((item) => item.name.replace("vanilla:", "")), datasets: [{ label: "Среднее время зачистки, с", data: waveLogs.slice(0, 8).map((item) => Math.round(item.avg_repel_seconds)), backgroundColor: CHART_COLORS[0], ...barDatasetDefaults }] }
  const shipChart = { labels: shipLogs.slice(0, 8).map((item) => item.name), datasets: [{ label: "Performance EMA", data: shipLogs.slice(0, 8).map((item) => item.performance_ema), backgroundColor: CHART_COLORS[1], ...barDatasetDefaults }] }
  const trendValues = shipLogs.flatMap((item) => item.recent_performance).slice(-24)
  const trendChart = { labels: trendValues.map((_, index) => `${index + 1}`), datasets: [{ label: "Последние наблюдения", data: trendValues, borderColor: CHART_COLORS[2], backgroundColor: `${CHART_COLORS[2]}28`, fill: true, ...lineDatasetDefaults }] }
  // Одиночная точка на категориальной оси прижимается к краю и с
  // pointRadius: 0 вообще не видна — подсвечиваем её, пока история короткая.
  const intensityPointRadius = intensityHistory.length < 3 ? 3 : 0
  const intensityChart = {
    labels: intensityHistory.map((point) => `${Math.floor(point.seconds / 60)}:${String(Math.floor(point.seconds % 60)).padStart(2, "0")}`),
    datasets: [
      { label: "Интенсивность", data: intensityHistory.map((point) => point.intensity), borderColor: CHART_COLORS[0], backgroundColor: `${CHART_COLORS[0]}28`, fill: true, ...lineDatasetDefaults, pointRadius: intensityPointRadius },
      { label: "Порог события", data: intensityHistory.map((point) => point.threshold), borderColor: CHART_COLORS[3], ...lineDatasetDefaults, pointRadius: intensityPointRadius },
    ],
  }
  const intensityChartOptions = {
    ...baseChartOptions,
    scales: {
      ...baseChartOptions.scales,
      x: {
        ...baseChartOptions.scales.x,
        ticks: { ...baseChartOptions.scales.x.ticks, maxTicksLimit: 8, maxRotation: 0 },
      },
    },
  }

  const setGlobal = (key: string, value: unknown) => updateDraft((next) => { next.config.global[key] = value })
  const setVanilla = (key: string, value: unknown) => updateDraft((next) => { const current = isRecord(next.config.global.vanilla_events) ? next.config.global.vanilla_events : {}; next.config.global.vanilla_events = { ...current, [key]: value } })
  // 3.1/3.5: списки исключений (подстроки в названиях волн).
  const ignoreList = (key: string): string[] => Array.isArray(global[key]) ? (global[key] as string[]) : []
  const createPlan = (x: number, y: number) => updateDraft((next) => { const wave = clone(next.config.waves[selectedWave] ?? emptyWave()); const plan: SpawnPlan = { ...wave, id: crypto.randomUUID(), wave_name: wave.name, source: "manual", status: "planned", x, y, note: "" }; next.plans.push(plan); setSelectedPlanId(plan.id); setPlacing(false) })
  const duplicatePlan = (id: string) => updateDraft((next) => { const source = next.plans.find((plan) => plan.id === id); if (!source) return; const duplicate: SpawnPlan = { ...clone(source), id: crypto.randomUUID(), note: source.note ? `${source.note} (копия)` : "" }; next.plans.push(duplicate); setSelectedPlanId(duplicate.id); toast.success("План продублирован") })
  const copyCoords = (x: number, y: number) => navigator.clipboard.writeText(`${x}, ${y}`).then(() => toast.success(`Координаты ${x}, ${y} скопированы`)).catch(() => toast.error("Не удалось скопировать"))

  // 1: постоянные пометки типов волн хранятся в memory.wave_overrides —
  // плагин применяет их к активным, конфиг- и изученным волнам. Ключи
  // сопоставляются без учёта регистра (названия ванильных волн бывают
  // в разном регистре, например "vanilla:AbyssCreature").
  const findOverrideKey = (overrides: Record<string, unknown>, name: string): string | null => {
    const lower = name.toLowerCase()
    for (const key of Object.keys(overrides)) {
      if (key.toLowerCase() === lower) return key
    }
    return null
  }
  const getWaveOverride = (name?: string | null): WaveOverride | undefined => {
    if (!draft || !name) return undefined
    const memory = isRecord(draft.memory) ? draft.memory : {}
    const overrides = isRecord(memory.wave_overrides) ? memory.wave_overrides : {}
    const key = findOverrideKey(overrides, name)
    const entry = key && isRecord(overrides[key]) ? overrides[key] : null
    if (!entry) return undefined
    return {
      difficulty_mode: typeof entry.difficulty_mode === "string" ? entry.difficulty_mode : "adjust",
      difficulty_value: typeof entry.difficulty_value === "number" ? entry.difficulty_value : 1,
      non_blocking: typeof entry.non_blocking === "boolean" ? entry.non_blocking : null,
      enabled: entry.enabled !== false,
      updated_utc: typeof entry.updated_utc === "string" ? entry.updated_utc : undefined,
      danger_mode: typeof entry.danger_mode === "string" ? entry.danger_mode : "adjust",
      danger_value: typeof entry.danger_value === "number" ? entry.danger_value : null,
    }
  }
  const setWaveOverride = (name: string, patch: Partial<WaveOverride>) => updateDraft((next) => {
    const memory = next.memory as Record<string, unknown>
    const overrides = isRecord(memory.wave_overrides) ? { ...memory.wave_overrides } : {}
    const existingKey = findOverrideKey(overrides, name)
    const existing = existingKey && isRecord(overrides[existingKey]) ? overrides[existingKey] : {}
    if (existingKey && existingKey !== name) delete overrides[existingKey]
    overrides[name] = { ...existing, ...patch, updated_utc: new Date().toISOString() }
    memory.wave_overrides = overrides
  })
  // 6: схемы позиционирования спавна.
  const updateSchemes = (fn: (schemes: SpawnScheme[]) => void) => updateDraft((next) => {
    const globalConfig = next.config.global as Record<string, unknown>
    const schemes = Array.isArray(globalConfig.spawn_schemes) ? clone(globalConfig.spawn_schemes as SpawnScheme[]) : []
    fn(schemes)
    globalConfig.spawn_schemes = schemes
  })
  // 2: убрать постоянную пометку типа волны целиком.
  const removeWaveOverride = (name: string) => updateDraft((next) => {
    const memory = next.memory as Record<string, unknown>
    const overrides = isRecord(memory.wave_overrides) ? { ...memory.wave_overrides } : {}
    for (const key of Object.keys(overrides)) {
      if (key.toLowerCase() === name.toLowerCase()) delete overrides[key]
    }
    memory.wave_overrides = overrides
  })
  // Ручные коррекции опасности видов: memory.species_danger_overrides —
  // фиксированная угроза вместо EMA (плагин не обновляет EMA для видов
  // с включённой ручной коррекцией).
  const getSpeciesDangerOverride = (species: string): { threat_value: number; enabled: boolean } | undefined => {
    if (!draft) return undefined
    const memory = isRecord(draft.memory) ? draft.memory : {}
    const overrides = isRecord(memory.species_danger_overrides) ? memory.species_danger_overrides : {}
    const key = findOverrideKey(overrides, species)
    const entry = key && isRecord(overrides[key]) ? overrides[key] : null
    if (!entry || typeof entry.threat_value !== "number") return undefined
    return { threat_value: entry.threat_value, enabled: entry.enabled !== false }
  }
  const setSpeciesDangerOverride = (species: string, threatValue: number | null, enabled = true) => updateDraft((next) => {
    const memory = next.memory as Record<string, unknown>
    const overrides = isRecord(memory.species_danger_overrides) ? { ...memory.species_danger_overrides } : {}
    const existingKey = findOverrideKey(overrides, species)
    if (threatValue == null || !Number.isFinite(threatValue)) {
      if (existingKey) delete overrides[existingKey]
    } else {
      const targetKey = existingKey ?? species
      if (existingKey && existingKey !== species) delete overrides[existingKey]
      overrides[targetKey] = { threat_value: Math.max(0.3, Math.min(2.5, threatValue)), enabled, updated_utc: new Date().toISOString() }
    }
    memory.species_danger_overrides = overrides
  })
  // 1: авто-признак «неблокирующая» по названию (на клиенте — для бейджей).
  const autoNonBlockByName = (name?: string | null) => !!name && /abyss|moloch|cave/i.test(name)
  // 1: шаблон изученной волны и его правка в памяти.
  const learnedTemplateFor = (name: string | null): SpawnWave | null => {
    if (!draft || !name) return null
    const memoryRecord = isRecord(draft.memory) ? draft.memory : {}
    const learnedMap = isRecord(memoryRecord.learned_waves) ? memoryRecord.learned_waves : {}
    const entry = isRecord(learnedMap[name]) ? learnedMap[name] : null
    if (!entry || !isRecord(entry.wave)) return null
    const template = clone(entry.wave) as unknown as SpawnWave
    // spawn_type в нижний регистр ("Cave"/"AbyssCave" из PosType.ToString()).
    if (typeof template.spawn_type === "string") template.spawn_type = template.spawn_type.trim().toLowerCase()
    return template
  }
  const updateLearnedTemplate = (name: string, fn: (template: SpawnWave) => void) => updateDraft((next) => {
    const memory = next.memory as Record<string, unknown>
    const learned = isRecord(memory.learned_waves) ? { ...memory.learned_waves } : {}
    const item = isRecord(learned[name]) ? { ...learned[name] } : {}
    const template = isRecord(item.wave) ? clone(item.wave) as unknown as SpawnWave : null
    if (!template) return
    fn(template)
    item.wave = template
    learned[name] = item
    memory.learned_waves = learned
  })
  const scaleLearnedTemplate = (name: string, field: "count" | "hp_multiplier", factor: number) => updateLearnedTemplate(name, (template) => {
    template.monsters = (template.monsters ?? []).map((m) => ({ ...m, [field]: Math.round(m[field] * factor * 100) / 100 }))
    if (field === "hp_multiplier" && template.difficulty > 0) template.difficulty = Math.round(template.difficulty * factor * 100) / 100
  })

  return <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-4">
    <section className="relative isolate overflow-hidden rounded-2xl border bg-gradient-to-br from-primary/[0.09] via-card to-card p-4 shadow-sm sm:p-6">
      <div aria-hidden="true" className="pointer-events-none absolute -right-12 -top-24 -z-10 size-72 rounded-full bg-primary/[0.08] blur-3xl" />
      <div className="flex flex-wrap items-center justify-between gap-5">
        <div className="flex min-w-0 items-start gap-4">
          <div className="relative mt-0.5 flex size-12 shrink-0 items-center justify-center rounded-2xl border border-primary/20 bg-primary/10 text-primary shadow-inner sm:size-14"><Skull className="size-6 sm:size-7" /></div>
          <div className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-2"><span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">Nexus · Контроль угроз</span><Badge variant="outline" className="font-mono text-[10px]">{server}</Badge></div>
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Управление монстрами</h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">Наблюдение за волнами и настройка MonsterSpawnController в реальном времени.</p>
          </div>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <Select value={server} onValueChange={setServer}><SelectTrigger className="min-w-32 flex-1 bg-background/80 sm:flex-none"><SelectValue /></SelectTrigger><SelectContent>{["ASPER1", "ASPER2"].map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select>
          <Button className="flex-1 sm:flex-none" disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save className="size-4" /> {save.isPending ? "Сохранение…" : "Сохранить"}</Button>
        </div>
      </div>
    </section>
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border bg-muted/20 px-3 py-2.5 text-xs sm:px-4">
      <div className="flex items-center gap-2"><span className={`size-2 rounded-full ${dashboard.round_active ? "animate-pulse bg-success" : "bg-muted-foreground/50"}`} /><span className="font-medium">{dashboard.round_active ? "Раунд активен" : "Ожидание раунда"}</span></div>
      <span className="hidden h-4 w-px bg-border sm:block" />
      <Badge variant={draft.pending ? "destructive" : "secondary"}>{draft.pending ? `Ожидает применения r${draft.desired_revision}` : `Применено r${draft.applied_revision}`}</Badge>
      {dashboard.round_active && <span className="text-muted-foreground">Волны: <b className="text-foreground">{dashboard.tracked_wave_count ?? dashboard.blocking_wave_count ?? 0}</b> отслеживаются · <b className="text-foreground">{dashboard.blocking_wave_count ?? 0}</b> в бою / лимит {dashboard.max_simultaneous_waves ?? 1}</span>}
      {dashboard.last_spawn_scheme && <Badge variant="outline" className="text-[10px]">Схема: {dashboard.last_spawn_scheme}{dashboard.last_spawn_zone ? ` · ${dashboard.last_spawn_zone}` : ""}</Badge>}
      <span className="text-muted-foreground">Pipeline: <b className="text-foreground">{String(vanilla.mode ?? "controller")}</b></span>
      {dirty && <Badge variant="outline" className="border-warning/40 text-warning">Есть несохранённые изменения</Badge>}
      <span className="ml-auto text-muted-foreground">Контакт: {draft.last_seen_at ? formatBrowserDate(draft.last_seen_at) : "нет данных"}</span>
    </div>

    <Tabs value={activeTab} onValueChange={setActiveTab} className="min-h-0">{activeTab === "logs" && <RoundReports reports={reports.data?.reports ?? []} server={server} />}
      <TabsList className="flex h-auto w-full gap-1 overflow-x-auto rounded-xl border bg-muted/40 p-1.5"><TabsTrigger value="overview" className="shrink-0 gap-1.5 rounded-lg px-3 py-2 text-xs sm:gap-2"><Activity className="size-4 shrink-0" /> Обзор</TabsTrigger><TabsTrigger value="monitoring" className="shrink-0 gap-1.5 rounded-lg px-3 py-2 text-xs sm:gap-2"><Radar className="size-4 shrink-0" /> Мониторинг</TabsTrigger><TabsTrigger value="llm" className="shrink-0 gap-1.5 rounded-lg px-3 py-2 text-xs sm:gap-2"><Bot className="size-4 shrink-0" /> LLM-ассистент</TabsTrigger><TabsTrigger value="control" className="shrink-0 gap-1.5 rounded-lg px-3 py-2 text-xs sm:gap-2"><SlidersHorizontal className="size-4 shrink-0" /> Настройки</TabsTrigger><TabsTrigger value="schemes" className="shrink-0 gap-1.5 rounded-lg px-3 py-2 text-xs sm:gap-2"><Layers className="size-4 shrink-0" /> Схемы спавна</TabsTrigger><TabsTrigger value="logs" className="shrink-0 gap-1.5 rounded-lg px-3 py-2 text-xs sm:gap-2"><TrendingUp className="size-4 shrink-0" /> Логи и анализ</TabsTrigger><TabsTrigger value="manual" className="shrink-0 gap-1.5 rounded-lg px-3 py-2 text-xs sm:gap-2"><Target className="size-4 shrink-0" /> Ручная настройка</TabsTrigger></TabsList>

      <TabsContent value="overview" className="space-y-4 pt-2">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric icon={ShipWheel} label="Корабль раунда" value={dashboard.submarine_name || "Нет активного раунда"} detail={`${Math.floor((dashboard.round_seconds || 0) / 60)} мин · ${dashboard.known_submarines} кораблей в памяти`} tone="primary" /><Metric icon={Gauge} label="Интенсивность" value={`${intensityPercent.toFixed(0)}%`} detail={`Порог vanilla event ${thresholdPercent.toFixed(0)}%`} tone="warning" /><Metric icon={BrainCircuit} label="Global performance EMA" value={report.global_performance_ema.toFixed(2)} detail="Больше 1.0: экипаж справляется быстрее ожиданий" tone="success" /><Metric icon={Anchor} label="Ожидают триггера" value={String(dashboard.pending_waves)} detail={`${telemetry.vanilla_events ?? 0} vanilla events · ${draft.config.waves.length} волн в пуле`} tone="info" /></div>
         <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,.65fr)]"><ChartCard title="Наблюдаемая интенсивность" description="История интенсивности и порога за текущий раунд">{intensityHistory.length ? <Line options={intensityChartOptions} data={intensityChart} /> : <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">{dashboard.round_active ? "Собираю историю — первые точки появятся через несколько секунд" : "Нет активного раунда — история начнёт собираться после старта раунда"}</div>}</ChartCard>
          <Card className="overflow-hidden py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="flex items-center gap-2 text-sm"><Radar className="size-4 text-destructive" /> Волны под атакой и недавние</CardTitle><CardDescription className="text-xs">Живые волны, их статусы и исходы последних столкновений</CardDescription></CardHeader><CardContent className="max-h-96 space-y-2 overflow-y-auto p-4">
            {activeWaves.length > 0 ? activeWaves.map((wave) => <ActiveWaveCard key={wave.wave_id} wave={wave} override={getWaveOverride(wave.wave_name)} onToggleNonBlocking={() => setWaveOverride(wave.wave_name, { non_blocking: !(getWaveOverride(wave.wave_name)?.non_blocking === true) })} onOpenOverride={() => setOverrideDialogWave({ name: wave.wave_name, difficulty: wave.effective_difficulty, alive: wave.monsters_alive, engaged: wave.in_combat_now ?? wave.engaged === true })} />) : <div className="rounded-xl border border-dashed p-4 text-center text-xs text-muted-foreground">Активных волн нет — корабль вне боя</div>}
            {recentWaves.slice(0, 4).map((wave) => <RecentWaveCard key={wave.wave_id + String(wave.resolved_at_seconds)} wave={wave} />)}
            {!activeWaves.length && !recentWaves.length && <Button variant="ghost" className="w-full justify-between" onClick={() => setActiveTab("logs")}>Открыть полный анализ <ChevronRight className="size-4" /></Button>}
          </CardContent></Card></div>
        <div className="grid gap-4 lg:grid-cols-2"><ChartCard title="Самые долгие волны" description="Среднее время от спавна до разрешения"><Bar options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, legend: { display: false } } }} data={waveChart} /></ChartCard><ChartCard title="Корабли по эффективности" description="Ниже 1.0 означает более тяжёлое прохождение"><Bar options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, legend: { display: false } } }} data={shipChart} /></ChartCard></div>
        <Card className="overflow-hidden py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="flex items-center gap-2 text-sm"><MapPin className="size-4 text-primary" /> Карта раунда</CardTitle><CardDescription className="text-xs">Цвет точки волны совпадает с цветом её существ. Мигающие метки — планируемый спавн. Нажмите на метку или существо для подробностей.</CardDescription></CardHeader><CardContent className="p-4"><div className="relative h-[380px] overflow-hidden rounded-xl border">
          {rounds.isError ? <MapPlaceholder>Не удалось загрузить активные раунды{rounds.error instanceof Error ? `: ${rounds.error.message}` : ""}</MapPlaceholder>
            : !round ? <MapPlaceholder>Для {server} сейчас нет активного раунда — мир не транслируется</MapPlaceholder>
              : geometry.isError || world.isError ? <MapPlaceholder>Ошибка загрузки карты: {((geometry.error ?? world.error) as Error | null)?.message ?? "нет данных"}</MapPlaceholder>
                 : geometry.data && world.data ? <WorldCanvas geometry={geometry.data} state={world.data} selectedCharacterId={selectedMapCharacterId} onSelectCharacter={setSelectedMapCharacterId} targetMode={false} markers={roundMapMarkers} selectedMarkerId={selectedPlanId} onSelectMarker={setSelectedPlanId} characterColors={characterColors} startZone={geometry.data.start_x != null && geometry.data.end_x != null ? { startX: geometry.data.start_x, endX: geometry.data.end_x, progress: Number(global.spawn_start_zone_progress ?? 0.1), enabled: global.spawn_start_zone_enabled !== false, left: telemetry.spawn_start_zone_left === true } : null} abyssStartY={geometry.data.abyss_start_y ?? null} />
                  : <MapPlaceholder>Загрузка карты раунда…</MapPlaceholder>}
        </div></CardContent></Card>

      </TabsContent>

      <TabsContent value="monitoring" className="space-y-4 pt-2">
        <PlannedActionCard planned={plannedAction} />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <Metric icon={MapPin} label="Прохождение" value={`${Number(telemetry.progress_percent ?? 0).toFixed(0)}%`} detail="уровня пройдено кораблём" tone="primary" />
          <Metric icon={TrendingUp} label="Темп продвижения" value={`${Number(telemetry.pace_ratio ?? 1).toFixed(2)}x`} detail={`${telemetry.pace_anomalous ? "Аномальный — перехват возможен" : "Нормальный"} · цель ${Number(telemetry.pace_target_progress_per_minute ?? 0).toFixed(1)}%/мин (${String(telemetry.pace_target_source ?? "current_round")})`} tone={telemetry.pace_anomalous ? "warning" : "success"} />
          <Metric icon={SlidersHorizontal} label="Лимит волн" value={`${Number(telemetry.tracked_wave_count ?? 0)}/${Number(telemetry.max_simultaneous_waves ?? 1)}`} detail={`отслеживаются · в бою ${Number(telemetry.blocking_wave_count ?? 0)} · cooldown ${Number(telemetry.struggle_delay_factor ?? 1).toFixed(2)}x`} tone={Number(telemetry.tracked_wave_count ?? 0) >= Number(telemetry.max_simultaneous_waves ?? 1) ? "warning" : "primary"} />
          <Metric icon={BrainCircuit} label="Изучено волн" value={String(telemetry.learned_waves_count ?? 0)} detail={`${telemetry.pace_intercepts_this_round ?? 0}/${telemetry.pace_intercept_max ?? 0} перехватов за раунд`} tone="info" />
          <Metric icon={Radar} label="Ванильные события" value={String(telemetry.vanilla_events ?? 0)} detail={`${telemetry.pending_waves ?? 0} волн ждут триггера · режим ${telemetry.vanilla_event_mode ?? "controller"}`} tone="primary" />
        </div>
        <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Активные волны</CardTitle><CardDescription className="text-xs">Правка сложности и пометки — во всплывающем окне, применяются сразу после сохранения</CardDescription></CardHeader><CardContent className="space-y-2 p-4">
          {activeWaves.length ? activeWaves.map((wave) => <ActiveWaveCard key={wave.wave_id} wave={wave} override={getWaveOverride(wave.wave_name)} onToggleNonBlocking={() => setWaveOverride(wave.wave_name, { non_blocking: !(getWaveOverride(wave.wave_name)?.non_blocking === true) })} onOpenOverride={() => setOverrideDialogWave({ name: wave.wave_name, difficulty: wave.effective_difficulty, alive: wave.monsters_alive, engaged: wave.in_combat_now ?? wave.engaged === true })} />) : <div className="rounded-xl border border-dashed p-6 text-center text-xs text-muted-foreground">Нет активных волн</div>}
        </CardContent></Card>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Изученные ванильные волны</CardTitle><CardDescription className="text-xs">Клик по волне — всплывающее окно с правкой шаблона и пометок (сохраняется в памяти)</CardDescription></CardHeader><CardContent className="p-4"><div className="overflow-x-auto rounded-xl border"><table className="w-full min-w-[520px] text-left text-xs"><thead className="bg-muted/30 text-muted-foreground"><tr><th className="px-3 py-2 font-medium">Волна</th><th className="px-3 py-2 font-medium">Вид</th><th className="px-3 py-2 font-medium">Наблюдений</th><th className="px-3 py-2 font-medium">В пуле</th><th className="w-8 px-2" /></tr></thead><tbody>{learnedWaves.map((wave) => <tr key={wave.wave_name} className="cursor-pointer border-t transition hover:bg-muted/40" onClick={() => { setSelectedLearned(wave.wave_name); setLearnedDialogOpen(true) }}><td className="px-3 py-2 font-mono">{wave.wave_name}{wave.non_blocking && <Badge variant="outline" className="ml-2 text-[9px] text-muted-foreground">неблок.</Badge>}</td><td className="px-3 py-2">{wave.species}</td><td className="px-3 py-2">{wave.times_seen}</td><td className="px-3 py-2">{wave.registered_in_pool ? <Check className="size-3.5 text-success" /> : <X className="size-3.5 text-muted-foreground" />}</td><td className="px-2 py-2"><Button size="sm" variant="ghost" className="size-7 p-0" onClick={(event) => { event.stopPropagation(); setSelectedLearned(wave.wave_name); setLearnedDialogOpen(true) }}><Pencil className="size-3.5" /></Button></td></tr>)}{!learnedWaves.length && <tr><td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">Пока ничего не изучено</td></tr>}</tbody></table></div></CardContent></Card>
          <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Оценка угрозы видов</CardTitle><CardDescription className="text-xs">&gt;1.0 — вид опасен для экипажа, частота спавна повышается. Ручная угроза фиксирует значение вместо EMA.</CardDescription></CardHeader><CardContent className="p-4"><div className="overflow-x-auto rounded-xl border"><table className="w-full min-w-[520px] text-left text-xs"><thead className="bg-muted/30 text-muted-foreground"><tr><th className="px-3 py-2 font-medium">Вид</th><th className="px-3 py-2 font-medium">Угроза (EMA)</th><th className="px-3 py-2 font-medium">Итог</th><th className="px-3 py-2 font-medium">Наблюдений</th><th className="px-3 py-2 font-medium">Ручная угроза</th></tr></thead><tbody>{speciesThreats.map((threat) => {
            const manual = getSpeciesDangerOverride(threat.species)
            const effective = threat.effective_threat ?? threat.threat_ema
            return <tr key={threat.species} className="border-t"><td className="px-3 py-2 font-medium">{threat.species}{manual && <Badge variant="outline" className="ml-2 text-[9px] text-warning">ручная</Badge>}</td><td className={`px-3 py-2 font-mono ${threat.threat_ema >= 1.2 ? "text-warning" : threat.threat_ema <= 0.8 ? "text-success" : ""}`}>{threat.threat_ema.toFixed(2)}</td><td className="px-3 py-2 font-mono text-primary">{Number(effective).toFixed(2)}</td><td className="px-3 py-2">{threat.samples}</td><td className="px-3 py-2"><div className="flex items-center gap-1"><Input type="number" step="0.05" className="h-7 w-20 text-xs" placeholder="авто" value={manual ? String(manual.threat_value) : ""} onChange={(event) => { const raw = event.target.value.trim(); setSpeciesDangerOverride(threat.species, raw === "" ? null : number(raw)) }} /><Button size="sm" variant="ghost" className="size-7 p-0" title="Убрать ручную угрозу" onClick={() => setSpeciesDangerOverride(threat.species, null)}><X className="size-3.5" /></Button></div></td></tr>
          })}{!speciesThreats.length && <tr><td colSpan={5} className="px-3 py-8 text-center text-muted-foreground">Данных ещё нет</td></tr>}</tbody></table></div></CardContent></Card>
        </div>
        <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Удержанные ванильные спавны и волны в ожидании</CardTitle><CardDescription className="text-xs">Пойманные события держатся hold_seconds перед спавном — идёт корректировка</CardDescription></CardHeader><CardContent className="space-y-2 p-4">
          {heldVanilla.length ? heldVanilla.map((event, index) => <div key={index} className="rounded-xl border bg-muted/10 p-3 text-xs"><div className="flex flex-wrap items-center gap-2"><span className="font-semibold">{event.wave_name}</span><span className="text-muted-foreground">{event.species} x{event.original_amount}</span><Badge variant="outline" className="text-[10px]">осталось {Math.round(event.hold_remaining_seconds ?? 0)}с</Badge></div></div>) : <div className="rounded-xl border border-dashed p-4 text-center text-xs text-muted-foreground">Ничего не удерживается</div>}
          {pendingWaveDetails.length > 0 && <div className="space-y-1 pt-1">{pendingWaveDetails.map((wave, index) => <div key={index} className="flex flex-wrap items-center gap-2 rounded-lg bg-muted/20 px-3 py-2 text-[11px]"><Anchor className="size-3.5" /><span className="font-medium">{wave.wave_name}</span><Badge variant="secondary" className="text-[9px]">{REASON_LABELS[wave.spawn_reason ?? ""] ?? wave.spawn_reason}</Badge><span className="text-muted-foreground">{wave.pos_type} · ждёт {Math.round(wave.waiting_seconds ?? 0)}с</span></div>)}</div>}
        </CardContent></Card>
        <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Недавно завершённые волны</CardTitle><CardDescription className="text-xs">Исход различает бой (repelled) и обход без боя (bypassed)</CardDescription></CardHeader><CardContent className="space-y-2 p-4">
          {recentWaves.length ? recentWaves.map((wave) => <RecentWaveCard key={wave.wave_id + String(wave.resolved_at_seconds)} wave={wave} />) : <div className="rounded-xl border border-dashed p-6 text-center text-xs text-muted-foreground">В этом раунде волны ещё не завершались</div>}
        </CardContent></Card>
      </TabsContent>

      <TabsContent value="llm" className="space-y-4 pt-2">
        <LLMPanel server={server} reports={reports.data?.reports ?? []} learning={vanilla.mode === "learning"} />
      </TabsContent>

      <TabsContent value="control" className="space-y-4 pt-2">
        <ExpressTuning global={global} vanilla={vanilla} setGlobal={setGlobal} setVanilla={setVanilla} />
        <SpeciesHpCard creatures={creatures} baseline={speciesBaseline} overrides={speciesHpOverrides} baseOverrides={speciesBaseHpOverrides} setBaseHp={setSpeciesBaseHp} minMult={Number(global.min_monster_health_multiplier ?? 0)} minFixed={Number(global.min_monster_health_fixed ?? 0)} setSpeciesHp={setSpeciesHp} setMinMult={(value) => setGlobal("min_monster_health_multiplier", value)} setMinFixed={(value) => setGlobal("min_monster_health_fixed", value)} />
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]"><Card className="overflow-hidden py-0"><CardHeader className="border-b px-4 py-3"><CardTitle className="text-sm">Адаптация ванильных событий</CardTitle><CardDescription className="text-xs">Насколько история эффективности влияет на здоровье и количество монстров.</CardDescription></CardHeader><CardContent className="grid gap-3 p-4 sm:grid-cols-2"><SliderField label="Влияние памяти на HP" value={Number(vanilla.adaptive_influence ?? 1)} min={0} max={1} step={0.05} onChange={(value) => setVanilla("adaptive_influence", value)} /><SliderField label="Влияние памяти на количество" value={Number(vanilla.adaptive_amount_influence ?? 0)} min={0} max={1} step={0.05} onChange={(value) => setVanilla("adaptive_amount_influence", value)} /></CardContent></Card>
          <div className="space-y-4"><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Режим vanilla pipeline</CardTitle><CardDescription className="text-xs">События выбирает игра, фактический спавн контролирует режим.</CardDescription></CardHeader><CardContent className="space-y-3"><Select value={String(vanilla.mode ?? "controller")} onValueChange={(value) => setVanilla("mode", value)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="controller">controller · анализ и корреляция</SelectItem><SelectItem value="learning">обучение · только наблюдение</SelectItem><SelectItem value="native">native · штатный спавн</SelectItem><SelectItem value="disabled">disabled · заблокировать</SelectItem></SelectContent></Select><div className="rounded-lg bg-muted/30 p-3 text-xs text-muted-foreground"><div className="mb-1 flex items-center gap-2 text-foreground"><Info className="size-3.5 text-info" /> Безопасный принцип</div>Анализатор не меняет параметры сам. Все предложения остаются на этой странице до ручного подтверждения оператором.</div></CardContent></Card><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Предохранители</CardTitle></CardHeader><CardContent className="space-y-3"><div className="flex items-center justify-between gap-3 text-xs"><span>Intensity gate</span><Switch checked={global.intensity_gate_enabled !== false} onCheckedChange={(value) => setGlobal("intensity_gate_enabled", value)} /></div><div className="flex items-center justify-between gap-3 text-xs"><span>Adaptive-memory</span><Switch checked={global.adaptive_memory_enabled !== false} onCheckedChange={(value) => setGlobal("adaptive_memory_enabled", value)} /></div><div className="flex items-center justify-between gap-3 text-xs"><span>Уважать server monster settings</span><Switch checked={global.respect_server_monster_settings !== false} onCheckedChange={(value) => setGlobal("respect_server_monster_settings", value)} /></div><div className="flex items-center justify-between gap-3 text-xs"><span>Swarm behavior</span><Switch checked={global.use_swarm_behavior !== false} onCheckedChange={(value) => setGlobal("use_swarm_behavior", value)} /></div></CardContent></Card></div></div>
        <Card><CardHeader className="pb-3"><CardTitle className="text-sm">Как читать регуляторы</CardTitle></CardHeader><CardContent className="grid gap-3 text-xs text-muted-foreground md:grid-cols-3"><div><b className="text-foreground">Amount</b><br />Масштабирует ванильное количество. Значение 1.0 сохраняет исходное поведение.</div><div><b className="text-foreground">Difficulty</b><br />Масштабирует HP и difficulty наблюдаемой волны, не меняя vanilla event selection.</div><div><b className="text-foreground">Adaptive</b><br />Подмешивает накопленную эффективность экипажей и кораблей в будущие vanilla события.</div></CardContent></Card>
      </TabsContent>

      <TabsContent value="schemes" className="space-y-4 pt-2">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric icon={Layers} label="Схем в пуле" value={String(schemeList.length)} detail="каждая волна случайно выбирает одну по весам" tone="primary" />
          <Metric icon={Target} label="Мин. дистанция спавна" value={`${Math.round(Number(global.spawn_min_distance_meters ?? 100))} м`} detail="волны не спавнятся ближе к кораблю" tone="warning" />
          <Metric icon={MapPin} label="Последняя схема" value={dashboard.last_spawn_scheme || "—"} detail={dashboard.last_spawn_zone ? `зона: ${dashboard.last_spawn_zone}` : "нет данных за раунд"} tone="info" />
          <div className="flex flex-col justify-center gap-2 rounded-xl border bg-muted/10 p-3 text-xs"><div className="flex items-center justify-between"><span>Схемы включены</span><Switch checked={global.spawn_scheme_enabled !== false} onCheckedChange={(value) => setGlobal("spawn_scheme_enabled", value)} /></div><span className="text-muted-foreground">выключено — позиции берутся из spawn_type волн (MainPath и т.п.)</span></div>
        </div>
        <div className="rounded-xl border border-primary/30 bg-primary/5 p-3 text-xs text-muted-foreground">Для волн с позициями MainPath/SidePath и пойманных ванильных событий схема заменяет фиксированные точки интереса уровня: монстры спавнятся относительно корабля — перед ним, сверху или снизу, не ближе минимальной дистанции и никогда не сзади. Руины, крейт-затонувшие и волны из списка исключений ниже по-прежнему используют структурные точки уровня.</div>
        <div className="space-y-2">
          {schemeList.map((scheme, index) => <SchemeRow key={index} scheme={scheme} index={index} onEdit={() => setSchemeDialog({ index })} onDelete={() => updateSchemes((schemes) => { schemes.splice(index, 1) })} />)}
          {!schemeList.length && <div className="rounded-xl border border-dashed p-6 text-center text-xs text-muted-foreground">Список пуст — плагин будет ставить волны на точках уровня (mainpath и т.п.), пока не появится хотя бы одна схема</div>}
          <Button variant="outline" onClick={() => { updateSchemes((schemes) => { schemes.push(newScheme()) }); setSchemeDialog({ index: schemeList.length, isNew: true }) }}><Plus className="size-4" /> Добавить схему</Button>
        </div>
        <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="flex items-center gap-2 text-sm"><Ban className="size-4 text-warning" /> Списки исключений</CardTitle><CardDescription className="text-xs">Подстроки в названиях волн — списки можно пополнять и чистить динамически</CardDescription></CardHeader>
          <CardContent className="grid gap-3 p-4 lg:grid-cols-2">
            <TagListEditor label="Не удерживать перед спавном" description="Волны с этими подстроками запускаются сразу, без hold_seconds (по умолчанию: abyss, moloch, cave)." tags={ignoreList("hold_ignore_names")} onChange={(tags) => setGlobal("hold_ignore_names", tags)} addPlaceholder="например: moloch" />
             <TagListEditor label="Без схем спавна" description="Волны с этими подстроками спавнятся на структурных точках уровня вместо схем (по умолчанию: cave, abyss)." tags={ignoreList("spawn_scheme_ignore_names")} onChange={(tags) => setGlobal("spawn_scheme_ignore_names", tags)} addPlaceholder="например: cave" />
             <TagListEditor label="Без множителей количества и HP" description="Волны с этими подстроками не получают adaptive/player amount и HP множители (по умолчанию: abyss)." tags={ignoreList("multiplier_exempt_wave_names")} onChange={(tags) => setGlobal("multiplier_exempt_wave_names", tags)} addPlaceholder="например: abyss" />
          </CardContent></Card>
      </TabsContent>

      <TabsContent value="logs" className="space-y-4 pt-2"><div className="grid gap-4 sm:grid-cols-3"><Metric icon={TrendingUp} label="Завершено волн" value={String(waveLogs.reduce((sum, item) => sum + item.times_spawned, 0))} detail={`${waveLogs.length} типа событий`} tone="primary" /><Metric icon={TrendingDown} label="Средняя скорость" value={waveLogs.length ? `${Math.round(waveLogs.filter((item) => item.avg_repel_seconds).reduce((sum, item) => sum + item.avg_repel_seconds, 0) / Math.max(1, waveLogs.filter((item) => item.avg_repel_seconds).length))} с` : "—"} detail="Среднее по типам волн" tone="warning" /><Metric icon={BrainCircuit} label="Наблюдений по кораблям" value={String(shipLogs.reduce((sum, item) => sum + item.times_spawned, 0))} detail="Используются в корреляции" tone="success" /></div><div className="grid gap-4 lg:grid-cols-2"><ChartCard title="Performance trajectory" description="Последние доступные значения из adaptive-memory"><Line options={baseChartOptions} data={trendChart} /></ChartCard><ChartCard title="Efficiency by ship" description="Сравнение кораблей по performance EMA"><Bar options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, legend: { display: false } } }} data={shipChart} /></ChartCard></div><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Лог разрешения волн</CardTitle><CardDescription className="text-xs">Как быстро экипажи закрывали волны и какой ущерб получили</CardDescription></CardHeader><CardContent><WaveLogs logs={waveLogs} /></CardContent></Card><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Эффективность по кораблям</CardTitle><CardDescription className="text-xs">Детализация для сравнения дизайна субмарин</CardDescription></CardHeader><CardContent><ShipLogs logs={shipLogs} /></CardContent></Card><Card className="py-0"><CardHeader className="border-b px-4 py-3"><CardTitle className="text-sm">Автоматический анализ и предложения LLM</CardTitle><CardDescription className="text-xs">Новый отчёт создаётся после каждого третьего раунда на основе последних 15.</CardDescription></CardHeader><CardContent className="space-y-3 p-4 text-sm">{latestRoundAnalysis?.status === "running" || latestRoundAnalysis?.status === "queued" ? <p>LLM анализирует раунды…</p> : latestRoundAnalysis?.status === "failed" ? <p className="text-destructive">{latestRoundAnalysis.error_message}</p> : latestRoundAnalysis?.result ? <><p>{latestRoundAnalysis.result.crew_summary || latestRoundAnalysis.result.situation}</p>{latestRoundAnalysis.result.recommendations?.map((item, index) => <p key={index}><b>{item.title}</b> — {item.detail}</p>)}<Button variant="outline" size="sm" onClick={() => setActiveTab("llm")}>Открыть предложения настроек</Button></> : <p className="text-muted-foreground">Ожидаем анализа LLM после трёх раундов.</p>}</CardContent></Card></TabsContent>

      <TabsContent value="manual" className="space-y-4 pt-2"><div className="rounded-xl border border-primary/30 bg-primary/5 p-3 text-xs text-muted-foreground"><div className="flex items-center gap-2 font-medium text-foreground"><Target className="size-4 text-primary" /> Полная ручная настройка</div><p className="mt-1">Здесь доступны планы, волны и сырой JSON без ограничений визуального режима. Изменения не попадут на сервер до нажатия «Сохранить».</p></div><Tabs defaultValue="plans"><TabsList className="grid w-full grid-cols-3"><TabsTrigger value="plans">Планы · {draft.plans.length}</TabsTrigger><TabsTrigger value="waves">Волны · {draft.config.waves.length}</TabsTrigger><TabsTrigger value="json">Raw JSON</TabsTrigger></TabsList><TabsContent value="plans" className="space-y-4 pt-3"><div className="grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(380px,.75fr)]"><div className="relative h-[480px] overflow-hidden rounded-xl border">
  {rounds.isError ? <MapPlaceholder height="480px">Не удалось загрузить активные раунды{rounds.error instanceof Error ? `: ${rounds.error.message}` : ""}</MapPlaceholder>
    : !round ? <MapPlaceholder height="480px">Для {server} сейчас нет активного раунда<br /><span className="text-xs">Планы можно редактировать справа</span></MapPlaceholder>
      : geometry.isError || world.isError ? <MapPlaceholder height="480px">Ошибка загрузки карты: {((geometry.error ?? world.error) as Error | null)?.message ?? "нет данных"}</MapPlaceholder>
        : geometry.data && world.data ? <WorldCanvas geometry={geometry.data} state={world.data} selectedCharacterId={null} onSelectCharacter={() => {}} targetMode={placing} onPickTarget={createPlan} markers={markers} selectedMarkerId={selectedPlanId} onSelectMarker={setSelectedPlanId} onMoveMarker={(id, x, y) => updateDraft((next) => { const plan = next.plans.find((item) => item.id === id); if (plan) { plan.x = x; plan.y = y } })} onMapContextMenu={(info) => { setContextMenu({ ...info }); if (info.markerId) setSelectedPlanId(info.markerId) }} />
          : <MapPlaceholder height="480px">Загрузка карты раунда…</MapPlaceholder>}
<div className="absolute bottom-3 left-3 flex flex-wrap gap-2"><Button variant={placing ? "destructive" : "secondary"} onClick={() => setPlacing(!placing)}>{placing ? <X className="size-4" /> : <Crosshair className="size-4" />}{placing ? "Отмена" : "Поставить точку"}</Button><Button variant="outline" size="icon" title={showLegend ? "Скрыть легенду" : "Показать легенду"} onClick={() => setShowLegend(!showLegend)}>{showLegend ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</Button></div>{showLegend && <div className="absolute right-3 top-3 rounded-lg border bg-background/90 p-2 text-[11px] shadow"><div className="mb-1 flex items-center gap-1 font-medium"><Layers className="size-3" /> Легенда</div><div><span className="mr-1 inline-block size-2 rounded-full bg-[#59D6C2]" />Ручной</div><div><span className="mr-1 inline-block size-2 rounded-full bg-[#F6C453]" />Авто</div></div>}</div><Card className="min-h-0 overflow-hidden py-0"><CardContent className="h-full overflow-y-auto p-4"><div className="flex gap-2"><div className="relative flex-1"><Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-7" placeholder="Поиск по планам" value={planSearch} onChange={(event) => setPlanSearch(event.target.value)} /></div><Select value={planStatus} onValueChange={(value) => setPlanStatus(value as PlanStatus)}><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все</SelectItem><SelectItem value="planned">Запланированы</SelectItem><SelectItem value="cancelled">Отменены</SelectItem><SelectItem value="spawned">Заспавнены</SelectItem></SelectContent></Select></div><div className="mt-3 flex gap-2"><Select value={String(selectedWave)} onValueChange={(value) => setSelectedWave(Number(value))}><SelectTrigger className="flex-1"><SelectValue /></SelectTrigger><SelectContent>{draft.config.waves.map((wave, index) => <SelectItem key={index} value={String(index)}>{wave.name}</SelectItem>)}</SelectContent></Select><Button variant="outline" onClick={() => createPlan(0, 0)}><Plus className="size-4" /> Создать</Button></div><div className="mt-3 max-h-64 space-y-1 overflow-y-auto pr-1">{filteredPlans.map((plan) => <button key={plan.id} className={`flex w-full items-center gap-2 rounded-lg border p-2.5 text-left transition ${plan.id === selectedPlanId ? "border-primary bg-accent" : "hover:bg-muted/50"}`} onClick={() => setSelectedPlanId(plan.id)}><span className={`size-2 shrink-0 rounded-full ${plan.status === "cancelled" ? "bg-muted-foreground/40" : plan.source === "manual" ? "bg-[#59D6C2]" : "bg-[#F6C453]"}`} /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{plan.wave_name || plan.name}</span><span className="block truncate text-xs text-muted-foreground">{Math.round(plan.x)}, {Math.round(plan.y)} · {plan.status}</span></span><Badge variant="secondary" className="text-[10px]">{plan.monsters.length} мобов</Badge></button>)}{!filteredPlans.length && <div className="py-6 text-center text-xs text-muted-foreground"><Filter className="mx-auto mb-2 size-4" />Планов не найдено</div>}</div>{selectedPlan && <div className="mt-4 space-y-3 rounded-xl border p-3"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline" className="font-mono text-[10px]">{selectedPlan.wave_name || selectedPlan.name}</Badge>{getWaveOverride(selectedPlan.wave_name || selectedPlan.name) && <Badge variant="outline" className="text-[10px] text-warning">есть пометка</Badge>}<Button size="sm" variant="outline" onClick={() => setPlanDialogOpen(true)}><Pencil className="size-3.5" /> Редактировать план</Button></div><div className="flex flex-wrap gap-2"><Button variant={selectedPlan.status === "cancelled" ? "secondary" : "destructive"} onClick={() => updateDraft((next) => { const plan = next.plans.find((item) => item.id === selectedPlan.id)!; plan.status = plan.status === "cancelled" ? "planned" : "cancelled" })}>{selectedPlan.status === "cancelled" ? "Вернуть план" : "Отменить спавн"}</Button><Button variant="outline" onClick={() => duplicatePlan(selectedPlan.id)}><Copy className="size-3.5" /> Дублировать</Button><Button variant="ghost" onClick={() => { updateDraft((next) => { next.plans = next.plans.filter((plan) => plan.id !== selectedPlan.id); setSelectedPlanId(null) }); setPlanDialogOpen(false) }}><Trash2 className="size-3.5" /> Удалить</Button><Button variant="outline" onClick={() => copyCoords(selectedPlan.x, selectedPlan.y)}><MapPin className="size-3.5" /> Коорд.</Button></div></div>}</CardContent></Card></div></TabsContent><TabsContent value="waves" className="space-y-3 pt-3"><div className="space-y-1">{draft.config.waves.map((wave, index) => <div key={index} className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-2.5 text-xs"><span className={`size-2 shrink-0 rounded-full ${autoNonBlockByName(wave.name) ? "bg-info" : "bg-[#F6C453]"}`} /><span className="min-w-0 flex-1"><span className="flex items-center gap-2"><span className="truncate text-sm font-medium">{wave.name}</span>{wave.non_blocking === true && <Badge variant="outline" className="text-[10px] text-muted-foreground">неблок.</Badge>}{wave.non_blocking == null && autoNonBlockByName(wave.name) && <Badge variant="outline" className="text-[10px] text-info">авто неблок.</Badge>}{getWaveOverride(wave.name) && <Badge variant="outline" className="text-[10px] text-warning">есть пометка</Badge>}{wave.learned && <Badge variant="secondary" className="text-[10px]">изученная</Badge>}</span><span className="block truncate text-muted-foreground">{wave.spawn_type} · сложность {wave.difficulty > 0 ? wave.difficulty : "авто"} · {wave.monsters.length} видов</span></span><Button size="sm" variant="outline" onClick={() => { setSelectedWave(index); setWaveDialogIndex(index) }}><Pencil className="size-3.5" /> Править</Button><Button size="sm" variant="ghost" disabled={draft.config.waves.length < 2} onClick={() => { updateDraft((next) => { next.config.waves.splice(index, 1) }); setWaveDialogIndex(null) }}><Trash2 className="size-3.5" /> Удалить</Button></div>)}{!draft.config.waves.length && <div className="rounded-xl border border-dashed p-6 text-center text-xs text-muted-foreground">Волн в конфиге нет</div>}</div><Button variant="outline" onClick={() => { updateDraft((next) => { next.config.waves.push(emptyWave()) }); setWaveDialogIndex(draft.config.waves.length) }}><Plus className="size-4" /> Добавить волну</Button></TabsContent><TabsContent value="json" className="space-y-3 pt-3"><div className="grid gap-4 xl:grid-cols-2"><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Полный config</CardTitle></CardHeader><CardContent><Textarea className="min-h-[620px] font-mono text-xs" value={JSON.stringify(draft.config, null, 2)} onChange={(event) => { try { const parsed = JSON.parse(event.target.value); updateDraft((next) => { next.config = parsed }) } catch { /* keep last valid JSON */ } }} /></CardContent></Card><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Memory</CardTitle><CardDescription className="text-xs">Редактирование защищено отдельным permission.</CardDescription></CardHeader><CardContent><Textarea className="min-h-[620px] font-mono text-xs" disabled={!canDo(admin, "monster_spawn", "manage_memory")} value={JSON.stringify(draft.memory, null, 2)} onChange={(event) => { try { const parsed = JSON.parse(event.target.value); updateDraft((next) => { next.memory = parsed }) } catch { /* keep last valid JSON */ } }} /></CardContent></Card></div></TabsContent></Tabs></TabsContent>
    </Tabs>
    {contextMenu && <div className="fixed z-50 min-w-52 rounded-xl border bg-popover p-1 text-sm shadow-xl" style={{ left: Math.min(contextMenu.screenX, window.innerWidth - 230), top: Math.min(contextMenu.screenY, window.innerHeight - 240) }} onClick={(event) => event.stopPropagation()}><div className="px-2 py-1 text-[11px] text-muted-foreground">{contextMenu.wx}, {contextMenu.wy}</div>{contextMenu.markerId ? <><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { setSelectedPlanId(contextMenu.markerId); setContextMenu(null) }}><LocateFixed className="size-3.5" /> Редактировать</button><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { duplicatePlan(contextMenu.markerId!); setContextMenu(null) }}><Files className="size-3.5" /> Дублировать</button><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { copyCoords(contextMenu.wx, contextMenu.wy); setContextMenu(null) }}><Copy className="size-3.5" /> Копировать координаты</button><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { updateDraft((next) => { const plan = next.plans.find((item) => item.id === contextMenu.markerId); if (plan) plan.status = plan.status === "cancelled" ? "planned" : "cancelled" }); setContextMenu(null) }}>{draft.plans.find((plan) => plan.id === contextMenu.markerId)?.status === "cancelled" ? <><Eye className="size-3.5" /> Вернуть</> : <><EyeOff className="size-3.5" /> Отменить</>}</button></> : <><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { createPlan(contextMenu.wx, contextMenu.wy); setContextMenu(null) }}><Plus className="size-3.5" /> Создать план здесь</button><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { copyCoords(contextMenu.wx, contextMenu.wy); setContextMenu(null) }}><Copy className="size-3.5" /> Копировать координаты</button></>}</div>}

    {/* 5: всплывающие окна правки */}
    {overrideDialogWave && <WaveOverrideDialog
      open
      onOpenChange={(open) => { if (!open) setOverrideDialogWave(null) }}
      waveName={overrideDialogWave.name}
      currentDifficulty={overrideDialogWave.difficulty}
      monstersAlive={overrideDialogWave.alive}
      engaged={overrideDialogWave.engaged}
      initial={getWaveOverride(overrideDialogWave.name) ?? null}
      onApply={(override) => setWaveOverride(overrideDialogWave.name, override)}
      onRemove={() => { removeWaveOverride(overrideDialogWave.name); toast.success("Пометка волны убрана") }}
    />}
    {waveDialogIndex != null && draft.config.waves[waveDialogIndex] && <EditorDialog
      open
      onOpenChange={(open) => { if (!open) setWaveDialogIndex(null) }}
      title={waveDialogIndex >= draft.config.waves.length ? "Новая волна" : `Волна «${draft.config.waves[waveDialogIndex].name}»`}
      icon={Target}
      description="Настройки срабатывания, состав и постоянные пометки волны"
    >
      <WaveEditor
        wave={draft.config.waves[waveDialogIndex]}
        onChange={(wave) => updateDraft((next) => { next.config.waves[waveDialogIndex] = wave })}
        override={getWaveOverride(draft.config.waves[waveDialogIndex].name)}
        onOverrideChange={(override) => setWaveOverride(draft.config.waves[waveDialogIndex].name, override)}
      />
    </EditorDialog>}
    {selectedPlan && planDialogOpen && <EditorDialog
      open
      onOpenChange={setPlanDialogOpen}
      title={`План «${selectedPlan.wave_name || selectedPlan.name}»`}
      icon={MapPin}
      description={`Точка ${Math.round(selectedPlan.x)}, ${Math.round(selectedPlan.y)} · статус: ${selectedPlan.status}`}
    >
      <WaveEditor
        wave={selectedPlan}
        onChange={(wave) => updateDraft((next) => { const index = next.plans.findIndex((plan) => plan.id === selectedPlan.id); next.plans[index] = { ...next.plans[index], ...wave, wave_name: wave.name } })}
        override={getWaveOverride(selectedPlan.wave_name || selectedPlan.name)}
        onOverrideChange={(override) => setWaveOverride(selectedPlan.wave_name || selectedPlan.name, override)}
      />
    </EditorDialog>}
    {learnedDialogOpen && selectedLearned && learnedTemplateFor(selectedLearned) && <EditorDialog
      open
      onOpenChange={setLearnedDialogOpen}
      title={`Изученная волна «${selectedLearned}»`}
      icon={Sparkles}
      description="Абсолютные значения сохраняются в памяти навсегда; кнопки масштабирования правят состав и HP шаблона, пометка — работает и для активных волн"
    >
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => scaleLearnedTemplate(selectedLearned, "count", 0.5)}>×0.5 состав</Button>
          <Button size="sm" variant="outline" onClick={() => scaleLearnedTemplate(selectedLearned, "count", 2)}>×2 состав</Button>
          <Button size="sm" variant="outline" onClick={() => scaleLearnedTemplate(selectedLearned, "hp_multiplier", 0.5)}>×0.5 HP</Button>
          <Button size="sm" variant="outline" onClick={() => scaleLearnedTemplate(selectedLearned, "hp_multiplier", 2)}>×2 HP</Button>
        </div>
        <WaveEditor
          wave={learnedTemplateFor(selectedLearned)!}
          onChange={(wave) => updateLearnedTemplate(selectedLearned, (template) => { Object.assign(template, wave) })}
          override={getWaveOverride(selectedLearned)}
          onOverrideChange={(override) => setWaveOverride(selectedLearned, override)}
        />
      </div>
    </EditorDialog>}
    {schemeDialog && schemeList[schemeDialog.index] && <EditorDialog
      open
      onOpenChange={(open) => { if (!open) setSchemeDialog(null) }}
      title={schemeDialog.isNew ? "Новая схема спавна" : `Схема «${schemeList[schemeDialog.index].name || `#${schemeDialog.index + 1}`}»`}
      icon={Layers}
      description="Веса секторов задают, куда относительно корабля попадут монстры; «сзади» запрещён всегда"
      footer={<span className="flex gap-2">
        <Button variant="ghost" onClick={() => { updateSchemes((schemes) => { schemes.splice(schemeDialog.index, 1) }); setSchemeDialog(null) }}><Trash2 className="size-3.5" /> Удалить</Button>
        <Button variant="outline" onClick={() => setSchemeDialog(null)}>Готово</Button>
      </span>}
    >
      <SchemeEditor
        scheme={schemeList[schemeDialog.index]}
        minMeters={Number(global.spawn_min_distance_meters ?? 100)}
        onChange={(next) => updateSchemes((schemes) => { schemes[schemeDialog.index] = next })}
      />
    </EditorDialog>}
  </div>
}
