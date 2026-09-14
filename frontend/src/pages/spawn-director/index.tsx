import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Radar, ShieldAlert, Swords } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { worldStateApi } from "@/pages/world-overview/api"
import { WorldCanvas } from "@/pages/world-overview/WorldCanvas"

type Wave = { id: number; species: string; amount: number; original_amount: number; hp_multiplier: number; x?: number; y?: number; state: string }
type Server = { server_name: string; mode: string; crew_power: number; threat_multiplier: number; waves: Wave[]; updated_at: string }

export function SpawnDirectorPage() {
  const [roundId, setRoundId] = useState<string | null>(null)
  const [multiplier, setMultiplier] = useState("0")
  const [fixed, setFixed] = useState("0")
  const [saving, setSaving] = useState(false)
  const [savedFor, setSavedFor] = useState<string | null>(null)
  const { data: admin } = useCurrentAdmin()
  const { data } = useQuery({ queryKey: ["spawn-director"], queryFn: () => api.get<{ servers: Server[] }>("/admin/spawn-director/state"), refetchInterval: 3000 })
  const servers = data?.servers ?? []
  const { data: rounds } = useQuery({ queryKey: ["spawn-director-rounds"], queryFn: () => worldStateApi.listRounds(), refetchInterval: 5000 })
  useEffect(() => { if (!roundId) setRoundId(rounds?.rounds[0]?.round_id ?? null) }, [roundId, rounds])
  const { data: geometry } = useQuery({ queryKey: ["spawn-director-geometry", roundId], queryFn: () => worldStateApi.getGeometry(roundId as string), enabled: !!roundId, staleTime: Infinity })
  const { data: state } = useQuery({ queryKey: ["spawn-director-world", roundId], queryFn: () => worldStateApi.getState(roundId as string), enabled: !!roundId, refetchInterval: 1000 })
  return <div className="space-y-5">
    <PageHeader title="Директор волн" description="Адаптивная угроза и точки появления на карте текущего раунда" icon={Radar} />
    {geometry && state && <section className="h-[420px]"><WorldCanvas geometry={geometry} state={state} selectedCharacterId={null} onSelectCharacter={() => {}} /></section>}
    {!servers.length ? <Card><CardContent className="py-16 text-center text-sm text-muted-foreground">Телеметрия ещё не поступала. Укажите `TelemetryUrl` в `adaptive_spawn_director.json`.</CardContent></Card> : servers.map(server => <section key={server.server_name} className="space-y-3">
      <div className="flex items-center justify-between"><h2 className="font-semibold">{server.server_name}</h2><Badge variant="secondary">{server.mode}</Badge></div>
      {admin?.is_superuser && <Card><CardContent className="flex flex-wrap items-end gap-3 py-4"><div><p className="mb-1 text-xs text-muted-foreground">Мин. множитель HP</p><Input className="w-40" type="number" min="0" step="0.05" value={multiplier} onChange={e => setMultiplier(e.target.value)} /></div><div><p className="mb-1 text-xs text-muted-foreground">Мин. фиксированный HP</p><Input className="w-40" type="number" min="0" step="1" value={fixed} onChange={e => setFixed(e.target.value)} /></div><Button disabled={saving} onClick={async () => { setSaving(true); try { await api.put(`/admin/spawn-director/health-floor/${encodeURIComponent(server.server_name)}`, { multiplier: Number(multiplier), fixed: Number(fixed) }); setSavedFor(server.server_name) } finally { setSaving(false) } }}>{saving ? "Сохранение..." : "Применить"}</Button>{savedFor === server.server_name && <span className="text-xs text-emerald-600">Отправлено директору</span>}<p className="basis-full text-xs text-muted-foreground">Используется больший из двух минимумов. Плагин применит настройку в течение 3 секунд.</p></CardContent></Card>}
      <div className="grid gap-3 md:grid-cols-2"><Card><CardContent className="flex items-center gap-3 py-4"><ShieldAlert className="size-5 text-primary" /><div><p className="text-xs text-muted-foreground">Сила экипажа</p><p className="text-2xl font-semibold">{server.crew_power.toFixed(2)}</p></div></CardContent></Card><Card><CardContent className="flex items-center gap-3 py-4"><Swords className="size-5 text-destructive" /><div><p className="text-xs text-muted-foreground">Множитель угрозы</p><p className="text-2xl font-semibold">x{server.threat_multiplier.toFixed(2)}</p></div></CardContent></Card></div>
      <Card><CardContent className="p-0"><div className="grid grid-cols-[1fr_auto_auto_auto] gap-3 border-b px-4 py-2 text-xs text-muted-foreground"><span>Волна / вид</span><span>Количество</span><span>HP</span><span>Координаты</span></div>{server.waves.map(w => <div key={w.id} className="grid grid-cols-[1fr_auto_auto_auto] gap-3 px-4 py-3 text-sm"><span><b>#{w.id}</b> {w.species} <span className="text-muted-foreground">{w.state}</span></span><span>{w.amount} <span className="text-muted-foreground">/ {w.original_amount}</span></span><span>x{w.hp_multiplier.toFixed(2)}</span><span className="font-mono text-xs">{w.x == null ? "ожидание точки" : `${Math.round(w.x)}, ${Math.round(w.y!)}`}</span></div>)}</CardContent></Card>
    </section>)}</div>
}
