import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { monsterSpawnApi, type CreatureInfo, type MonsterSpawnState, type SpawnWave } from "./api"

type Kind = "monster" | "wave" | "learned"

export function SpawnNowDialog({ server, state, creatures, onQueued, onClose }: {
  server: string; state: MonsterSpawnState; creatures: CreatureInfo[]; onQueued: () => void; onClose: () => void
}) {
  const [kind, setKind] = useState<Kind>("monster")
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState("")
  const [count, setCount] = useState(1)
  const species = new Map(creatures.filter(c => c.group !== "human").map(c => [c.id, c.name_ru || c.id]))
  const baseline = state.telemetry.species_baseline
  if (Array.isArray(baseline)) for (const item of baseline) if (item?.species && !species.has(item.species)) species.set(item.species, item.species)
  const learned = state.memory.learned_waves as Record<string, { wave?: SpawnWave }> | undefined
  const choices = kind === "monster"
    ? [...species].map(([key, label]) => ({ key, label, detail: key }))
    : (kind === "wave" ? state.config.waves.map(wave => ({ key: wave.name, wave }))
      : Object.entries(learned ?? {}).filter(([, value]) => value?.wave).map(([key, value]) => ({ key, wave: value.wave! })))
      .map(({ key, wave }) => ({ key, label: wave.name, detail: wave.monsters.map(m => `${m.species} × ${m.amount_max || m.count}`).join(", ") }))
  const filtered = choices.filter(c => `${c.label} ${c.detail}`.toLowerCase().includes(search.toLowerCase())).sort((a, b) => a.label.localeCompare(b.label))
  const chosen = choices.find(c => c.key === selected)
  const spawn = useMutation({
    mutationFn: () => monsterSpawnApi.spawnNow(server, { kind, key: selected, count }),
    onSuccess: () => { toast.success("Команда отправлена. Результат появится после создания монстров."); onQueued() },
    onError: (error: Error) => toast.error(error.message),
  })
  const statuses: Record<string, string> = { planned: "Ожидает получения плагином", spawning: "Создание", spawned: "Создано", rejected: "Отклонено", expired: "Истекла" }
  const commands = state.plans.filter(p => p.source === "manual_immediate").slice(-5).reverse()
  const pending = commands.some(p => (p.status === "planned" || p.status === "spawning") && p.round_key === state.telemetry.round_key && Date.parse(p.expires_at ?? "") > Date.now())
  return <Dialog open onOpenChange={open => { if (!open) onClose() }}><DialogContent className="sm:max-w-xl">
    <DialogHeader><DialogTitle>Спавнить сейчас · {server}</DialogTitle><DialogDescription>Без ожидания бюджета и таймеров волн. Команда выполняется при следующем опросе API (обычно до 10 секунд). Ограничения безопасной позиции и разрешённых видов сохраняются.</DialogDescription></DialogHeader>
    <div className="flex gap-2">{([["monster", "Монстр"], ["wave", "Волна"], ["learned", "Изученная волна"]] as const).map(([value, label]) => <Button key={value} variant={kind === value ? "default" : "outline"} onClick={() => { setKind(value); setSelected(""); setSearch("") }}>{label}</Button>)}</div>
    <Input aria-label="Поиск монстра или волны" placeholder="Поиск по имени или виду…" value={search} onChange={e => setSearch(e.target.value)} />
    <div className="max-h-64 space-y-1 overflow-y-auto rounded-lg border p-2" role="listbox" aria-label="Каталог спавна">
      {filtered.map(c => <button key={c.key} role="option" aria-selected={selected === c.key} className={`w-full rounded-md p-2 text-left text-sm hover:bg-accent ${selected === c.key ? "bg-primary/10 ring-1 ring-primary" : ""}`} onClick={() => setSelected(c.key)}><div className="font-medium">{c.label}</div><div className="text-xs text-muted-foreground">{c.detail}</div></button>)}
      {!filtered.length && <p className="p-3 text-sm text-muted-foreground">Подходящих записей нет</p>}
    </div>
    {kind === "monster" && <div className="space-y-1"><Label htmlFor="spawn-now-count">Количество (1–30)</Label><Input id="spawn-now-count" type="number" min={1} max={30} value={count} onChange={e => setCount(Number(e.target.value))} /></div>}
    <Button disabled={!chosen || pending || spawn.isPending || !state.dashboard?.round_active || !Number.isInteger(count) || count < 1 || count > 30} onClick={() => spawn.mutate()}>{spawn.isPending ? "Отправка…" : `Спавнить ${chosen?.label ?? "выбранное"}${kind === "monster" ? ` × ${count}` : ""}`}</Button>
    {commands.length > 0 && <div className="space-y-2 border-t pt-3 text-xs" aria-live="polite">{commands.map(p => <div key={p.id}><b>{p.wave_name || p.name}</b> · {statuses[p.status] || p.status}{p.note && <div className="text-muted-foreground">{p.note}</div>}</div>)}</div>}
  </DialogContent></Dialog>
}
