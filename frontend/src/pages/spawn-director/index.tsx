import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Radar, RotateCcw, ShieldAlert, Swords } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { PageHeader } from "@/components/layout/PageHeader"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { worldStateApi } from "@/pages/world-overview/api"
import { WorldCanvas } from "@/pages/world-overview/WorldCanvas"

type Wave = { id: number; species: string; amount: number; original_amount: number; hp_multiplier: number; x?: number; y?: number; state: string; adjustment?: string }
type Server = { server_name: string; mode: string; crew_power: number; learned_baseline: number; baseline_samples: number; threat_multiplier: number; explanation: string; waves: Wave[]; updated_at: string }
type CreatureTarget = { tag: string; state: string; priority: string; react?: number; attack?: number }
type Creature = { id: string; name_ru?: string; group: string; base_hp: number; needs_water: boolean; needs_air: boolean; combat_strength: number; sight: number; hearing: number; aggression_hurt: number; aggression_greed: number; flee_health_threshold: number; aggressive_boarding: boolean; targets: CreatureTarget[] }

const TAG_RU: Record<string, string> = {
  human: "человек", weaker: "более слабый противник", stronger: "более сильный противник",
  dead: "трупы", husk: "хаск", wall: "стены", door: "двери", room: "отсеки",
  engine: "двигатели", weapon: "оружие", turret: "турели", sonar: "сонар",
  searchlight: "прожекторы", decoy: "приманки", provocative: "провокаторы",
  tool: "инструменты", monsterfood: "еда", leucocyte: "лейкоцит",
  terminalcell: "терминальные клетки", nasonov: "артефакт", crawlerbroodmother: "матка ползунов",
  crawler_large: "крупный ползун", watcher: "смотритель",
}
const STATE_RU: Record<string, string> = {
  Attack: "атака", Aggressive: "агрессия", Avoid: "избегание", Eat: "поедание",
  Protect: "защита", PassiveAggressive: "оборона", Observe: "наблюдение",
  Follow: "преследование", Flee: "бегство", Idle: "покой",
}

/** Игровые единицы дальности — пиксели, 100 ед. = 1 м (Physics.DisplayToRealWorldRatio). */
function meters(value: number | undefined): string {
  if (!value || value <= 0) return "авто"
  const m = value / 100
  return `${Number.isInteger(m) ? m : m.toFixed(1)} м`
}

function StatBar({ label, display, value, max }: { label: string; display: string; value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return <div>
    <div className="flex items-baseline justify-between gap-2"><p className="text-xs text-muted-foreground">{label}</p><p className="text-xs font-medium">{display}</p></div>
    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} /></div>
  </div>
}

const NUM_FILTERS = [
  { key: "hp", label: "HP", get: (c: Creature) => c.base_hp },
  { key: "cs", label: "Сила", get: (c: Creature) => c.combat_strength },
  { key: "sight", label: "Зрение", get: (c: Creature) => c.sight },
  { key: "hearing", label: "Слух", get: (c: Creature) => c.hearing },
  { key: "flee", label: "Бегство %", get: (c: Creature) => c.flee_health_threshold },
] as const

