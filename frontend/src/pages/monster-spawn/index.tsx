import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Crosshair, Plus, Save, Skull, Trash2, X, Search, Copy, MapPin, Layers, Eye, EyeOff, Filter, Clipboard, Files, LocateFixed } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { PageHeader } from "@/components/layout/PageHeader"
import { canDo, useCurrentAdmin } from "@/lib/auth"
import { formatBrowserDate } from "@/lib/format-date"
import { worldStateApi } from "@/pages/world-overview/api"
import { WorldCanvas, type WorldMarker } from "@/pages/world-overview/WorldCanvas"
import { monsterSpawnApi, type MonsterSpawnState, type SpawnPlan, type SpawnWave } from "./api"

const emptyWave = (): SpawnWave => ({
  name: "Новая волна", min_progress: 0, max_progress: 0, min_round_time: 0,
  min_players: 0, max_sub_health: 0, max_crew_health: 0, chance: 1,
  spawn_type: "around", difficulty: -1,
  monsters: [{ species: "crawler", count: 1, scale_with_players: 0, hp_multiplier: 1, hp_scale_with_progress: 0, mobility: 1 }],
})
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const number = (value: string) => Number.isFinite(Number(value)) ? Number(value) : 0

function NumberField({ label, value, onChange, step = "any" }: { label: string; value: number; onChange: (v: number) => void; step?: string }) {
  return <Label className="space-y-1 text-xs"><span className="text-muted-foreground">{label}</span><Input type="number" step={step} value={value} onChange={(e) => onChange(number(e.target.value))} /></Label>
}

