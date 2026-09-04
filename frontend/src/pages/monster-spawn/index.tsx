import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Bar, Line } from "react-chartjs-2"
import {
  Activity, AlertTriangle, Anchor, Bot, BrainCircuit, Check, ChevronRight, CircleGauge,
  Copy, Crosshair, Eye, EyeOff, Files, Filter, FlaskConical, Gauge, Info, Layers, LocateFixed,
  MapPin, Plus, Radar, Save, Search, ShipWheel, Skull, SlidersHorizontal, Sparkles,
  Target, Trash2, TrendingDown, TrendingUp, X, Zap,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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
  type HeldVanillaTelemetry,
  type LearnedWaveTelemetry,
  type LLMRuntime,
  type MonsterSpawnAnalysis,
  type MonsterSpawnRecommendation,
  type MonsterSpawnShipLog,
  type MonsterSpawnState,
  type MonsterSpawnWaveLog,
  type PendingWaveTelemetry,
  type PlannedActionTelemetry,
  type RecentWaveTelemetry,
  type SpawnPlan,
  type SpawnWave,
  type SpeciesThreatTelemetry,
} from "./api"

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const number = (value: string) => Number.isFinite(Number(value)) ? Number(value) : 0
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)

const emptyWave = (): SpawnWave => ({
  name: "Новая волна", min_progress: 0, max_progress: 0, min_round_time: 0, min_players: 0,
  max_sub_health: 0, max_crew_health: 0, chance: 1, commonness: 1, spawn_type: "around",
  difficulty: -1, delay_between_spawns: 0.1, scatter: 500, offset: 0, spawn_distance: 0,
  spawn_point_tag: "", reset_time: 0, play_dead_probability: -1,
  monsters: [{ species: "crawler", count: 1, amount_min: 0, amount_max: 0, max_amount_per_level: 0,
    scale_with_players: 0, hp_multiplier: 1, hp_scale_with_progress: 0, mobility: 1 }],
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

function ChartCard({ title, description, children, className = "" }: { title: string; description?: string; children: React.ReactNode; className?: string }) {
  return <Card className={`overflow-hidden py-0 ${className}`}>
    <CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">{title}</CardTitle>{description && <CardDescription className="text-xs">{description}</CardDescription>}</CardHeader>
    <CardContent className="h-72 p-4">{children}</CardContent>
  </Card>
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

function WaveEditor({ wave, onChange }: { wave: SpawnWave; onChange: (wave: SpawnWave) => void }) {
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
    </div>
    <div className="space-y-2"><div className="flex items-center justify-between"><h4 className="text-sm font-medium">Состав волны</h4><Button size="sm" variant="outline" onClick={() => set("monsters", [...wave.monsters, clone(emptyWave().monsters[0])])}><Plus className="size-3.5" /> Существо</Button></div>
      {wave.monsters.map((monster, index) => <div key={index} className="grid grid-cols-2 gap-2 rounded-xl border bg-muted/20 p-3 lg:grid-cols-5">
        <Label className="space-y-1 text-xs"><span>Вид</span><Input value={monster.species} onChange={(event) => { const monsters = [...wave.monsters]; monsters[index] = { ...monster, species: event.target.value }; set("monsters", monsters) }} /></Label>
        {(["count", "amount_min", "amount_max", "max_amount_per_level", "scale_with_players", "hp_multiplier", "hp_scale_with_progress", "mobility"] as const).map((key) => <NumberField key={key} label={key.replaceAll("_", " ")} value={monster[key]} onChange={(value) => { const monsters = [...wave.monsters]; monsters[index] = { ...monster, [key]: value }; set("monsters", monsters) }} />)}
        <Button className="lg:col-start-5" size="sm" variant="ghost" disabled={wave.monsters.length === 1} onClick={() => set("monsters", wave.monsters.filter((_, itemIndex) => itemIndex !== index))}><Trash2 className="size-3.5" /> Удалить</Button>
      </div>)}
    </div>
  </div>
}

function Recommendation({ item }: { item: MonsterSpawnRecommendation }) {
  const Icon = item.severity === "warning" ? AlertTriangle : item.severity === "success" ? Check : Info
  const tone = item.severity === "warning" ? "text-warning border-warning/30 bg-warning/5" : item.severity === "success" ? "text-success border-success/30 bg-success/5" : "text-info border-info/30 bg-info/5"
  return <div className={`rounded-xl border p-4 ${tone}`}><div className="flex items-start gap-3"><Icon className="mt-0.5 size-4 shrink-0" /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><div className="text-sm font-semibold text-foreground">{item.title}</div><Badge variant="outline" className="text-[10px]">уверенность {Math.round(item.confidence * 100)}%</Badge></div><p className="mt-1 text-xs text-muted-foreground">{item.reason}</p><div className="mt-3 rounded-lg bg-background/60 p-3 text-xs text-foreground"><span className="font-medium">Предложение:</span> {item.suggested_adjustment}</div><div className="mt-2 font-mono text-[10px] text-muted-foreground">Данные: {Object.entries(item.evidence).map(([key, value]) => `${key}=${String(value)}`).join(" · ")}</div></div></div></div>
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

function ModificationsList({ modifications }: { modifications: string[] | string }) {
  const list = Array.isArray(modifications) ? modifications : modifications ? [modifications] : []
  if (!list.length) return <div className="text-[11px] text-muted-foreground">Модификаций не было — волна применилась в исходном виде</div>
  return <ul className="space-y-1">{list.map((item, index) => <li key={index} className="font-mono text-[11px] text-warning">{item}</li>)}</ul>
}

function ActiveWaveCard({ wave }: { wave: ActiveWaveTelemetry }) {
  const modified = wave.modifications?.length > 0
  return <div className="space-y-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <Radar className="size-4 text-destructive" />
      <span className="font-semibold">{wave.wave_name}</span>
      <Badge variant="outline" className="text-[10px]">ID {wave.wave_id}</Badge>
      <Badge variant="secondary" className="text-[10px]">{REASON_LABELS[wave.spawn_reason] ?? wave.spawn_reason}</Badge>
      {wave.struggling && <Badge variant="destructive" className="text-[10px]">экипаж не справляется</Badge>}
    </div>
    <div className="text-muted-foreground">Состав: <span className="text-foreground">{monsterCountsText(wave.monster_counts)}</span></div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div><div className="font-mono text-sm">{Math.round(wave.elapsed_seconds)}с</div><div className="text-[10px] text-muted-foreground">в бою (ожидалось {Math.round(wave.expected_repel_seconds)}с)</div></div>
      <div><div className="font-mono text-sm">{wave.monsters_alive}/{wave.monsters_total}</div><div className="text-[10px] text-muted-foreground">живых монстров</div></div>
      <div><div className="font-mono text-sm">{Math.round(wave.engagement_seconds)}с</div><div className="text-[10px] text-muted-foreground">вовлечённость</div></div>
      <div><div className="font-mono text-sm">{wave.effective_difficulty.toFixed(1)}</div><div className="text-[10px] text-muted-foreground">сложность</div></div>
    </div>
    {modified && <ModificationsList modifications={wave.modifications} />}
  </div>
}

function RecentWaveCard({ wave }: { wave: RecentWaveTelemetry }) {
  const outcome = OUTCOME_LABELS[wave.outcome] ?? { label: wave.outcome, tone: "text-muted-foreground border-border bg-muted/30" }
  const modified = wave.modifications && wave.modifications.length > 0
  return <div className="space-y-2 rounded-xl border bg-muted/10 p-3 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-semibold">{wave.wave_name}</span>
      <Badge variant="outline" className="text-[10px]">ID {wave.wave_id}</Badge>
      <Badge variant="outline" className={`text-[10px] ${outcome.tone}`}>{outcome.label}</Badge>
      <Badge variant="secondary" className="text-[10px]">{REASON_LABELS[wave.spawn_reason] ?? wave.spawn_reason}</Badge>
    </div>
    <div className="text-muted-foreground">Состав: <span className="text-foreground">{monsterCountsText(wave.monster_counts)}</span></div>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
      <div><div className="font-mono text-sm">{Math.round(wave.repel_seconds)}с</div><div className="text-[10px] text-muted-foreground">зачистка (ожидалось {Math.round(wave.expected_repel_seconds)}с)</div></div>
      <div><div className="font-mono text-sm">{wave.performance.toFixed(2)}</div><div className="text-[10px] text-muted-foreground">performance</div></div>
      <div><div className="font-mono text-sm">{Math.round(wave.engagement_seconds)}с</div><div className="text-[10px] text-muted-foreground">вовлечённость</div></div>
      <div><div className="font-mono text-sm">{wave.crew_deaths}</div><div className="text-[10px] text-muted-foreground">потери экипажа</div></div>
      <div><div className="font-mono text-sm">{wave.sub_health_loss.toFixed(1)}%</div><div className="text-[10px] text-muted-foreground">урон лодке</div></div>
    </div>
    {modified ? <ModificationsList modifications={wave.modifications} /> : <div className="text-[11px] text-muted-foreground">Без модификаций</div>}
  </div>
}

function PlannedActionCard({ planned }: { planned: PlannedActionTelemetry }) {
  const probabilityPercent = Math.round((planned.probability ?? 0) * 100)
  return <Card className="py-0"><CardContent className="space-y-2 p-4">
    <div className="flex items-center gap-2 text-sm font-medium"><Zap className="size-4 text-primary" /> Что планирует контроллер</div>
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant="outline" className="font-mono text-xs">{planned.action}</Badge>
      <span className="text-xs text-muted-foreground">вероятность ближайшего спавна:</span>
      <span className="font-mono text-sm text-primary">{probabilityPercent}%</span>
    </div>
    <div className="text-xs text-muted-foreground">{planned.detail}</div>
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
      <SliderField label="Целевая сложность в минуту" value={Number(global.target_difficulty_per_minute ?? 0.8)} min={0.1} max={3} step={0.05} onChange={(value) => setGlobal("target_difficulty_per_minute", value)} />
      <SliderField label="Пауза между спавнами, с" value={Number(global.min_seconds_between_spawns ?? 90)} min={15} max={600} step={5} suffix="с" onChange={(value) => setGlobal("min_seconds_between_spawns", value)} />
      <SliderField label="Бюджет краткоср. сложности" value={Number(global.max_short_term_difficulty ?? 6)} min={1} max={30} step={0.5} onChange={(value) => setGlobal("max_short_term_difficulty", value)} />
      <SliderField label="Порог интенсивности" value={Number(global.intensity_threshold ?? 0.2)} min={0.05} max={0.9} step={0.01} onChange={(value) => setGlobal("intensity_threshold", value)} />
      <SliderField label="Vanilla: количество" value={Number(vanilla.amount_multiplier ?? 1)} min={0.1} max={3} step={0.05} suffix="x" onChange={(value) => setVanilla("amount_multiplier", value)} />
      <SliderField label="Vanilla: сила (HP)" value={Number(vanilla.difficulty_multiplier ?? 1)} min={0.3} max={2.5} step={0.05} suffix="x" onChange={(value) => setVanilla("difficulty_multiplier", value)} />
      <SliderField label="Vanilla: удержание спавна, с" value={Number(vanilla.hold_seconds ?? 30)} min={0} max={120} step={5} suffix="с" onChange={(value) => setVanilla("hold_seconds", value)} />
      <SliderField label="Смещение к опасным видам" value={Number(global.danger_bias_strength ?? 0.5)} min={0} max={1.5} step={0.05} onChange={(value) => setGlobal("danger_bias_strength", value)} />
      <SliderField label="Эскалация обхода волн" value={Number(global.bypass_difficulty_escalation ?? 0.5)} min={0} max={1} step={0.05} onChange={(value) => setGlobal("bypass_difficulty_escalation", value)} />
      <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Вторая волна при атаке</span><Switch checked={global.single_wave_mode !== false} onCheckedChange={(value) => setGlobal("single_wave_mode", value)} /></div>
      <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Перехват темпа</span><Switch checked={global.pace_intercept_enabled !== false} onCheckedChange={(value) => setGlobal("pace_intercept_enabled", value)} /></div>
      <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Изучение ванильных волн</span><Switch checked={global.learned_waves_enabled !== false} onCheckedChange={(value) => setGlobal("learned_waves_enabled", value)} /></div>
    </CardContent></Card>
}

function LLMPanel({ server }: { server: string }) {
  const queryClient = useQueryClient()
  const [instruction, setInstruction] = useState("")
  const [mode, setMode] = useState<"analyze" | "apply" | "spawn">("analyze")
  const runtime = useQuery({ queryKey: ["monster-spawn-llm", server], queryFn: () => monsterSpawnApi.llmRuntime(server), refetchInterval: 10000 })
  const saveRuntime = useMutation({
    mutationFn: (body: Partial<Pick<LLMRuntime, "enabled" | "auto_apply" | "auto_interval_minutes">>) => monsterSpawnApi.updateLLMRuntime(server, body),
    onSuccess: () => { toast.success("Настройки ассистента обновлены"); queryClient.invalidateQueries({ queryKey: ["monster-spawn-llm", server] }) },
    onError: (error: Error) => toast.error(error.message),
  })
  const run = useMutation({
    mutationFn: () => monsterSpawnApi.runLLM(server, { mode, instruction: instruction.trim() || undefined }),
    onSuccess: () => { toast.success("LLM-цикл выполнен"); queryClient.invalidateQueries({ queryKey: ["monster-spawn-llm", server] }) },
    onError: (error: Error) => toast.error(error.message),
  })

  const r = runtime.data?.runtime
  const deployment = runtime.data?.deployment
  const last = r?.last_result
  const history = r?.history ?? []

  return <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3">
    <CardTitle className="flex items-center gap-2 text-sm"><Bot className="size-4 text-primary" /> LLM-ассистент</CardTitle>
    <CardDescription className="text-xs">{deployment?.has_base_url ? `endpoint: ${deployment.base_url} · модель: ${deployment.model} · t=${deployment.temperature}` : "Не настроен — задайте MONSTER_LLM_BASE_URL в .env (см. docs/MONSTER_LLM_SETUP.md)"}</CardDescription>
  </CardHeader>
    <CardContent className="space-y-4 p-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Ассистент включён</span><Switch checked={r?.enabled === true} onCheckedChange={(value) => saveRuntime.mutate({ enabled: value })} /></div>
        <div className="flex items-center justify-between gap-3 rounded-xl border bg-muted/10 p-3 text-xs"><span>Автоприменение</span><Switch checked={r?.auto_apply === true} onCheckedChange={(value) => saveRuntime.mutate({ auto_apply: value })} /></div>
        <Label className="space-y-1 rounded-xl border bg-muted/10 p-3 text-xs"><span className="text-muted-foreground">Автоцикл, мин (0 = вручную)</span><Input type="number" min={0} max={1440} value={r?.auto_interval_minutes ?? 0} onChange={(event) => saveRuntime.mutate({ auto_interval_minutes: Math.max(0, Number(event.target.value) || 0) })} /></Label>
      </div>
      <div className="space-y-2">
        <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Команда оператора (необязательно)</span><Textarea rows={2} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="Например: экипаж скучает, добавь давления впереди по курсу" /></Label>
        <div className="flex flex-wrap gap-2">
          <Select value={mode} onValueChange={(value) => setMode(value as typeof mode)}><SelectTrigger className="w-56"><SelectValue /></SelectTrigger><SelectContent>
            <SelectItem value="analyze">analyze · разбор и рекомендации</SelectItem>
            <SelectItem value="apply">apply · применить настройки</SelectItem>
            <SelectItem value="spawn">spawn · настройки + спавн</SelectItem>
          </SelectContent></Select>
          <Button disabled={run.isPending || !runtime.data?.endpoint_configured} onClick={() => run.mutate()}><FlaskConical className="size-4" /> {run.isPending ? "Модель думает…" : "Запустить LLM-цикл"}</Button>
        </div>
      </div>
      {last && <div className="space-y-2 rounded-xl border bg-muted/10 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{last.at}</Badge><Badge variant="secondary">{last.mode}</Badge><span className="text-muted-foreground">уверенность {Math.round((last.confidence ?? 0) * 100)}% · {last.duration_ms}мс</span></div>
        <div className="text-foreground">{last.situation}</div>
        {last.crew_summary && <div className="rounded-lg bg-background/60 p-3"><div className="mb-1 font-medium text-foreground">Эффективность экипажа</div><div className="text-muted-foreground">{last.crew_summary}</div></div>}
        {last.recommendations?.length > 0 && <div><div className="font-medium">Рекомендации оператору:</div><ul className="mt-1 space-y-1">{last.recommendations.map((rec, index) => <li key={index} className="rounded-lg bg-background/60 p-2"><span className="font-medium text-foreground">{rec.title}</span>{rec.detail && <span className="text-muted-foreground"> — {rec.detail}</span>}</li>)}</ul></div>}        {last.applied_changes?.length > 0 && <div><div className="font-medium">Применённые изменения:</div><ul className="mt-1 space-y-1">{last.applied_changes.map((change, index) => <li key={index} className="font-mono text-[11px] text-success">{change.key}: {String(change.old_value ?? "?")} → {String(change.new_value)} {change.reason ? `— ${change.reason}` : ""}</li>)}</ul></div>}
        {last.spawn_requests?.length > 0 && <div><div className="font-medium">Спавн:</div><ul className="mt-1 space-y-1">{last.spawn_requests.map((request, index) => <li key={index} className="font-mono text-[11px] text-info">{request.species} x{request.count} ({request.position_hint}) {request.reason ? `— ${request.reason}` : ""}</li>)}</ul></div>}
        {last.rejected?.length > 0 && <div><div className="font-medium text-warning">Отклонено валидатором:</div><ul className="mt-1 space-y-1">{last.rejected.map((reason, index) => <li key={index} className="font-mono text-[11px] text-warning">{reason}</li>)}</ul></div>}
      </div>}
      {history.length > 0 && <div className="space-y-1 text-[11px] text-muted-foreground">
        <div className="font-medium text-foreground">История последних запусков</div>
        {history.slice().reverse().map((entry, index) => <div key={index} className="flex flex-wrap gap-2"><span className="font-mono">{entry.at.slice(0, 16).replace("T", " ")}</span><Badge variant="outline" className="text-[9px]">{entry.mode}</Badge><span>применено {entry.applied}, отклонено {entry.rejected}</span><span className="truncate">{entry.summary}</span></div>)}
      </div>}
    </CardContent></Card>
}

export function MonsterSpawnPage() {
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()
  const [server, setServer] = useState("ASPER1")
  const [draft, setDraft] = useState<MonsterSpawnState | null>(null)
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null)
  const [selectedWave, setSelectedWave] = useState(0)
  const [placing, setPlacing] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [planSearch, setPlanSearch] = useState("")
  const [planStatus, setPlanStatus] = useState<PlanStatus>("all")
  const [showLegend, setShowLegend] = useState(true)
  const [contextMenu, setContextMenu] = useState<{ screenX: number; screenY: number; wx: number; wy: number; markerId: string | null } | null>(null)
  const [activeTab, setActiveTab] = useState("overview")

  const rounds = useQuery({ queryKey: ["world-rounds"], queryFn: worldStateApi.listRounds, refetchInterval: 5000 })
  const round = rounds.data?.rounds.find((item) => item.server_name?.toUpperCase() === server) ?? null
  const geometry = useQuery({ queryKey: ["world-geometry", round?.round_id], queryFn: () => worldStateApi.getGeometry(round!.round_id), enabled: !!round, staleTime: Infinity })
  const world = useQuery({ queryKey: ["world-state", round?.round_id], queryFn: () => worldStateApi.getState(round!.round_id), enabled: !!round, refetchInterval: 3000 })
  const state = useQuery({ queryKey: ["monster-spawn", server], queryFn: () => monsterSpawnApi.get(server), refetchInterval: 5000 })
  const analysis = useQuery({ queryKey: ["monster-spawn-analysis", server], queryFn: () => monsterSpawnApi.analysis(server), refetchInterval: 15000, staleTime: 5000 })

  useEffect(() => { if (state.data && !dirty) setDraft(clone(state.data)) }, [state.data, dirty])
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
  const heldVanilla = telemetryList<HeldVanillaTelemetry>(telemetry.held_vanilla_events)
  const pendingWaveDetails = telemetryList<PendingWaveTelemetry>(telemetry.pending_wave_details)
  const plannedAction: PlannedActionTelemetry = isRecord(telemetry.planned_action)
    ? {
        action: String(telemetry.planned_action.action ?? "unknown"),
        probability: Number(telemetry.planned_action.probability ?? 0),
        detail: String(telemetry.planned_action.detail ?? ""),
        next_spawn_possible_at: Number(telemetry.planned_action.next_spawn_possible_at ?? 0),
      }
    : { action: "unknown", probability: 0, detail: "Плагин ещё не прислал план действий", next_spawn_possible_at: 0 }
  const global = isRecord(draft.config.global) ? draft.config.global : {}
  const vanilla = isRecord(global.vanilla_events) ? global.vanilla_events : {}
  const report: MonsterSpawnAnalysis = analysis.data ?? { generated_at: "", read_only: true, global_performance_ema: 1, wave_logs: [], ship_logs: [], recommendations: [{ id: "loading", severity: "info", title: "Собираю рекомендации", reason: "Анализ памяти контроллера выполняется автоматически.", evidence: {}, suggested_adjustment: "Не менять настройки до загрузки анализа.", confidence: 0, read_only: true }] }
  const intensityPercent = Math.max(0, Math.min(100, dashboard.intensity * 100))
  const thresholdPercent = Math.max(0, Math.min(100, dashboard.event_threshold * 100))
  const waveLogs = report.wave_logs
  const shipLogs = report.ship_logs
  const selectedPlan = draft.plans.find((plan) => plan.id === selectedPlanId) ?? null
  const markers: WorldMarker[] = draft.plans.map((plan) => ({ id: plan.id, x: plan.x, y: plan.y, label: plan.wave_name || plan.name, color: plan.source === "manual" ? "#59D6C2" : "#F6C453", muted: plan.status === "cancelled" }))
  const waveChart = { labels: waveLogs.slice(0, 8).map((item) => item.name.replace("vanilla:", "")), datasets: [{ label: "Среднее время зачистки, с", data: waveLogs.slice(0, 8).map((item) => Math.round(item.avg_repel_seconds)), backgroundColor: CHART_COLORS[0], ...barDatasetDefaults }] }
  const shipChart = { labels: shipLogs.slice(0, 8).map((item) => item.name), datasets: [{ label: "Performance EMA", data: shipLogs.slice(0, 8).map((item) => item.performance_ema), backgroundColor: CHART_COLORS[1], ...barDatasetDefaults }] }
  const trendValues = shipLogs.flatMap((item) => item.recent_performance).slice(-24)
  const trendChart = { labels: trendValues.map((_, index) => `${index + 1}`), datasets: [{ label: "Последние наблюдения", data: trendValues, borderColor: CHART_COLORS[2], backgroundColor: `${CHART_COLORS[2]}28`, fill: true, ...lineDatasetDefaults }] }

  const setGlobal = (key: string, value: unknown) => updateDraft((next) => { next.config.global[key] = value })
  const setVanilla = (key: string, value: unknown) => updateDraft((next) => { const current = isRecord(next.config.global.vanilla_events) ? next.config.global.vanilla_events : {}; next.config.global.vanilla_events = { ...current, [key]: value } })
  const createPlan = (x: number, y: number) => updateDraft((next) => { const wave = clone(next.config.waves[selectedWave] ?? emptyWave()); const plan: SpawnPlan = { ...wave, id: crypto.randomUUID(), wave_name: wave.name, source: "manual", status: "planned", x, y, note: "" }; next.plans.push(plan); setSelectedPlanId(plan.id); setPlacing(false) })
  const duplicatePlan = (id: string) => updateDraft((next) => { const source = next.plans.find((plan) => plan.id === id); if (!source) return; const duplicate: SpawnPlan = { ...clone(source), id: crypto.randomUUID(), note: source.note ? `${source.note} (копия)` : "" }; next.plans.push(duplicate); setSelectedPlanId(duplicate.id); toast.success("План продублирован") })
  const copyCoords = (x: number, y: number) => navigator.clipboard.writeText(`${x}, ${y}`).then(() => toast.success(`Координаты ${x}, ${y} скопированы`)).catch(() => toast.error("Не удалось скопировать"))

  return <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-4">
    <PageHeader title="Управление монстрами" description="Ванильный pipeline под контролем MonsterSpawnController: наблюдай, корректируй, не применяй вслепую" icon={Skull} actions={<div className="flex gap-2"><Select value={server} onValueChange={setServer}><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent>{["ASPER1", "ASPER2"].map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select><Button disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save className="size-4" /> Сохранить</Button></div>} />
    <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card px-3 py-2 text-xs"><Badge variant={dashboard.round_active ? "default" : "outline"}>{dashboard.round_active ? "Раунд активен" : "Ожидание раунда"}</Badge><Badge variant={draft.pending ? "destructive" : "secondary"}>{draft.pending ? `Ожидает применения r${draft.desired_revision}` : `Применено r${draft.applied_revision}`}</Badge><span className="text-muted-foreground">Vanilla pipeline: <b className="text-foreground">{String(vanilla.mode ?? "controller")}</b></span>{dirty && <Badge variant="outline">Есть несохранённые изменения</Badge>}<span className="ml-auto text-muted-foreground">Последний контакт: {draft.last_seen_at ? formatBrowserDate(draft.last_seen_at) : "нет данных"}</span></div>

    <Tabs value={activeTab} onValueChange={setActiveTab} className="min-h-0">
      <TabsList className="grid h-auto w-full grid-cols-3 gap-1 bg-muted/60 p-1 sm:grid-cols-6"><TabsTrigger value="overview" className="gap-2 py-2"><Activity className="size-4" /> Обзор</TabsTrigger><TabsTrigger value="monitoring" className="gap-2 py-2"><Radar className="size-4" /> Мониторинг</TabsTrigger><TabsTrigger value="llm" className="gap-2 py-2"><Bot className="size-4" /> LLM-ассистент</TabsTrigger><TabsTrigger value="control" className="gap-2 py-2"><SlidersHorizontal className="size-4" /> Визуальное управление</TabsTrigger><TabsTrigger value="logs" className="gap-2 py-2"><TrendingUp className="size-4" /> Логи и анализ</TabsTrigger><TabsTrigger value="manual" className="gap-2 py-2"><Target className="size-4" /> Ручная настройка</TabsTrigger></TabsList>

      <TabsContent value="overview" className="space-y-4 pt-2">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric icon={ShipWheel} label="Корабль раунда" value={dashboard.submarine_name || "Нет активного раунда"} detail={`${Math.floor((dashboard.round_seconds || 0) / 60)} мин · ${dashboard.known_submarines} кораблей в памяти`} tone="primary" /><Metric icon={Gauge} label="Текущее давление" value={`${intensityPercent.toFixed(0)}%`} detail={`Порог vanilla event ${thresholdPercent.toFixed(0)}%`} tone="warning" /><Metric icon={BrainCircuit} label="Global performance EMA" value={report.global_performance_ema.toFixed(2)} detail="Больше 1.0: экипаж справляется быстрее ожиданий" tone="success" /><Metric icon={Anchor} label="Ожидают триггера" value={String(dashboard.pending_waves)} detail={`${telemetry.vanilla_events ?? 0} vanilla events · ${draft.config.waves.length} волн в пуле`} tone="info" /></div>
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,.65fr)]"><ChartCard title="Наблюдаемая интенсивность" description="Текущая интенсивность и порог, полученные от плагина"><Line options={baseChartOptions} data={{ labels: ["Сейчас"], datasets: [{ label: "Интенсивность", data: [dashboard.intensity], borderColor: CHART_COLORS[0], backgroundColor: `${CHART_COLORS[0]}28`, fill: true, ...lineDatasetDefaults }, { label: "Порог события", data: [dashboard.event_threshold], borderColor: CHART_COLORS[3], ...lineDatasetDefaults }] }} /></ChartCard>
          <Card className="overflow-hidden py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="flex items-center gap-2 text-sm"><Radar className="size-4 text-destructive" /> Волны под атакой и недавние</CardTitle><CardDescription className="text-xs">Активные и недавно побеждённые волны вместо статических предложений</CardDescription></CardHeader><CardContent className="max-h-96 space-y-2 overflow-y-auto p-4">
            {activeWaves.length > 0 ? activeWaves.map((wave) => <ActiveWaveCard key={wave.wave_id} wave={wave} />) : <div className="rounded-xl border border-dashed p-4 text-center text-xs text-muted-foreground">Активных волн нет — корабль вне боя</div>}
            {recentWaves.slice(0, 4).map((wave) => <RecentWaveCard key={wave.wave_id + String(wave.resolved_at_seconds)} wave={wave} />)}
            {!activeWaves.length && !recentWaves.length && <Button variant="ghost" className="w-full justify-between" onClick={() => setActiveTab("logs")}>Открыть полный анализ <ChevronRight className="size-4" /></Button>}
          </CardContent></Card></div>
        <div className="grid gap-4 lg:grid-cols-2"><ChartCard title="Самые долгие волны" description="Среднее время от спавна до разрешения"><Bar options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, legend: { display: false } } }} data={waveChart} /></ChartCard><ChartCard title="Корабли по эффективности" description="Ниже 1.0 означает более тяжёлое прохождение"><Bar options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, legend: { display: false } } }} data={shipChart} /></ChartCard></div>
        <Card className="overflow-hidden py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="flex items-center gap-2 text-sm"><MapPin className="size-4 text-primary" /> Карта раунда</CardTitle><CardDescription className="text-xs">Карта текущего раунда с планами спавна из ручной настройки</CardDescription></CardHeader><CardContent className="p-4"><div className="relative min-h-[380px] overflow-hidden rounded-xl border">{geometry.data && world.data ? <WorldCanvas geometry={geometry.data} state={world.data} selectedCharacterId={null} onSelectCharacter={() => {}} targetMode={false} markers={markers} selectedMarkerId={selectedPlanId} onSelectMarker={(id) => { setSelectedPlanId(id); setActiveTab("manual") }} onMoveMarker={() => {}} /> : <div className="flex h-full min-h-[380px] items-center justify-center p-8 text-center text-sm text-muted-foreground">Для {server} нет активной карты раунда</div>}</div></CardContent></Card>
        <ExpressTuning global={global} vanilla={vanilla} setGlobal={setGlobal} setVanilla={setVanilla} />
      </TabsContent>

      <TabsContent value="monitoring" className="space-y-4 pt-2">
        <PlannedActionCard planned={plannedAction} />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric icon={TrendingUp} label="Темп продвижения" value={`${Number(telemetry.pace_ratio ?? 1).toFixed(2)}x`} detail={telemetry.pace_anomalous ? "Аномальный — перехват возможен" : "Нормальный"} tone={telemetry.pace_anomalous ? "warning" : "success"} />
          <Metric icon={SlidersHorizontal} label="Задержка спавна" value={`${Number(telemetry.struggle_delay_factor ?? 1).toFixed(2)}x`} detail={telemetry.single_wave_lock ? "Замок: волна уже в бою" : "Штатный режим"} tone={Number(telemetry.struggle_delay_factor ?? 1) > 1.2 ? "warning" : "primary"} />
          <Metric icon={BrainCircuit} label="Изучено волн" value={String(telemetry.learned_waves_count ?? 0)} detail={`${telemetry.pace_intercepts_this_round ?? 0}/${telemetry.pace_intercept_max ?? 0} перехватов за раунд`} tone="info" />
          <Metric icon={Radar} label="Ванильные события" value={String(telemetry.vanilla_events ?? 0)} detail={`${telemetry.pending_waves ?? 0} волн ждут триггера · режим ${telemetry.vanilla_event_mode ?? "controller"}`} tone="primary" />
        </div>
        <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Активные волны</CardTitle></CardHeader><CardContent className="space-y-2 p-4">
          {activeWaves.length ? activeWaves.map((wave) => <ActiveWaveCard key={wave.wave_id} wave={wave} />) : <div className="rounded-xl border border-dashed p-6 text-center text-xs text-muted-foreground">Нет активных волн</div>}
        </CardContent></Card>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Изученные ванильные волны</CardTitle><CardDescription className="text-xs">Автоматически добавляются в пул возможных волн</CardDescription></CardHeader><CardContent className="p-4"><div className="overflow-x-auto rounded-xl border"><table className="w-full min-w-[520px] text-left text-xs"><thead className="bg-muted/30 text-muted-foreground"><tr><th className="px-3 py-2 font-medium">Волна</th><th className="px-3 py-2 font-medium">Вид</th><th className="px-3 py-2 font-medium">Наблюдений</th><th className="px-3 py-2 font-medium">В пуле</th></tr></thead><tbody>{learnedWaves.map((wave) => <tr key={wave.wave_name} className="border-t"><td className="px-3 py-2 font-mono">{wave.wave_name}</td><td className="px-3 py-2">{wave.species}</td><td className="px-3 py-2">{wave.times_seen}</td><td className="px-3 py-2">{wave.registered_in_pool ? <Check className="size-3.5 text-success" /> : <X className="size-3.5 text-muted-foreground" />}</td></tr>)}{!learnedWaves.length && <tr><td colSpan={4} className="px-3 py-8 text-center text-muted-foreground">Пока ничего не изучено</td></tr>}</tbody></table></div></CardContent></Card>
          <Card className="py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="text-sm">Оценка угрозы видов</CardTitle><CardDescription className="text-xs">&gt;1.0 — вид опасен для экипажа, частота спавна повышается</CardDescription></CardHeader><CardContent className="p-4"><div className="overflow-x-auto rounded-xl border"><table className="w-full min-w-[420px] text-left text-xs"><thead className="bg-muted/30 text-muted-foreground"><tr><th className="px-3 py-2 font-medium">Вид</th><th className="px-3 py-2 font-medium">Угроза</th><th className="px-3 py-2 font-medium">Наблюдений</th></tr></thead><tbody>{speciesThreats.map((threat) => <tr key={threat.species} className="border-t"><td className="px-3 py-2 font-medium">{threat.species}</td><td className={`px-3 py-2 font-mono ${threat.threat_ema >= 1.2 ? "text-warning" : threat.threat_ema <= 0.8 ? "text-success" : ""}`}>{threat.threat_ema.toFixed(2)}</td><td className="px-3 py-2">{threat.samples}</td></tr>)}{!speciesThreats.length && <tr><td colSpan={3} className="px-3 py-8 text-center text-muted-foreground">Данных ещё нет</td></tr>}</tbody></table></div></CardContent></Card>
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
        <LLMPanel server={server} />
      </TabsContent>

      <TabsContent value="control" className="space-y-4 pt-2">
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]"><Card className="overflow-hidden py-0"><CardHeader className="border-b bg-muted/20 px-4 py-3"><CardTitle className="flex items-center gap-2 text-sm"><CircleGauge className="size-4 text-primary" /> Базовая модель сложности</CardTitle><CardDescription className="text-xs">Ползунки меняют только конфиг. Нажми «Сохранить», чтобы отправить новую revision.</CardDescription></CardHeader><CardContent className="grid gap-3 p-4 sm:grid-cols-2"><SliderField label="Количество vanilla монстров" value={Number(vanilla.amount_multiplier ?? 1)} min={0} max={3} step={0.05} suffix="x" onChange={(value) => setVanilla("amount_multiplier", value)} /><SliderField label="Сила vanilla события" value={Number(vanilla.difficulty_multiplier ?? 1)} min={0.5} max={2.5} step={0.05} suffix="x" onChange={(value) => setVanilla("difficulty_multiplier", value)} /><SliderField label="Влияние adaptive-memory на HP" value={Number(vanilla.adaptive_influence ?? 1)} min={0} max={1} step={0.05} suffix="" onChange={(value) => setVanilla("adaptive_influence", value)} /><SliderField label="Влияние adaptive-memory на amount" value={Number(vanilla.adaptive_amount_influence ?? 0)} min={0} max={1} step={0.05} suffix="" onChange={(value) => setVanilla("adaptive_amount_influence", value)} /><SliderField label="Порог интенсивности" value={Number(global.intensity_threshold ?? 0.2)} min={0} max={1} step={0.01} onChange={(value) => setGlobal("intensity_threshold", value)} /><SliderField label="Минимальная пауза между спавнами" value={Number(global.min_seconds_between_spawns ?? 90)} min={0} max={600} step={5} suffix="с" onChange={(value) => setGlobal("min_seconds_between_spawns", value)} /><SliderField label="Целевая сложность в минуту" value={Number(global.target_difficulty_per_minute ?? 0.8)} min={0} max={3} step={0.05} onChange={(value) => setGlobal("target_difficulty_per_minute", value)} /><SliderField label="Порог орды простоя" value={Number(global.idle_spawn_after_seconds ?? 240)} min={0} max={1200} step={30} suffix="с" onChange={(value) => setGlobal("idle_spawn_after_seconds", value)} /></CardContent></Card>
          <div className="space-y-4"><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Режим vanilla pipeline</CardTitle><CardDescription className="text-xs">События выбирает игра, фактический спавн контролирует режим.</CardDescription></CardHeader><CardContent className="space-y-3"><Select value={String(vanilla.mode ?? "controller")} onValueChange={(value) => setVanilla("mode", value)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="controller">controller · анализ и корреляция</SelectItem><SelectItem value="native">native · штатный спавн</SelectItem><SelectItem value="disabled">disabled · заблокировать</SelectItem></SelectContent></Select><div className="rounded-lg bg-muted/30 p-3 text-xs text-muted-foreground"><div className="mb-1 flex items-center gap-2 text-foreground"><Info className="size-3.5 text-info" /> Безопасный принцип</div>Анализатор не меняет параметры сам. Все предложения остаются на этой странице до ручного подтверждения оператором.</div></CardContent></Card><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Предохранители</CardTitle></CardHeader><CardContent className="space-y-3"><div className="flex items-center justify-between gap-3 text-xs"><span>Intensity gate</span><Switch checked={global.intensity_gate_enabled !== false} onCheckedChange={(value) => setGlobal("intensity_gate_enabled", value)} /></div><div className="flex items-center justify-between gap-3 text-xs"><span>Adaptive-memory</span><Switch checked={global.adaptive_memory_enabled !== false} onCheckedChange={(value) => setGlobal("adaptive_memory_enabled", value)} /></div><div className="flex items-center justify-between gap-3 text-xs"><span>Уважать server monster settings</span><Switch checked={global.respect_server_monster_settings !== false} onCheckedChange={(value) => setGlobal("respect_server_monster_settings", value)} /></div><div className="flex items-center justify-between gap-3 text-xs"><span>Swarm behavior</span><Switch checked={global.use_swarm_behavior !== false} onCheckedChange={(value) => setGlobal("use_swarm_behavior", value)} /></div></CardContent></Card></div></div>
        <Card><CardHeader className="pb-3"><CardTitle className="text-sm">Как читать регуляторы</CardTitle></CardHeader><CardContent className="grid gap-3 text-xs text-muted-foreground md:grid-cols-3"><div><b className="text-foreground">Amount</b><br />Масштабирует ванильное количество. Значение 1.0 сохраняет исходное поведение.</div><div><b className="text-foreground">Difficulty</b><br />Масштабирует HP и difficulty наблюдаемой волны, не меняя vanilla event selection.</div><div><b className="text-foreground">Adaptive</b><br />Подмешивает накопленную эффективность экипажей и кораблей в будущие vanilla события.</div></CardContent></Card>
      </TabsContent>

      <TabsContent value="logs" className="space-y-4 pt-2"><div className="grid gap-4 sm:grid-cols-3"><Metric icon={TrendingUp} label="Завершено волн" value={String(waveLogs.reduce((sum, item) => sum + item.times_spawned, 0))} detail={`${waveLogs.length} типа событий`} tone="primary" /><Metric icon={TrendingDown} label="Средняя скорость" value={waveLogs.length ? `${Math.round(waveLogs.filter((item) => item.avg_repel_seconds).reduce((sum, item) => sum + item.avg_repel_seconds, 0) / Math.max(1, waveLogs.filter((item) => item.avg_repel_seconds).length))} с` : "—"} detail="Среднее по типам волн" tone="warning" /><Metric icon={BrainCircuit} label="Наблюдений по кораблям" value={String(shipLogs.reduce((sum, item) => sum + item.times_spawned, 0))} detail="Используются в корреляции" tone="success" /></div><div className="grid gap-4 lg:grid-cols-2"><ChartCard title="Performance trajectory" description="Последние доступные значения из adaptive-memory"><Line options={baseChartOptions} data={trendChart} /></ChartCard><ChartCard title="Efficiency by ship" description="Сравнение кораблей по performance EMA"><Bar options={{ ...baseChartOptions, plugins: { ...baseChartOptions.plugins, legend: { display: false } } }} data={shipChart} /></ChartCard></div><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Лог разрешения волн</CardTitle><CardDescription className="text-xs">Как быстро экипажи закрывали волны и какой ущерб получили</CardDescription></CardHeader><CardContent><WaveLogs logs={waveLogs} /></CardContent></Card><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Эффективность по кораблям</CardTitle><CardDescription className="text-xs">Детализация для сравнения дизайна субмарин</CardDescription></CardHeader><CardContent><ShipLogs logs={shipLogs} /></CardContent></Card><Card><CardHeader className="pb-3"><CardTitle className="flex items-center gap-2 text-sm"><Sparkles className="size-4 text-warning" /> Автоматический анализ и предложения</CardTitle><CardDescription className="text-xs">Сгенерировано {report.generated_at ? formatBrowserDate(report.generated_at) : "сейчас"}. Рекомендации информационные и не применяются автоматически.</CardDescription></CardHeader><CardContent className="grid gap-3 lg:grid-cols-2">{report.recommendations.map((item) => <Recommendation key={item.id} item={item} />)}</CardContent></Card></TabsContent>

      <TabsContent value="manual" className="space-y-4 pt-2"><div className="rounded-xl border border-primary/30 bg-primary/5 p-3 text-xs text-muted-foreground"><div className="flex items-center gap-2 font-medium text-foreground"><Target className="size-4 text-primary" /> Полная ручная настройка</div><p className="mt-1">Здесь доступны планы, волны и сырой JSON без ограничений визуального режима. Изменения не попадут на сервер до нажатия «Сохранить».</p></div><Tabs defaultValue="plans"><TabsList className="grid w-full grid-cols-3"><TabsTrigger value="plans">Планы · {draft.plans.length}</TabsTrigger><TabsTrigger value="waves">Волны · {draft.config.waves.length}</TabsTrigger><TabsTrigger value="json">Raw JSON</TabsTrigger></TabsList><TabsContent value="plans" className="space-y-4 pt-3"><div className="grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(380px,.75fr)]"><div className="relative min-h-[480px] overflow-hidden rounded-xl border">{geometry.data && world.data ? <WorldCanvas geometry={geometry.data} state={world.data} selectedCharacterId={null} onSelectCharacter={() => {}} targetMode={placing} onPickTarget={createPlan} markers={markers} selectedMarkerId={selectedPlanId} onSelectMarker={setSelectedPlanId} onMoveMarker={(id, x, y) => updateDraft((next) => { const plan = next.plans.find((item) => item.id === id); if (plan) { plan.x = x; plan.y = y } })} onMapContextMenu={(info) => { setContextMenu({ ...info }); if (info.markerId) setSelectedPlanId(info.markerId) }} /> : <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">Для {server} нет активной карты раунда<br /><span className="text-xs">Планы можно редактировать справа</span></div>}<div className="absolute bottom-3 left-3 flex flex-wrap gap-2"><Button variant={placing ? "destructive" : "secondary"} onClick={() => setPlacing(!placing)}>{placing ? <X className="size-4" /> : <Crosshair className="size-4" />}{placing ? "Отмена" : "Поставить точку"}</Button><Button variant="outline" size="icon" title={showLegend ? "Скрыть легенду" : "Показать легенду"} onClick={() => setShowLegend(!showLegend)}>{showLegend ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</Button></div>{showLegend && <div className="absolute right-3 top-3 rounded-lg border bg-background/90 p-2 text-[11px] shadow"><div className="mb-1 flex items-center gap-1 font-medium"><Layers className="size-3" /> Легенда</div><div><span className="mr-1 inline-block size-2 rounded-full bg-[#59D6C2]" />Ручной</div><div><span className="mr-1 inline-block size-2 rounded-full bg-[#F6C453]" />Авто</div></div>}</div><Card className="min-h-0 overflow-hidden py-0"><CardContent className="h-full overflow-y-auto p-4"><div className="flex gap-2"><div className="relative flex-1"><Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-7" placeholder="Поиск по планам" value={planSearch} onChange={(event) => setPlanSearch(event.target.value)} /></div><Select value={planStatus} onValueChange={(value) => setPlanStatus(value as PlanStatus)}><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все</SelectItem><SelectItem value="planned">Запланированы</SelectItem><SelectItem value="cancelled">Отменены</SelectItem><SelectItem value="spawned">Заспавнены</SelectItem></SelectContent></Select></div><div className="mt-3 flex gap-2"><Select value={String(selectedWave)} onValueChange={(value) => setSelectedWave(Number(value))}><SelectTrigger className="flex-1"><SelectValue /></SelectTrigger><SelectContent>{draft.config.waves.map((wave, index) => <SelectItem key={index} value={String(index)}>{wave.name}</SelectItem>)}</SelectContent></Select><Button variant="outline" onClick={() => createPlan(0, 0)}><Plus className="size-4" /> Создать</Button></div><div className="mt-3 max-h-64 space-y-1 overflow-y-auto pr-1">{filteredPlans.map((plan) => <button key={plan.id} className={`flex w-full items-center gap-2 rounded-lg border p-2.5 text-left transition ${plan.id === selectedPlanId ? "border-primary bg-accent" : "hover:bg-muted/50"}`} onClick={() => setSelectedPlanId(plan.id)}><span className={`size-2 shrink-0 rounded-full ${plan.status === "cancelled" ? "bg-muted-foreground/40" : plan.source === "manual" ? "bg-[#59D6C2]" : "bg-[#F6C453]"}`} /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{plan.wave_name || plan.name}</span><span className="block truncate text-xs text-muted-foreground">{Math.round(plan.x)}, {Math.round(plan.y)} · {plan.status}</span></span><Badge variant="secondary" className="text-[10px]">{plan.monsters.length} мобов</Badge></button>)}{!filteredPlans.length && <div className="py-6 text-center text-xs text-muted-foreground"><Filter className="mx-auto mb-2 size-4" />Планов не найдено</div>}</div>{selectedPlan && <div className="mt-4 space-y-3 rounded-xl border p-3"><WaveEditor wave={selectedPlan} onChange={(wave) => updateDraft((next) => { const index = next.plans.findIndex((plan) => plan.id === selectedPlan.id); next.plans[index] = { ...next.plans[index], ...wave, wave_name: wave.name } })} /><div className="flex flex-wrap gap-2"><Button variant={selectedPlan.status === "cancelled" ? "secondary" : "destructive"} onClick={() => updateDraft((next) => { const plan = next.plans.find((item) => item.id === selectedPlan.id)!; plan.status = plan.status === "cancelled" ? "planned" : "cancelled" })}>{selectedPlan.status === "cancelled" ? "Вернуть план" : "Отменить спавн"}</Button><Button variant="outline" onClick={() => duplicatePlan(selectedPlan.id)}><Copy className="size-3.5" /> Дублировать</Button><Button variant="ghost" onClick={() => updateDraft((next) => { next.plans = next.plans.filter((plan) => plan.id !== selectedPlan.id); setSelectedPlanId(null) })}><Trash2 className="size-3.5" /> Удалить</Button><Button variant="outline" onClick={() => copyCoords(selectedPlan.x, selectedPlan.y)}><MapPin className="size-3.5" /> Коорд.</Button></div></div>}</CardContent></Card></div></TabsContent><TabsContent value="waves" className="space-y-3 pt-3"><div className="flex gap-2"><Select value={String(selectedWave)} onValueChange={(value) => setSelectedWave(Number(value))}><SelectTrigger className="max-w-sm flex-1"><SelectValue /></SelectTrigger><SelectContent>{draft.config.waves.map((wave, index) => <SelectItem key={index} value={String(index)}>{wave.name}</SelectItem>)}</SelectContent></Select><Button variant="outline" onClick={() => updateDraft((next) => { next.config.waves.push(emptyWave()); setSelectedWave(next.config.waves.length - 1) })}><Plus className="size-4" /></Button><Button variant="ghost" disabled={draft.config.waves.length < 2} onClick={() => updateDraft((next) => { next.config.waves.splice(selectedWave, 1); setSelectedWave(0) })}><Trash2 className="size-4" /></Button></div>{draft.config.waves[selectedWave] && <Card><CardContent className="p-4"><WaveEditor wave={draft.config.waves[selectedWave]} onChange={(wave) => updateDraft((next) => { next.config.waves[selectedWave] = wave })} /></CardContent></Card>}</TabsContent><TabsContent value="json" className="space-y-3 pt-3"><div className="grid gap-4 xl:grid-cols-2"><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Полный config</CardTitle></CardHeader><CardContent><Textarea className="min-h-[620px] font-mono text-xs" value={JSON.stringify(draft.config, null, 2)} onChange={(event) => { try { const parsed = JSON.parse(event.target.value); updateDraft((next) => { next.config = parsed }) } catch { /* keep last valid JSON */ } }} /></CardContent></Card><Card><CardHeader className="pb-3"><CardTitle className="text-sm">Memory</CardTitle><CardDescription className="text-xs">Редактирование защищено отдельным permission.</CardDescription></CardHeader><CardContent><Textarea className="min-h-[620px] font-mono text-xs" disabled={!canDo(admin, "monster_spawn", "manage_memory")} value={JSON.stringify(draft.memory, null, 2)} onChange={(event) => { try { const parsed = JSON.parse(event.target.value); updateDraft((next) => { next.memory = parsed }) } catch { /* keep last valid JSON */ } }} /></CardContent></Card></div></TabsContent></Tabs></TabsContent>
    </Tabs>
    {contextMenu && <div className="fixed z-50 min-w-52 rounded-xl border bg-popover p-1 text-sm shadow-xl" style={{ left: Math.min(contextMenu.screenX, window.innerWidth - 230), top: Math.min(contextMenu.screenY, window.innerHeight - 240) }} onClick={(event) => event.stopPropagation()}><div className="px-2 py-1 text-[11px] text-muted-foreground">{contextMenu.wx}, {contextMenu.wy}</div>{contextMenu.markerId ? <><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { setSelectedPlanId(contextMenu.markerId); setContextMenu(null) }}><LocateFixed className="size-3.5" /> Редактировать</button><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { duplicatePlan(contextMenu.markerId!); setContextMenu(null) }}><Files className="size-3.5" /> Дублировать</button><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { copyCoords(contextMenu.wx, contextMenu.wy); setContextMenu(null) }}><Copy className="size-3.5" /> Копировать координаты</button><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { updateDraft((next) => { const plan = next.plans.find((item) => item.id === contextMenu.markerId); if (plan) plan.status = plan.status === "cancelled" ? "planned" : "cancelled" }); setContextMenu(null) }}>{draft.plans.find((plan) => plan.id === contextMenu.markerId)?.status === "cancelled" ? <><Eye className="size-3.5" /> Вернуть</> : <><EyeOff className="size-3.5" /> Отменить</>}</button></> : <><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { createPlan(contextMenu.wx, contextMenu.wy); setContextMenu(null) }}><Plus className="size-3.5" /> Создать план здесь</button><button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent" onClick={() => { copyCoords(contextMenu.wx, contextMenu.wy); setContextMenu(null) }}><Copy className="size-3.5" /> Копировать координаты</button></>}</div>}
  </div>
}