export function SpawnDirectorPage() {
  const [roundId, setRoundId] = useState<string | null>(null)
  const [selectedServer, setSelectedServer] = useState<string>("__all__")
  const [multiplier, setMultiplier] = useState("0")
  const [fixed, setFixed] = useState("0")
  const [saving, setSaving] = useState(false)
  const [savedFor, setSavedFor] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [groupFilter, setGroupFilter] = useState("__all__")
  const [habitatFilter, setHabitatFilter] = useState("__all__")
  const [boardingFilter, setBoardingFilter] = useState("__all__")
  const [targetFilter, setTargetFilter] = useState("__all__")
  const [numFilter, setNumFilter] = useState<Record<string, { min: string; max: string }>>({})
  const { data: admin } = useCurrentAdmin()
  const { data } = useQuery({ queryKey: ["spawn-director", selectedServer], queryFn: () => api.get<{ servers: Server[] }>(`/api/v1/admin/spawn-director/state${selectedServer === "__all__" ? "" : `?server_name=${encodeURIComponent(selectedServer)}`}`), refetchInterval: 3000 })
  const { data: creatureData, isError: creaturesError } = useQuery({ queryKey: ["spawn-director-creatures"], queryFn: async () => { try { return await api.get<{ creatures: Creature[] }>("/api/v1/admin/spawn-director/creatures") } catch { const res = await fetch("/static/creature_catalog.json"); if (!res.ok) throw new Error(`Статический каталог недоступен: HTTP ${res.status}`); return (await res.json()) as { creatures: Creature[] } } }, staleTime: Infinity, retry: 1 })
  const { data: knownServers } = useQuery({ queryKey: ["spawn-director-known-servers"], queryFn: () => worldStateApi.listServers(), refetchInterval: 5000 })
  const servers = data?.servers ?? []
  const serverNames = [...new Set([...(knownServers?.servers ?? []), ...servers.map(server => server.server_name)])].sort()
  const creatures = creatureData?.creatures ?? []
  const { data: rounds } = useQuery({ queryKey: ["spawn-director-rounds", selectedServer], queryFn: () => worldStateApi.listRounds(selectedServer === "__all__" ? null : selectedServer), refetchInterval: 5000 })
  useEffect(() => { if (!roundId) setRoundId(rounds?.rounds[0]?.round_id ?? null) }, [roundId, rounds])
  const { data: geometry } = useQuery({ queryKey: ["spawn-director-geometry", roundId], queryFn: () => worldStateApi.getGeometry(roundId as string), enabled: !!roundId, staleTime: Infinity })
  const { data: state } = useQuery({ queryKey: ["spawn-director-world", roundId], queryFn: () => worldStateApi.getState(roundId as string), enabled: !!roundId, refetchInterval: 1000 })

  const groups = useMemo(() => [...new Set(creatures.map(c => c.group || "без группы"))].sort(), [creatures])
  const targetTags = useMemo(() => [...new Set(creatures.flatMap(c => c.targets.map(t => t.tag)).filter(Boolean))].sort(), [creatures])
  const maxima = useMemo(() => ({
    hp: Math.max(1, ...creatures.map(c => c.base_hp)),
    cs: Math.max(1, ...creatures.map(c => c.combat_strength)),
    sight: Math.max(1, ...creatures.map(c => c.sight)),
    hearing: Math.max(1, ...creatures.map(c => c.hearing)),
  }), [creatures])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return creatures.filter(creature => {
      if (q && !`${creature.id} ${creature.name_ru ?? ""} ${creature.group}`.toLowerCase().includes(q)) return false
      if (groupFilter !== "__all__" && (creature.group || "без группы") !== groupFilter) return false
      if (habitatFilter === "water" && !creature.needs_water) return false
      if (habitatFilter === "land" && creature.needs_water) return false
      if (boardingFilter === "yes" && !creature.aggressive_boarding) return false
      if (boardingFilter === "no" && creature.aggressive_boarding) return false
      if (targetFilter !== "__all__" && !creature.targets.some(t => t.tag === targetFilter)) return false
      for (const { key, get } of NUM_FILTERS) {
        const bounds = numFilter[key]
        if (!bounds) continue
        const value = get(creature)
        if (bounds.min !== "" && value < Number(bounds.min)) return false
        if (bounds.max !== "" && value > Number(bounds.max)) return false
      }
      return true
    })
  }, [creatures, search, groupFilter, habitatFilter, boardingFilter, targetFilter, numFilter])

  const resetFilters = () => { setSearch(""); setGroupFilter("__all__"); setHabitatFilter("__all__"); setBoardingFilter("__all__"); setTargetFilter("__all__"); setNumFilter({}) }

  return <div className="space-y-5">
    <PageHeader title="Директор волн" description="Адаптивная угроза и точки появления на карте текущего раунда" icon={Radar} actions={<Select value={selectedServer} onValueChange={(server) => { setSelectedServer(server); setRoundId(null) }}><SelectTrigger className="w-44"><SelectValue placeholder="Сервер" /></SelectTrigger><SelectContent><SelectItem value="__all__">Все серверы</SelectItem>{serverNames.map(server => <SelectItem key={server} value={server}>{server}</SelectItem>)}</SelectContent></Select>} />
    {geometry && state && <section className="h-[420px]"><WorldCanvas geometry={geometry} state={state} selectedCharacterId={null} onSelectCharacter={() => {}} /></section>}
    {!servers.length ? <Card><CardContent className="py-16 text-center text-sm text-muted-foreground">Телеметрия ещё не поступала. Укажите `TelemetryUrl` в `adaptive_spawn_director.json`.</CardContent></Card> : servers.map(server => <section key={server.server_name} className="space-y-3">
      <div className="flex items-center justify-between"><h2 className="font-semibold">{server.server_name}</h2><Badge variant="secondary">{server.mode}</Badge></div>
      {admin?.is_superuser && <Card><CardContent className="flex flex-wrap items-end gap-3 py-4"><div><p className="mb-1 text-xs text-muted-foreground">Мин. множитель HP</p><Input className="w-40" type="number" min="0" step="0.05" value={multiplier} onChange={e => setMultiplier(e.target.value)} /></div><div><p className="mb-1 text-xs text-muted-foreground">Мин. фиксированный HP</p><Input className="w-40" type="number" min="0" step="1" value={fixed} onChange={e => setFixed(e.target.value)} /></div><Button disabled={saving} onClick={async () => { setSaving(true); try { await api.put(`/api/v1/admin/spawn-director/health-floor/${encodeURIComponent(server.server_name)}`, { multiplier: Number(multiplier), fixed: Number(fixed) }); setSavedFor(server.server_name) } finally { setSaving(false) } }}>{saving ? "Сохранение..." : "Применить"}</Button>{savedFor === server.server_name && <span className="text-xs text-emerald-600">Отправлено директору</span>}<p className="basis-full text-xs text-muted-foreground">Используется больший из двух минимумов. Плагин применит настройку в течение 3 секунд.</p></CardContent></Card>}
      <div className="grid gap-3 md:grid-cols-3"><Card><CardContent className="flex items-center gap-3 py-4"><ShieldAlert className="size-5 text-primary" /><div><p className="text-xs text-muted-foreground">Сила экипажа (EMA)</p><p className="text-2xl font-semibold">{server.crew_power.toFixed(2)}</p></div></CardContent></Card><Card><CardContent className="py-4"><p className="text-xs text-muted-foreground">Самообученная норма</p><p className="text-2xl font-semibold">{server.learned_baseline.toFixed(2)}</p><p className="text-xs text-muted-foreground">{server.baseline_samples} измерений, без ручной установки</p></CardContent></Card><Card><CardContent className="flex items-center gap-3 py-4"><Swords className="size-5 text-destructive" /><div><p className="text-xs text-muted-foreground">Множитель угрозы</p><p className="text-2xl font-semibold">x{server.threat_multiplier.toFixed(2)}</p></div></CardContent></Card></div>
      <p className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">{server.explanation}</p>
      <Card><CardContent className="p-0"><div className="grid grid-cols-[1fr_auto_auto_auto] gap-3 border-b px-4 py-2 text-xs text-muted-foreground"><span>Волна / решение</span><span>Количество</span><span>HP</span><span>Точка</span></div>{server.waves.map(w => <div key={w.id} className="grid grid-cols-[1fr_auto_auto_auto] gap-3 px-4 py-3 text-sm"><span><b>#{w.id}</b> {w.species}<p className="text-xs text-muted-foreground">{w.state === "pending" ? "План: ванильная точка ещё не подтверждена" : "Применено: "}{w.adjustment}</p></span><span>{w.amount} <span className="text-muted-foreground">/ {w.original_amount}</span></span><span>x{w.hp_multiplier.toFixed(2)}</span><span className="font-mono text-xs">{w.x == null ? "ожидание точки" : `${Math.round(w.x)}, ${Math.round(w.y!)}`}</span></div>)}</CardContent></Card>
    </section>)}
    <section className="space-y-3">
      <div><h2 className="font-semibold">Ванильный каталог существ</h2><p className="text-sm text-muted-foreground">Данные извлечены из `Content/Characters`: базовая vitality, параметры AI и приоритеты целей. При множителе показывается итоговый нижний порог HP для каждого вида.</p></div>
      <details className="rounded-lg border px-4 py-3 text-sm"><summary className="cursor-pointer font-medium">Что означают показатели (по коду игры)</summary><ul className="mt-2 list-disc space-y-1.5 pl-5 text-muted-foreground">
        <li><b>Базовое HP</b> — vitality из `Content/Characters`. Шкала на карточке — относительно самого живучего вида.</li>
        <li><b>Зрение / слух</b> — безразмерные множители восприятия (1,0 — норма; у большинства монстров зрение 0–2, слух до 10). Дальность обнаружения = «заметность» цели × множитель: бегущий шумный человек виден дальше (`Character.cs`). 100 ед. дальности = 1 м.</li>
        <li><b>Реакция на урон</b> — рост ненависти к обидчику: (полученный урон / max HP) × значение (`EnemyAIController`). Подсказка на карточке — бонус приоритета за удар на 10% HP.</li>
        <li><b>Жадность</b> — азарт добивания: (свой урон / текущее HP жертвы) × значение, постройкам — половина. Высокое значение — залипание на раненых целях.</li>
        <li><b>Бегство при HP</b> — ниже этого % здоровья существо обращается в бегство.</li>
        <li><b>Боевая мощь</b> — сравнение сил: цели «сильнее» избегаются, «слабее» атакуются.</li>
        <li><b>Цели</b> — состояние: что делает существо (атака, поедание, защита, избегание, оборона при провокации); приоритет: выше — желаннее. Дистанции реакции/атаки в метрах; «авто» — автовыбор по восприятию, атака примерно с половины дистанции реакции.</li>
      </ul></details>
      {creatures.length > 0 && <Card><CardContent className="space-y-3 py-4">
        <Input placeholder="Поиск: название, вид, группа…" value={search} onChange={e => setSearch(e.target.value)} />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div><p className="mb-1 text-xs text-muted-foreground">Группа</p><Select value={groupFilter} onValueChange={setGroupFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="__all__">Все</SelectItem>{groups.map(g => <SelectItem key={g} value={g}>{g}</SelectItem>)}</SelectContent></Select></div>
          <div><p className="mb-1 text-xs text-muted-foreground">Среда</p><Select value={habitatFilter} onValueChange={setHabitatFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="__all__">Любая</SelectItem><SelectItem value="water">Водная</SelectItem><SelectItem value="land">Суша / амфибия</SelectItem></SelectContent></Select></div>
          <div><p className="mb-1 text-xs text-muted-foreground">Штурм подлодки</p><Select value={boardingFilter} onValueChange={setBoardingFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="__all__">Не важно</SelectItem><SelectItem value="yes">Да</SelectItem><SelectItem value="no">Нет</SelectItem></SelectContent></Select></div>
          <div><p className="mb-1 text-xs text-muted-foreground">Охотится на</p><Select value={targetFilter} onValueChange={setTargetFilter}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="__all__">Все цели</SelectItem>{targetTags.map(t => <SelectItem key={t} value={t}>{TAG_RU[t] ?? t}</SelectItem>)}</SelectContent></Select></div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {NUM_FILTERS.map(({ key, label }) => <div key={key}><p className="mb-1 text-xs text-muted-foreground">{label}: от – до</p><div className="flex items-center gap-1.5"><Input className="h-8" type="number" placeholder="мин" value={numFilter[key]?.min ?? ""} onChange={e => setNumFilter(prev => ({ ...prev, [key]: { min: e.target.value, max: prev[key]?.max ?? "" } }))} /><Input className="h-8" type="number" placeholder="макс" value={numFilter[key]?.max ?? ""} onChange={e => setNumFilter(prev => ({ ...prev, [key]: { min: prev[key]?.min ?? "", max: e.target.value } }))} /></div></div>)}
        </div>
        <div className="flex items-center justify-between"><p className="text-xs text-muted-foreground">Показано {filtered.length} из {creatures.length}</p><Button variant="ghost" size="sm" onClick={resetFilters}><RotateCcw className="mr-1.5 size-3.5" />Сбросить</Button></div>
      </CardContent></Card>}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{filtered.map(creature => {
        const multiplierFloor = creature.base_hp * Number(multiplier || 0)
        const finalFloor = Math.max(multiplierFloor, Number(fixed || 0))
        const sortedTargets = [...creature.targets].sort((a, b) => Number(b.priority || 0) - Number(a.priority || 0))
        return <Card key={creature.id}><CardContent className="space-y-3 py-4">
          <div className="flex items-start justify-between gap-2"><div><h3 className="font-semibold">{creature.name_ru || creature.id}</h3><p className="font-mono text-xs text-muted-foreground">{creature.id}</p><p className="mt-0.5 text-xs text-muted-foreground">{creature.group || "без группы"} · {creature.needs_water ? "водная среда" : "суша/амфибия"}</p></div><Badge variant="outline">CS {creature.combat_strength}</Badge></div>
          <div className="space-y-2">
            <StatBar label="Ванильное HP" display={Number(multiplier) > 0 ? `${creature.base_hp} → мин. ${Math.round(finalFloor)} HP` : `${creature.base_hp} HP`} value={creature.base_hp} max={maxima.hp} />
            <div className="grid grid-cols-2 gap-x-3 gap-y-2">
              <StatBar label="Боевая мощь" display={`${creature.combat_strength}`} value={creature.combat_strength} max={maxima.cs} />
              <StatBar label="Бегство при HP" display={`${creature.flee_health_threshold}%`} value={creature.flee_health_threshold} max={100} />
              <StatBar label="Зрение" display={`${creature.sight}`} value={creature.sight} max={maxima.sight} />
              <StatBar label="Слух" display={`${creature.hearing}`} value={creature.hearing} max={maxima.hearing} />
            </div>
          </div>
          <div className="border-t pt-2 text-xs text-muted-foreground space-y-1">
            <p>Реакция на урон: <b className="text-foreground">{creature.aggression_hurt}</b> <span>(+{(creature.aggression_hurt / 10).toFixed(1)} ненависти за удар на 10% HP)</span></p>
            <p>Жадность: <b className="text-foreground">{creature.aggression_greed}</b> <span>(+{(creature.aggression_greed / 10).toFixed(1)} азарта за добивание 10% HP)</span>; штурм: {creature.aggressive_boarding ? "да" : "нет"}</p>
          </div>
          <div className="border-t pt-2"><p className="mb-1.5 text-xs font-medium">Цели по приоритету</p>
            {!sortedTargets.length && <p className="text-xs text-muted-foreground">не определены</p>}
            <ul className="space-y-1">
              {sortedTargets.slice(0, 6).map((target, i) => <li key={i} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs"><Badge variant="secondary" className="font-mono">{target.priority}</Badge><span className="font-medium">{(TAG_RU[target.tag] ?? target.tag) || "?"}</span><span className="text-muted-foreground">{STATE_RU[target.state] ?? target.state} · реакция {meters(target.react)} · атака {meters(target.attack)}</span></li>)}
            </ul>
            {sortedTargets.length > 6 && <details className="mt-1"><summary className="cursor-pointer text-xs text-muted-foreground">Ещё {sortedTargets.length - 6}…</summary><ul className="mt-1 space-y-1">{sortedTargets.slice(6).map((target, i) => <li key={i} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs"><Badge variant="secondary" className="font-mono">{target.priority}</Badge><span className="font-medium">{(TAG_RU[target.tag] ?? target.tag) || "?"}</span><span className="text-muted-foreground">{STATE_RU[target.state] ?? target.state} · реакция {meters(target.react)} · атака {meters(target.attack)}</span></li>)}</ul></details>}
          </div>
        </CardContent></Card>
      })}</div>
      {!creatures.length && creaturesError && <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">Встроенный каталог существ не найден в развёртывании. Обратитесь к администратору сайта.</CardContent></Card>}
      {creatures.length > 0 && !filtered.length && <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">Под фильтры ничего не подходит. Измените условия поиска.</CardContent></Card>}
    </section></div>
}
