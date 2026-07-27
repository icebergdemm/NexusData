/** Вкладки «Профессия» и «Статы и здоровье» конструктора кукол.
 *
 *  Все списки (профессии, таланты, недуги, типы статов) приходят из выгрузки
 *  игрового контента с русскими названиями — админ выбирает «Капитан», а в
 *  куклу уходит identifier "captain". Свободный ввод остаётся только там, где
 *  справочник пуст (например, на сервере стоит мод со своими талантами).
 */
import { useState } from "react"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { LIMBS, type DollReference, type DollSpec } from "./types"

const SKILLS = ["helm", "weapons", "medical", "electrical", "mechanical"]

function SkillSlider({ label, value, onChange }: {
  label: string
  value: number
  onChange: (v: number) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-24 shrink-0 text-xs">{label}</span>
      <input
        type="range" min={0} max={100} step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-muted accent-primary"
      />
      <Input
        type="number" min={0} max={100}
        value={value}
        onChange={(e) => onChange(Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
        className="h-8 w-16"
      />
    </div>
  )
}

export function JobTab({ spec, onChange, reference }: {
  spec: DollSpec
  onChange: (next: DollSpec) => void
  reference?: DollReference
}) {
  const job = spec.job ?? {}
  const progression = spec.progression ?? {}
  const jobId = job.identifier ?? "assistant"
  const jobs = reference?.jobs ?? []
  const jobInfo = jobs.find((j) => j.identifier === jobId)
  const talents = reference?.talents?.[jobId] ?? []
  const chosenTalents = progression.talents ?? []

  const setJob = (patch: Partial<NonNullable<DollSpec["job"]>>) =>
    onChange({ ...spec, job: { ...job, ...patch } })
  const setProgression = (patch: Partial<NonNullable<DollSpec["progression"]>>) =>
    onChange({ ...spec, progression: { ...progression, ...patch } })

  const skills = job.skills ?? {}

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label className="mb-1 block text-xs">Профессия</Label>
          <Select
            value={jobId}
            onValueChange={(v) => {
              // Навыки и таланты привязаны к профессии: при смене подставляем
              // штатные уровни новой и сбрасываем чужие таланты, иначе движок
              // молча их проигнорирует, а админ будет думать, что применились.
              const next = jobs.find((j) => j.identifier === v)
              onChange({
                ...spec,
                job: { identifier: v, variant: 0, skills: next ? { ...next.default_skills } : undefined },
                progression: { ...progression, talents: [] },
              })
            }}
          >
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {jobs.map((j) => <SelectItem key={j.identifier} value={j.identifier}>{j.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label className="mb-1 block text-xs">Вариант снаряжения</Label>
          <Select value={String(job.variant ?? 0)} onValueChange={(v) => setJob({ variant: Number(v) })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {Array.from({ length: jobInfo?.variants ?? 1 }).map((_, i) => (
                <SelectItem key={i} value={String(i)}>Набор {i + 1}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <Label className="text-xs">Навыки</Label>
          {jobInfo && (
            <button
              type="button"
              className="text-xs text-muted-foreground underline"
              onClick={() => setJob({ skills: { ...jobInfo.default_skills } })}
            >
              вернуть штатные для профессии
            </button>
          )}
        </div>
        <div className="space-y-2 rounded-md border p-3">
          {SKILLS.map((s) => (
            <SkillSlider
              key={s}
              label={reference?.skill_labels?.[s] ?? s}
              value={skills[s] ?? 0}
              onChange={(v) => setJob({ skills: { ...skills, [s]: v } })}
            />
          ))}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label className="mb-1 block text-xs">Опыт</Label>
          <Input
            type="number" min={0}
            value={progression.experience ?? 0}
            onChange={(e) => setProgression({ experience: Math.max(0, Number(e.target.value) || 0) })}
          />
        </div>
        <div>
          <Label className="mb-1 block text-xs">Дополнительные очки талантов</Label>
          <Input
            type="number" min={0}
            value={progression.additional_talent_points ?? 0}
            onChange={(e) => setProgression({ additional_talent_points: Math.max(0, Number(e.target.value) || 0) })}
          />
        </div>
      </div>

      <div>
        <Label className="mb-2 block text-xs">
          Таланты
          {chosenTalents.length > 0 && <Badge variant="secondary" className="ml-2">{chosenTalents.length}</Badge>}
        </Label>
        {talents.length ? (
          <div className="flex max-h-52 flex-wrap gap-1 overflow-y-auto rounded-md border p-2">
            {talents.map((t) => {
              const active = chosenTalents.includes(t.identifier)
              return (
                <button
                  key={t.identifier}
                  type="button"
                  title={t.identifier}
                  onClick={() => setProgression({
                    talents: active
                      ? chosenTalents.filter((x) => x !== t.identifier)
                      : [...chosenTalents, t.identifier],
                  })}
                  className={`rounded-md border px-2 py-1 text-xs transition ${
                    active ? "border-primary bg-primary/10" : "hover:bg-accent"
                  }`}
                >
                  {t.name}
                </button>
              )
            })}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Дерево талантов этой профессии не выгружено — задайте идентификаторы во вкладке JSON.
          </p>
        )}
      </div>
    </div>
  )
}

export function EffectsTab({ spec, onChange, reference }: {
  spec: DollSpec
  onChange: (next: DollSpec) => void
  reference?: DollReference
}) {
  const stats = spec.stats ?? []
  const afflictions = spec.health?.afflictions ?? []
  const statTypes = reference?.stat_types ?? []
  const knownAfflictions = reference?.afflictions ?? []
  const [statQuery, setStatQuery] = useState("")

  const setStats = (next: DollSpec["stats"]) => onChange({ ...spec, stats: next })
  const setAfflictions = (next: NonNullable<DollSpec["health"]>["afflictions"]) =>
    onChange({ ...spec, health: { ...(spec.health ?? {}), afflictions: next } })

  const filteredStats = statQuery.trim()
    ? statTypes.filter((s) => s.toLowerCase().includes(statQuery.trim().toLowerCase()))
    : statTypes

  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 flex items-center justify-between">
          <Label className="text-xs">Постоянные модификаторы (статы)</Label>
          <Button
            variant="outline" size="sm"
            onClick={() => setStats([...stats, { stat_type: statTypes[0] ?? "", value: 1 }])}
          >
            <Plus className="mr-1 size-4" /> Добавить
          </Button>
        </div>
        <div className="space-y-1">
          {stats.map((st, i) => (
            <div key={i} className="flex items-center gap-2">
              {statTypes.length ? (
                <Select
                  value={st.stat_type}
                  onValueChange={(v) => setStats(stats.map((s, j) => (j === i ? { ...s, stat_type: v } : s)))}
                >
                  <SelectTrigger className="flex-1"><SelectValue placeholder="Выберите стат" /></SelectTrigger>
                  <SelectContent>
                    <div className="border-b p-1">
                      <input
                        value={statQuery}
                        onChange={(e) => setStatQuery(e.target.value)}
                        onKeyDown={(e) => e.stopPropagation()}
                        placeholder="Поиск"
                        className="w-full bg-transparent px-1 py-0.5 text-sm outline-none"
                      />
                    </div>
                    {filteredStats.slice(0, 60).map((s) => (
                      <SelectItem key={s} value={s}>{s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input
                  value={st.stat_type}
                  onChange={(e) => setStats(stats.map((s, j) => (j === i ? { ...s, stat_type: e.target.value } : s)))}
                  placeholder="StatType"
                  className="flex-1 font-mono text-xs"
                />
              )}
              <Input
                type="number" step="0.1"
                value={st.value}
                onChange={(e) => setStats(stats.map((s, j) => (j === i ? { ...s, value: Number(e.target.value) || 0 } : s)))}
                className="w-24"
              />
              <Button variant="ghost" size="icon-sm" onClick={() => setStats(stats.filter((_, j) => j !== i))}>
                <Trash2 className="size-4 text-destructive" />
              </Button>
            </div>
          ))}
          {!stats.length && (
            <p className="rounded-md border border-dashed py-4 text-center text-sm text-muted-foreground">
              Модификаторов нет — кукла с обычными характеристиками
            </p>
          )}
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <Label className="text-xs">Состояние здоровья</Label>
          <Button
            variant="outline" size="sm"
            onClick={() => setAfflictions([...afflictions, {
              identifier: knownAfflictions[0]?.identifier ?? "", strength: 10,
            }])}
          >
            <Plus className="mr-1 size-4" /> Добавить недуг
          </Button>
        </div>
        <div className="space-y-1">
          {afflictions.map((a, i) => {
            const info = knownAfflictions.find((k) => k.identifier === a.identifier)
            const update = (patch: Partial<typeof a>) =>
              setAfflictions(afflictions.map((x, j) => (j === i ? { ...x, ...patch } : x)))
            return (
              <div key={i} className="flex items-center gap-2">
                {knownAfflictions.length ? (
                  <Select value={a.identifier} onValueChange={(v) => update({ identifier: v })}>
                    <SelectTrigger className="flex-1"><SelectValue placeholder="Выберите недуг" /></SelectTrigger>
                    <SelectContent className="max-h-72">
                      {knownAfflictions.map((k) => (
                        <SelectItem key={k.identifier} value={k.identifier}>{k.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    value={a.identifier}
                    onChange={(e) => update({ identifier: e.target.value })}
                    className="flex-1 font-mono text-xs"
                  />
                )}
                <Input
                  type="number" min={0} step="1"
                  value={a.strength ?? 0}
                  onChange={(e) => update({ strength: Number(e.target.value) || 0 })}
                  title="Сила недуга"
                  className="w-24"
                />
                <Select
                  value={a.limb ?? "none"}
                  onValueChange={(v) => update({ limb: v === "none" ? undefined : v })}
                  disabled={info ? !info.limb_specific : false}
                >
                  <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Всё тело</SelectItem>
                    {LIMBS.map((l) => <SelectItem key={l} value={l}>{l}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button variant="ghost" size="icon-sm" onClick={() => setAfflictions(afflictions.filter((_, j) => j !== i))}>
                  <Trash2 className="size-4 text-destructive" />
                </Button>
              </div>
            )
          })}
          {!afflictions.length && (
            <p className="rounded-md border border-dashed py-4 text-center text-sm text-muted-foreground">
              Кукла заспавнится здоровой
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
