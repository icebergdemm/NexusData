import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Crosshair, Plus, Save, Skull, Trash2, X } from "lucide-react"
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

export function MonsterSpawnPage() {
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()
  const [server, setServer] = useState("ASPER1")
  const [draft, setDraft] = useState<MonsterSpawnState | null>(null)
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null)
  const [selectedWave, setSelectedWave] = useState(0)
  const [placing, setPlacing] = useState(false)
  const [dirty, setDirty] = useState(false)

  const rounds = useQuery({ queryKey: ["world-rounds"], queryFn: worldStateApi.listRounds, refetchInterval: 5000 })
  const round = rounds.data?.rounds.find((r) => r.server_name?.toUpperCase() === server) ?? null
  const geometry = useQuery({ queryKey: ["world-geometry", round?.round_id], queryFn: () => worldStateApi.getGeometry(round!.round_id), enabled: !!round, staleTime: Infinity })
  const world = useQuery({ queryKey: ["world-state", round?.round_id], queryFn: () => worldStateApi.getState(round!.round_id), enabled: !!round, refetchInterval: 3000 })
  const state = useQuery({ queryKey: ["monster-spawn", server], queryFn: () => monsterSpawnApi.get(server), refetchInterval: 5000 })

  useEffect(() => { if (state.data && !dirty) setDraft(clone(state.data)) }, [state.data, dirty])
  useEffect(() => { setDirty(false); setSelectedPlanId(null); setDraft(null) }, [server])

  const save = useMutation({
    mutationFn: () => monsterSpawnApi.update(server, { config: draft!.config, plans: draft!.plans, memory: draft!.memory }),
    onSuccess: (next) => { setDraft(clone(next)); setDirty(false); queryClient.setQueryData(["monster-spawn", server], next); toast.success("Изменения отправлены плагину") },
    onError: (e: Error) => toast.error(e.message),
  })
  const updateDraft = (fn: (next: MonsterSpawnState) => void) => { if (!draft) return; const next = clone(draft); fn(next); setDraft(next); setDirty(true) }
  const markers: WorldMarker[] = (draft?.plans ?? []).map((p) => ({ id: p.id, x: p.x, y: p.y, label: p.wave_name || p.name, color: p.source === "manual" ? "#59D6C2" : "#F6C453", muted: p.status === "cancelled" }))
  const selectedPlan = draft?.plans.find((p) => p.id === selectedPlanId) ?? null

  const globalEntries = useMemo(() => Object.entries(draft?.config.global ?? {}), [draft?.config.global])
  if (!draft) return <div><PageHeader title="Управление монстрами" description="Загрузка состояния плагина" icon={Skull} /><div className="h-64 animate-pulse rounded-xl bg-muted" /></div>

  const createPlan = (x: number, y: number) => updateDraft((next) => {
    const wave = clone(next.config.waves[selectedWave] ?? emptyWave())
    const plan: SpawnPlan = { ...wave, id: crypto.randomUUID(), wave_name: wave.name, source: "manual", status: "planned", x, y, note: "" }
    next.plans.push(plan); setSelectedPlanId(plan.id); setPlacing(false)
  })

  return <div className="flex h-[calc(100vh-6rem)] flex-col">
    <PageHeader title="Управление монстрами" description="Планы волн, сложность и память MonsterSpawnController" icon={Skull} actions={<div className="flex gap-2"><Select value={server} onValueChange={setServer}><SelectTrigger className="w-36"><SelectValue /></SelectTrigger><SelectContent>{["ASPER1", "ASPER2"].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent></Select><Button disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save className="size-4" /> Сохранить</Button></div>} />
    <div className="mb-3 flex items-center gap-2 text-xs"><Badge variant={draft.pending ? "destructive" : "secondary"}>{draft.pending ? `Ожидает применения r${draft.desired_revision}` : `Применено r${draft.applied_revision}`}</Badge><span className="text-muted-foreground">Плагин: {draft.last_seen_at ? new Date(draft.last_seen_at).toLocaleString() : "ещё не подключался"}</span>{dirty && <Badge variant="outline">Есть несохранённые изменения</Badge>}</div>
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(420px,.65fr)]">
      <div className="relative min-h-[430px]">
        {geometry.data && world.data ? <WorldCanvas geometry={geometry.data} state={world.data} selectedCharacterId={null} onSelectCharacter={() => {}} targetMode={placing} onPickTarget={createPlan} markers={markers} selectedMarkerId={selectedPlanId} onSelectMarker={setSelectedPlanId} onMoveMarker={(id, x, y) => updateDraft((next) => { const p = next.plans.find((item) => item.id === id); if (p) { p.x = x; p.y = y } })} /> : <Card className="flex h-full items-center justify-center text-sm text-muted-foreground">Для {server} нет активной карты раунда</Card>}
        <div className="absolute bottom-3 left-3 flex gap-2"><Button variant={placing ? "destructive" : "secondary"} onClick={() => setPlacing(!placing)}>{placing ? <X className="size-4" /> : <Crosshair className="size-4" />}{placing ? "Отмена" : "Поставить точку"}</Button></div>
      </div>
      <Card className="min-h-0 overflow-hidden"><CardContent className="h-full overflow-y-auto p-4">
        <Tabs defaultValue="plans">
          <TabsList className="grid w-full grid-cols-5"><TabsTrigger value="plans">Планы</TabsTrigger><TabsTrigger value="waves">Волны</TabsTrigger><TabsTrigger value="difficulty">Сложность</TabsTrigger><TabsTrigger value="memory">Память</TabsTrigger><TabsTrigger value="json">JSON</TabsTrigger></TabsList>
          <TabsContent value="plans" className="space-y-3">
            <div className="flex gap-2"><Select value={String(selectedWave)} onValueChange={(v) => setSelectedWave(Number(v))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{draft.config.waves.map((w, i) => <SelectItem key={i} value={String(i)}>{w.name}</SelectItem>)}</SelectContent></Select></div>
            {draft.plans.map((p) => <button key={p.id} className={`w-full rounded-lg border p-3 text-left ${p.id === selectedPlanId ? "border-primary bg-accent" : ""}`} onClick={() => setSelectedPlanId(p.id)}><div className="flex items-center justify-between"><span className="font-medium">{p.wave_name || p.name}</span><Badge variant={p.status === "cancelled" ? "outline" : "secondary"}>{p.status}</Badge></div><div className="mt-1 text-xs text-muted-foreground">{Math.round(p.x)}, {Math.round(p.y)} · {p.source === "manual" ? "ручная" : "автоматическая"}</div></button>)}
            {selectedPlan && <div className="space-y-3 rounded-lg border p-3"><WaveEditor wave={selectedPlan} onChange={(wave) => updateDraft((next) => { const i = next.plans.findIndex((p) => p.id === selectedPlan.id); next.plans[i] = { ...next.plans[i], ...wave, wave_name: wave.name } })} /><Label className="space-y-1 text-xs"><span>Заметка оператора</span><Textarea value={selectedPlan.note || ""} onChange={(e) => updateDraft((next) => { next.plans.find((p) => p.id === selectedPlan.id)!.note = e.target.value })} /></Label><div className="flex gap-2"><Button variant={selectedPlan.status === "cancelled" ? "secondary" : "destructive"} onClick={() => updateDraft((next) => { const p = next.plans.find((item) => item.id === selectedPlan.id)!; p.status = p.status === "cancelled" ? "planned" : "cancelled" })}>{selectedPlan.status === "cancelled" ? "Вернуть план" : "Отменить спавн"}</Button>{selectedPlan.source === "manual" && <Button variant="ghost" onClick={() => updateDraft((next) => { next.plans = next.plans.filter((p) => p.id !== selectedPlan.id); setSelectedPlanId(null) })}><Trash2 className="size-4" /> Удалить</Button>}</div></div>}
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