function WaveEditor({ wave, onChange }: { wave: SpawnWave; onChange: (wave: SpawnWave) => void }) {
  const set = <K extends keyof SpawnWave>(key: K, value: SpawnWave[K]) => onChange({ ...wave, [key]: value })
  return <div className="space-y-4">
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Label className="col-span-2 space-y-1 text-xs"><span className="text-muted-foreground">Название</span><Input value={wave.name} onChange={(e) => set("name", e.target.value)} /></Label>
      <Label className="space-y-1 text-xs"><span className="text-muted-foreground">Позиция</span><Select value={wave.spawn_type} onValueChange={(v) => set("spawn_type", v)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="around">Вокруг</SelectItem><SelectItem value="ahead">Впереди</SelectItem><SelectItem value="below">Снизу</SelectItem></SelectContent></Select></Label>
      <NumberField label="Сложность (-1 = авто)" value={wave.difficulty} onChange={(v) => set("difficulty", v)} />
      <NumberField label="Прогресс от" value={wave.min_progress} onChange={(v) => set("min_progress", v)} step="0.05" />
      <NumberField label="Прогресс до" value={wave.max_progress} onChange={(v) => set("max_progress", v)} step="0.05" />
      <NumberField label="Время раунда от, с" value={wave.min_round_time} onChange={(v) => set("min_round_time", v)} />
      <NumberField label="Игроков от" value={wave.min_players} onChange={(v) => set("min_players", v)} />
      <NumberField label="Здоровье субмарины до, %" value={wave.max_sub_health} onChange={(v) => set("max_sub_health", v)} />
      <NumberField label="Здоровье экипажа до, %" value={wave.max_crew_health} onChange={(v) => set("max_crew_health", v)} />
      <NumberField label="Шанс 0..1" value={wave.chance} onChange={(v) => set("chance", v)} step="0.05" />
    </div>
    <div className="space-y-2">
      <div className="flex items-center justify-between"><h4 className="text-sm font-medium">Состав волны</h4><Button size="sm" variant="outline" onClick={() => set("monsters", [...wave.monsters, emptyWave().monsters[0]])}><Plus className="size-3.5" /> Существо</Button></div>
      {wave.monsters.map((monster, index) => <div key={index} className="grid grid-cols-2 gap-2 rounded-lg border bg-muted/20 p-3 lg:grid-cols-6">
        <Label className="space-y-1 text-xs"><span>Вид</span><Input value={monster.species} onChange={(e) => { const monsters = [...wave.monsters]; monsters[index] = { ...monster, species: e.target.value }; set("monsters", monsters) }} /></Label>
        {(["count", "scale_with_players", "hp_multiplier", "hp_scale_with_progress", "mobility"] as const).map((key) => <NumberField key={key} label={key.replaceAll("_", " ")} value={monster[key]} onChange={(v) => { const monsters = [...wave.monsters]; monsters[index] = { ...monster, [key]: v }; set("monsters", monsters) }} />)}
        <Button className="lg:col-start-6" size="sm" variant="ghost" disabled={wave.monsters.length === 1} onClick={() => set("monsters", wave.monsters.filter((_, i) => i !== index))}><Trash2 className="size-3.5" /> Удалить</Button>
      </div>)}
    </div>
  </div>
}

type PlanStatus = SpawnPlan["status"] | "all"

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

  const rounds = useQuery({ queryKey: ["world-rounds"], queryFn: worldStateApi.listRounds, refetchInterval: 5000 })
  const round = rounds.data?.rounds.find((r) => r.server_name?.toUpperCase() === server) ?? null
  const geometry = useQuery({ queryKey: ["world-geometry", round?.round_id], queryFn: () => worldStateApi.getGeometry(round!.round_id), enabled: !!round, staleTime: Infinity })
  const world = useQuery({ queryKey: ["world-state", round?.round_id], queryFn: () => worldStateApi.getState(round!.round_id), enabled: !!round, refetchInterval: 3000 })
  const state = useQuery({ queryKey: ["monster-spawn", server], queryFn: () => monsterSpawnApi.get(server), refetchInterval: 5000 })

  useEffect(() => { if (state.data && !dirty) setDraft(clone(state.data)) }, [state.data, dirty])
  useEffect(() => { setDirty(false); setSelectedPlanId(null); setDraft(null) }, [server])
  useEffect(() => {
    const onDown = () => setContextMenu(null)
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setContextMenu(null)
    window.addEventListener("click", onDown)
    window.addEventListener("keydown", onEsc)
    return () => { window.removeEventListener("click", onDown); window.removeEventListener("keydown", onEsc) }
  }, [])

  const save = useMutation({
    mutationFn: () => monsterSpawnApi.update(server, { config: draft!.config, plans: draft!.plans, memory: draft!.memory }),
    onSuccess: (next) => { setDraft(clone(next)); setDirty(false); queryClient.setQueryData(["monster-spawn", server], next); toast.success("Изменения отправлены плагину") },
    onError: (e: Error) => toast.error(e.message),
  })
  const updateDraft = (fn: (next: MonsterSpawnState) => void) => { if (!draft) return; const next = clone(draft); fn(next); setDraft(next); setDirty(true) }
  const markers: WorldMarker[] = (draft?.plans ?? []).map((p) => ({ id: p.id, x: p.x, y: p.y, label: p.wave_name || p.name, color: p.source === "manual" ? "#59D6C2" : "#F6C453", muted: p.status === "cancelled" }))
  const selectedPlan = draft?.plans.find((p) => p.id === selectedPlanId) ?? null

  const globalEntries = useMemo(() => Object.entries(draft?.config.global ?? {}), [draft?.config.global])

  const filteredPlans = useMemo(() => {
    if (!draft) return []
    return draft.plans.filter((p) => {
      if (planStatus !== "all" && p.status !== planStatus) return false
      if (planSearch.trim()) {
        const q = planSearch.toLowerCase()
        const hay = `${p.wave_name} ${p.name} ${p.note ?? ""} ${p.x},${p.y} ${p.status}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [draft, planSearch, planStatus])

  if (!draft) return <div><PageHeader title="Управление монстрами" description="Загрузка состояния плагина" icon={Skull} /><div className="h-64 animate-pulse rounded-xl bg-muted" /></div>

  const createPlan = (x: number, y: number) => updateDraft((next) => {
    const wave = clone(next.config.waves[selectedWave] ?? emptyWave())
    const plan: SpawnPlan = { ...wave, id: crypto.randomUUID(), wave_name: wave.name, source: "manual", status: "planned", x, y, note: "" }
    next.plans.push(plan); setSelectedPlanId(plan.id); setPlacing(false)
  })

  const handleMapContextMenu = (info: { wx: number; wy: number; markerId: string | null; screenX: number; screenY: number }) => {
    setContextMenu({ screenX: info.screenX, screenY: info.screenY, wx: info.wx, wy: info.wy, markerId: info.markerId })
    if (info.markerId) setSelectedPlanId(info.markerId)
  }

  const copyCoords = (x: number, y: number) => {
    navigator.clipboard.writeText(`${x}, ${y}`).then(() => toast.success(`Координаты ${x}, ${y} скопированы`)).catch(() => toast.error("Не удалось скопировать"))
  }

  const duplicatePlan = (id: string) => updateDraft((next) => {
    const src = next.plans.find((p) => p.id === id)
    if (!src) return
    const dup: SpawnPlan = { ...clone(src), id: crypto.randomUUID(), note: src.note ? `${src.note} (копия)` : "" }
    next.plans.push(dup); setSelectedPlanId(dup.id); toast.success("План продублирован")
  })

  return <div className="flex h-[calc(100vh-6rem)] flex-col">
    <PageHeader title="Управление монстрами" description="Планы волн, сложность и память MonsterSpawnController — правый клик по карте для быстрого создания/управления" icon={Skull} actions={<div className="flex gap-2"><Select value={server} onValueChange={setServer}><SelectTrigger className="w-36"><SelectValue /></SelectTrigger><SelectContent>{["ASPER1", "ASPER2"].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent></Select><Button disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save className="size-4" /> Сохранить</Button></div>} />
    <div className="mb-3 flex flex-wrap items-center gap-2 text-xs"><Badge variant={draft.pending ? "destructive" : "secondary"}>{draft.pending ? `Ожидает применения r${draft.desired_revision}` : `Применено r${draft.applied_revision}`}</Badge><span className="text-muted-foreground">Плагин: {draft.last_seen_at ? formatBrowserDate(draft.last_seen_at) : "ещё не подключался"} · Планов: {draft.plans.length} · Волн: {draft.config.waves.length}</span>{dirty && <Badge variant="outline">Есть несохранённые изменения</Badge>}<span className="ml-auto hidden items-center gap-1 text-muted-foreground xl:flex"><MapPin className="size-3" /> ПКМ по карте — меню · ЛКМ-перетаскивание — переместить · Колёсико — зум</span></div>
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(420px,.65fr)]">
      <div className="relative min-h-[430px]">
        {geometry.data && world.data ? <WorldCanvas geometry={geometry.data} state={world.data} selectedCharacterId={null} onSelectCharacter={() => {}} targetMode={placing} onPickTarget={createPlan} markers={markers} selectedMarkerId={selectedPlanId} onSelectMarker={setSelectedPlanId} onMoveMarker={(id, x, y) => updateDraft((next) => { const p = next.plans.find((item) => item.id === id); if (p) { p.x = x; p.y = y } })} onMapContextMenu={handleMapContextMenu} /> : <Card className="flex h-full items-center justify-center text-sm text-muted-foreground"><CardContent className="py-10 text-center">Для {server} нет активной карты раунда<br /><span className="text-xs">Планы можно редактировать в таблице справа — карта появится когда раунд запущен</span></CardContent></Card>}
        <div className="absolute bottom-3 left-3 flex flex-wrap gap-2">
          <Button variant={placing ? "destructive" : "secondary"} onClick={() => setPlacing(!placing)}>{placing ? <X className="size-4" /> : <Crosshair className="size-4" />}{placing ? "Отмена" : "Поставить точку (ЛКМ)"}</Button>
          <Button variant="outline" size="icon" title={showLegend ? "Скрыть легенду" : "Показать легенду"} onClick={() => setShowLegend(!showLegend)}>{showLegend ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</Button>
        </div>
        {showLegend && <div className="absolute right-3 top-12 rounded-md border bg-background/90 p-2 text-[11px] shadow">
          <div className="mb-1 flex items-center gap-1 font-medium"><Layers className="size-3" /> Легенда</div>
          <div className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: "#59D6C2" }} /> Ручной план</div>
          <div className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: "#F6C453" }} /> Автоматический</div>
          <div className="flex items-center gap-1.5 opacity-60"><span className="h-2 w-2 rounded-full bg-foreground" /> Отменён</div>
        </div>}
        {contextMenu && (
          <div
            className="fixed z-50 min-w-52 rounded-md border bg-popover p-1 text-sm shadow-md"
            style={{ left: Math.min(contextMenu.screenX, window.innerWidth - 220), top: Math.min(contextMenu.screenY, window.innerHeight - 260) }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-2 py-1 text-[11px] font-medium text-muted-foreground flex items-center gap-1"><MapPin className="size-3" /> {contextMenu.wx}, {contextMenu.wy} {contextMenu.markerId && <Badge variant="secondary" className="ml-auto text-[10px]">метка</Badge>}</div>
            {contextMenu.markerId ? (
              <>
                <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-accent text-left" onClick={() => { setSelectedPlanId(contextMenu.markerId); setContextMenu(null) }}><LocateFixed className="size-3.5" /> Редактировать</button>
                <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-accent text-left" onClick={() => { duplicatePlan(contextMenu.markerId!); setContextMenu(null) }}><Files className="size-3.5" /> Дублировать</button>
                <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-accent text-left" onClick={() => { copyCoords(contextMenu.wx, contextMenu.wy); setContextMenu(null) }}><Copy className="size-3.5" /> Копировать координаты</button>
                <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-accent text-left" onClick={() => { updateDraft((next) => { const p = next.plans.find((x) => x.id === contextMenu.markerId); if (p) p.status = p.status === "cancelled" ? "planned" : "cancelled" }); setContextMenu(null) }}>
                  {draft.plans.find((p) => p.id === contextMenu.markerId)?.status === "cancelled" ? <><Eye className="size-3.5" /> Вернуть план</> : <><EyeOff className="size-3.5" /> Отменить спавн</>}
                </button>
                <div className="my-1 h-px bg-border" />
                <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-accent text-left text-destructive" onClick={() => { updateDraft((next) => { next.plans = next.plans.filter((p) => p.id !== contextMenu.markerId); }); setSelectedPlanId(null); setContextMenu(null); toast.success("План удалён") }}><Trash2 className="size-3.5" /> Удалить</button>
              </>
            ) : (
              <>
                <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-accent text-left" onClick={() => { createPlan(contextMenu.wx, contextMenu.wy); setContextMenu(null); toast.success(`План создан: ${contextMenu.wx}, ${contextMenu.wy}`) }}><Plus className="size-3.5" /> Создать план здесь</button>
                <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 hover:bg-accent text-left" onClick={() => { copyCoords(contextMenu.wx, contextMenu.wy); setContextMenu(null) }}><Clipboard className="size-3.5" /> Копировать координаты</button>
                <div className="px-2 py-1 text-[11px] text-muted-foreground">Волна: {draft.config.waves[selectedWave]?.name ?? "—"}</div>
              </>
            )}
          </div>
        )}
      </div>
      <Card className="min-h-0 overflow-hidden"><CardContent className="h-full overflow-y-auto p-4">
        <Tabs defaultValue="plans">
          <TabsList className="grid w-full grid-cols-5"><TabsTrigger value="plans">Планы {draft.plans.length ? `· ${draft.plans.length}` : ""}</TabsTrigger><TabsTrigger value="waves">Волны</TabsTrigger><TabsTrigger value="difficulty">Сложность</TabsTrigger><TabsTrigger value="memory">Память</TabsTrigger><TabsTrigger value="json">JSON</TabsTrigger></TabsList>
          <TabsContent value="plans" className="space-y-3">
            <div className="flex gap-2">
              <Select value={String(selectedWave)} onValueChange={(v) => setSelectedWave(Number(v))}><SelectTrigger className="w-36"><SelectValue /></SelectTrigger><SelectContent>{draft.config.waves.map((w, i) => <SelectItem key={i} value={String(i)}>{w.name}</SelectItem>)}</SelectContent></Select>
              <Button variant="outline" size="sm" onClick={() => { if (!draft) return; const wx = 0, wy = 0; createPlan(wx, wy); toast.info("План создан в 0,0 — перетащите на карте или ПКМ") }} title="Быстро создать без карты"><Plus className="size-3.5" /> Создать</Button>
            </div>
            <div className="flex gap-2">
              <div className="relative flex-1"><Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-7 h-8 text-sm" placeholder="Поиск по имени/координатам/заметке" value={planSearch} onChange={(e) => setPlanSearch(e.target.value)} /></div>
              <Select value={planStatus} onValueChange={(v) => setPlanStatus(v as PlanStatus)}><SelectTrigger className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все</SelectItem><SelectItem value="planned">Запланированы</SelectItem><SelectItem value="cancelled">Отменены</SelectItem><SelectItem value="spawned">Заспавнены</SelectItem></SelectContent></Select>
            </div>
            <div className="flex flex-wrap gap-1 text-xs">
              <Badge variant="secondary">{filteredPlans.length} показано</Badge>
              {draft.plans.some((p) => p.status === "cancelled") && <Button size="xs" variant="ghost" onClick={() => updateDraft((next) => { next.plans = next.plans.filter((p) => p.status !== "cancelled") })}><Trash2 className="size-3" /> Удалить отменённые</Button>}
              {planSearch && <Button size="xs" variant="ghost" onClick={() => setPlanSearch("")}><X className="size-3" /> Сброс</Button>}
            </div>
            <div className="max-h-64 overflow-y-auto space-y-1 pr-1">
              {filteredPlans.map((p) => <button key={p.id} className={`flex w-full items-center gap-2 rounded-lg border p-2.5 text-left transition ${p.id === selectedPlanId ? "border-primary bg-accent" : "hover:bg-muted/50"}`} onClick={() => setSelectedPlanId(p.id)} onContextMenu={(e) => { e.preventDefault(); setContextMenu({ screenX: e.clientX, screenY: e.clientY, wx: p.x, wy: p.y, markerId: p.id }) }}>
                <div className="h-2 w-2 rounded-full shrink-0" style={{ background: p.source === "manual" ? "#59D6C2" : "#F6C453", opacity: p.status === "cancelled" ? 0.35 : 1 }} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5"><span className="truncate font-medium text-sm">{p.wave_name || p.name}</span>{p.status === "cancelled" && <Badge variant="outline" className="text-[10px]">отменён</Badge>}</div>
                  <div className="truncate text-xs text-muted-foreground">{Math.round(p.x)}, {Math.round(p.y)} · {p.source === "manual" ? "ручная" : "авто"} · {p.status} {p.note && `· ${p.note.slice(0, 30)}`}</div>
                </div>
                <Badge variant={p.status === "cancelled" ? "outline" : "secondary"} className="shrink-0 text-[10px]">{p.monsters.length} мобов</Badge>
              </button>)}
              {!filteredPlans.length && <div className="py-6 text-center text-sm text-muted-foreground flex flex-col items-center gap-1"><Filter className="size-4" /> Ничего не найдено — измените фильтр или ПКМ по карте</div>}
            </div>
            {selectedPlan && <div className="space-y-3 rounded-lg border p-3"><WaveEditor wave={selectedPlan} onChange={(wave) => updateDraft((next) => { const i = next.plans.findIndex((p) => p.id === selectedPlan.id); next.plans[i] = { ...next.plans[i], ...wave, wave_name: wave.name } })} /><Label className="space-y-1 text-xs"><span>Заметка оператора</span><Textarea value={selectedPlan.note || ""} onChange={(e) => updateDraft((next) => { next.plans.find((p) => p.id === selectedPlan.id)!.note = e.target.value })} /></Label><div className="flex gap-2"><Button variant={selectedPlan.status === "cancelled" ? "secondary" : "destructive"} onClick={() => updateDraft((next) => { const p = next.plans.find((item) => item.id === selectedPlan.id)!; p.status = p.status === "cancelled" ? "planned" : "cancelled" })}>{selectedPlan.status === "cancelled" ? "Вернуть план" : "Отменить спавн"}</Button>{selectedPlan.source === "manual" && <Button variant="ghost" onClick={() => updateDraft((next) => { next.plans = next.plans.filter((p) => p.id !== selectedPlan.id); setSelectedPlanId(null) })}><Trash2 className="size-4" /> Удалить</Button>}<Button variant="outline" onClick={() => duplicatePlan(selectedPlan.id)}>Дублировать</Button><Button variant="outline" onClick={() => copyCoords(selectedPlan.x, selectedPlan.y)}><Copy className="size-3.5" /> Коорд.</Button></div></div>}
          </TabsContent>
          <TabsContent value="waves" className="space-y-3"><div className="flex gap-2"><Select value={String(selectedWave)} onValueChange={(v) => setSelectedWave(Number(v))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{draft.config.waves.map((w, i) => <SelectItem key={i} value={String(i)}>{w.name}</SelectItem>)}</SelectContent></Select><Button variant="outline" onClick={() => updateDraft((next) => { next.config.waves.push(emptyWave()); setSelectedWave(next.config.waves.length - 1) })}><Plus className="size-4" /></Button><Button variant="ghost" disabled={draft.config.waves.length < 2} onClick={() => updateDraft((next) => { next.config.waves.splice(selectedWave, 1); setSelectedWave(0) })}><Trash2 className="size-4" /></Button></div>{draft.config.waves[selectedWave] && <WaveEditor wave={draft.config.waves[selectedWave]} onChange={(wave) => updateDraft((next) => { next.config.waves[selectedWave] = wave })} />}</TabsContent>
          <TabsContent value="difficulty" className="space-y-3"><p className="text-xs text-muted-foreground">Все глобальные параметры плагина. Логические значения переключаются, числовые редактируются напрямую.</p><div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{globalEntries.map(([key, value]) => <Label key={key} className="space-y-1 text-xs"><span className="text-muted-foreground">{key.replaceAll("_", " ")}</span>{typeof value === "boolean" ? <div className="flex h-9 items-center"><Switch checked={value} onCheckedChange={(v) => updateDraft((next) => { next.config.global[key] = v })} /></div> : typeof value === "number" ? <Input type="number" step="any" value={value} onChange={(e) => updateDraft((next) => { next.config.global[key] = number(e.target.value) })} /> : <Input value={Array.isArray(value) ? value.join(", ") : typeof value === "object" ? JSON.stringify(value) : String(value ?? "")} onChange={(e) => updateDraft((next) => { next.config.global[key] = Array.isArray(value) ? e.target.value.split(",").map((s) => s.trim()).filter(Boolean) : e.target.value })} />}</Label>)}</div></TabsContent>
          <TabsContent value="memory" className="space-y-2"><p className="text-xs text-muted-foreground">Долговременная адаптивная память. Доступ защищён отдельным правом.</p><Textarea className="min-h-[520px] font-mono text-xs" disabled={!canDo(admin, "monster_spawn", "manage_memory")} value={JSON.stringify(draft.memory, null, 2)} onChange={(e) => { try { const parsed = JSON.parse(e.target.value); updateDraft((next) => { next.memory = parsed }) } catch { /* keep the last valid document */ } }} /></TabsContent>
          <TabsContent value="json" className="space-y-3"><p className="text-xs text-muted-foreground">Полный конфиг для редких и новых параметров, которые ещё не получили отдельное поле.</p><Textarea className="min-h-[560px] font-mono text-xs" value={JSON.stringify(draft.config, null, 2)} onChange={(e) => { try { const parsed = JSON.parse(e.target.value); updateDraft((next) => { next.config = parsed }) } catch { /* keep the last valid document */ } }} /></TabsContent>
        </Tabs>
      </CardContent></Card>
    </div>
  </div>
}
