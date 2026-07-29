import { useMemo, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Search } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { useItemCatalog, itemDisplayName } from "@/lib/items"
import { api } from "@/lib/api"
import type { AvailableKvalKit, RewardReference } from "./detail-types"

/** Профессия «все» — бэкенд хранит её отдельным ключом "all" (см.
 * set_reward_stat/set_reward_talent в routes/admin.py). */
const ALL_JOBS = "all"

interface RewardsSectionProps {
  playerId: string
  canEdit: boolean
  kvalKits: Record<string, number>
  items: Record<string, number>
  stats: Record<string, number | Record<string, number>>
  talents: Record<string, string[]>
  availableKvalKits: AvailableKvalKit[]
  reference: RewardReference | undefined
  onChanged: () => void
}

/** Русское название профессии по идентификатору; неизвестный (модовый или
 * из старой награды) остаётся как есть — иначе он просто исчез бы с глаз. */
function jobName(reference: RewardReference | undefined, identifier: string): string {
  if (identifier === ALL_JOBS) return "Все профессии"
  return reference?.jobs.find((j) => j.identifier === identifier)?.name ?? identifier
}

function talentName(reference: RewardReference | undefined, job: string, identifier: string): string {
  return reference?.talents?.[job]?.find((t) => t.identifier === identifier)?.name ?? identifier
}

/** Выпадающий список профессий. Значения — идентификаторы, подписи — русские
 * названия; при пустом справочнике список профессий пуст, и остаётся ручной
 * ввод (см. вызывающие карточки). */
function JobSelect({ value, onChange, reference, includeAll, placeholder = "Профессия…", className }: {
  value: string
  onChange: (v: string) => void
  reference: RewardReference | undefined
  includeAll?: boolean
  placeholder?: string
  className?: string
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className={className}><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        {includeAll && <SelectItem value={ALL_JOBS}>Все профессии</SelectItem>}
        {(reference?.jobs ?? []).map((j) => (
          <SelectItem key={j.identifier} value={j.identifier}>{j.name}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export function RewardsSection({
  playerId, canEdit, kvalKits, items, stats, talents, availableKvalKits, reference, onChanged,
}: RewardsSectionProps) {
  const queryClient = useQueryClient()

  const invalidateAndRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ["player-detail", playerId] })
    onChanged()
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <KvalKitsCard playerId={playerId} canEdit={canEdit} kvalKits={kvalKits} available={availableKvalKits} reference={reference} onDone={invalidateAndRefresh} />
      <ItemsCard playerId={playerId} canEdit={canEdit} items={items} onDone={invalidateAndRefresh} />
      <StatsCard playerId={playerId} canEdit={canEdit} stats={stats} reference={reference} onDone={invalidateAndRefresh} />
      <TalentsCard playerId={playerId} canEdit={canEdit} talents={talents} reference={reference} onDone={invalidateAndRefresh} />
    </div>
  )
}

function KvalKitsCard({ playerId, canEdit, kvalKits, available, reference, onDone }: {
  playerId: string; canEdit: boolean; kvalKits: Record<string, number>
  available: AvailableKvalKit[]; reference: RewardReference | undefined; onDone: () => void
}) {
  const [jobId, setJobId] = useState("")

  const set = useMutation({
    mutationFn: (v: { job_id: string; level: number }) => api.post(`/api/v1/admin/players/${playerId}/rewards/kval-kits`, v),
    onSuccess: () => { toast.success("Квал-кит выдан"); setJobId(""); onDone() },
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: (job_id: string) => api.delete(`/api/v1/admin/players/${playerId}/rewards/kval-kits?job_id=${encodeURIComponent(job_id)}`),
    onSuccess: () => { toast.success("Квал-кит снят"); onDone() },
  })

  const grantable = available.filter((k) => !(k.job in kvalKits) || kvalKits[k.job] !== k.level)

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">🎓 Квалификационные киты</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {Object.entries(kvalKits).map(([job, level]) => (
            <Badge key={job} variant="outline" className="gap-1.5 border-info/40 text-info">
              {jobName(reference, job)} — ур. {level}
              {canEdit && <button onClick={() => remove.mutate(job)} className="ml-0.5 hover:text-destructive">✕</button>}
            </Badge>
          ))}
          {!Object.keys(kvalKits).length && <span className="text-sm text-muted-foreground">Нет квалификаций</span>}
        </div>
        {canEdit && (
          <div className="flex gap-2">
            <Select value={jobId} onValueChange={setJobId}>
              <SelectTrigger className="flex-1"><SelectValue placeholder="Выбрать квал-кит…" /></SelectTrigger>
              <SelectContent>
                {grantable.map((k) => (
                  <SelectItem key={`${k.job}-${k.level}`} value={`${k.job}|${k.level}`}>
                    {jobName(reference, k.job)} — ур. {k.level}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              disabled={!jobId}
              onClick={() => { const [job, level] = jobId.split("|"); set.mutate({ job_id: job, level: Number(level) }) }}
            >
              Выдать
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ItemsCard({ playerId, canEdit, items, onDone }: {
  playerId: string; canEdit: boolean; items: Record<string, number>; onDone: () => void
}) {
  const { data: catalog } = useItemCatalog()
  const [search, setSearch] = useState("")
  const [itemId, setItemId] = useState("")
  const [qty, setQty] = useState("1")

  const set = useMutation({
    mutationFn: (v: { item_id: string; quantity: number }) => api.post(`/api/v1/admin/players/${playerId}/rewards/items`, v),
    onSuccess: () => { toast.success("Предмет выдан"); setItemId(""); setSearch(""); setQty("1"); onDone() },
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: (item_id: string) => api.delete(`/api/v1/admin/players/${playerId}/rewards/items?item_id=${encodeURIComponent(item_id)}`),
    onSuccess: () => { toast.success("Предмет удалён"); onDone() },
  })

  // Тот же приём, что на складе Диспетчерской: каталог в пару сотен позиций
  // фильтруется на клиенте мгновенно, отдельный компонент-комбобокс ради
  // этого заводить незачем.
  const options = useMemo(() => {
    if (!catalog) return []
    const q = search.trim().toLowerCase()
    if (!q) return []
    return Object.entries(catalog)
      .filter(([id, entry]) => entry.name.toLowerCase().includes(q) || id.toLowerCase().includes(q))
      .sort((a, b) => a[1].name.localeCompare(b[1].name, "ru"))
      .slice(0, 40)
  }, [catalog, search])

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">📦 Дополнительные предметы</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {Object.entries(items).map(([id, q]) => (
            <Badge key={id} variant="outline" className="gap-1.5 border-warning/40 text-warning">
              <ItemIcon identifier={id} size={16} />
              {itemDisplayName(catalog, id)} ×{q}
              {canEdit && <button onClick={() => remove.mutate(id)} className="ml-0.5 hover:text-destructive">✕</button>}
            </Badge>
          ))}
          {!Object.keys(items).length && <span className="text-sm text-muted-foreground">Нет доп. предметов</span>}
        </div>
        {canEdit && (
          <div className="space-y-2">
            {itemId ? (
              <div className="flex items-center gap-2 rounded-md border p-2">
                <ItemIcon identifier={itemId} size={28} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{itemDisplayName(catalog, itemId)}</div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground">{itemId}</div>
                </div>
                <Button variant="ghost" size="sm" onClick={() => setItemId("")}>Изменить</Button>
              </div>
            ) : (
              <>
                <div className="relative">
                  <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    className="pl-8"
                    placeholder="Поиск предмета по названию или ID…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                {options.length > 0 && (
                  <div className="max-h-48 overflow-y-auto rounded-md border">
                    {options.map(([id, entry]) => (
                      <button
                        key={id}
                        onClick={() => setItemId(id)}
                        className="flex w-full items-center gap-2 border-b px-2.5 py-1.5 text-left text-sm last:border-b-0 hover:bg-accent"
                      >
                        <ItemIcon identifier={id} size={20} />
                        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                        <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{id}</span>
                      </button>
                    ))}
                  </div>
                )}
                {/* Модовый предмет каталогом не покрыт — оставляем прямой ввод
                    идентификатора, иначе выдать его стало бы невозможно. */}
                {search.trim() && !options.length && (
                  <Button variant="outline" size="sm" className="w-full" onClick={() => setItemId(search.trim())}>
                    Использовать «{search.trim()}» как идентификатор
                  </Button>
                )}
              </>
            )}
            {itemId && (
              <div className="flex gap-2">
                <Input type="number" min={1} placeholder="кол-во" value={qty} onChange={(e) => setQty(e.target.value)} className="w-24" />
                <Button size="sm" className="flex-1" onClick={() => set.mutate({ item_id: itemId, quantity: Number(qty) || 1 })}>
                  Выдать
                </Button>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function StatsCard({ playerId, canEdit, stats, reference, onDone }: {
  playerId: string; canEdit: boolean; stats: Record<string, number | Record<string, number>>
  reference: RewardReference | undefined; onDone: () => void
}) {
  const [jobId, setJobId] = useState("")
  const [statKey, setStatKey] = useState("")
  const [value, setValue] = useState("0")

  const set = useMutation({
    mutationFn: (v: { job_id: string; stat_key?: string; value: number }) => api.post(`/api/v1/admin/players/${playerId}/rewards/stats`, v),
    onSuccess: () => { toast.success("Бонус к стату выдан"); setJobId(""); setStatKey(""); setValue("0"); onDone() },
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: (v: { job_id: string; stat_key?: string }) => {
      const params = new URLSearchParams({ job_id: v.job_id })
      if (v.stat_key) params.set("stat_key", v.stat_key)
      return api.delete(`/api/v1/admin/players/${playerId}/rewards/stats?${params}`)
    },
    onSuccess: () => { toast.success("Бонус снят"); onDone() },
  })

  const skillLabels = reference?.skill_labels ?? {}
  const skillKeys = Object.keys(skillLabels)

  const rows: { job: string; key?: string; value: number }[] = []
  for (const [job, v] of Object.entries(stats)) {
    if (typeof v === "number") rows.push({ job, value: v })
    else for (const [k, vv] of Object.entries(v)) rows.push({ job, key: k, value: vv })
  }

  // Для «всех профессий» бэкенд хранит одно число без ключа навыка
  // (stats.all = N), поэтому поле навыка в этом режиме не нужно.
  const isAll = jobId === ALL_JOBS
  const canSubmit = !!jobId && (isAll || !!statKey)

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">📊 Бонусы к навыкам</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {rows.map((r) => (
            <Badge key={`${r.job}-${r.key ?? ""}`} variant="outline" className="gap-1.5 border-success/40 text-success">
              {jobName(reference, r.job)}{r.key ? ` · ${skillLabels[r.key] ?? r.key}` : ""}: {r.value > 0 ? "+" : ""}{r.value}
              {canEdit && <button onClick={() => remove.mutate({ job_id: r.job, stat_key: r.key })} className="ml-0.5 hover:text-destructive">✕</button>}
            </Badge>
          ))}
          {!rows.length && <span className="text-sm text-muted-foreground">Нет бонусов</span>}
        </div>
        {canEdit && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              <JobSelect
                className="min-w-[160px] flex-1"
                value={jobId}
                onChange={(v) => { setJobId(v); if (v === ALL_JOBS) setStatKey("") }}
                reference={reference}
                includeAll
              />
              {!isAll && (
                skillKeys.length > 0 ? (
                  <Select value={statKey} onValueChange={setStatKey}>
                    <SelectTrigger className="min-w-[150px] flex-1"><SelectValue placeholder="Навык…" /></SelectTrigger>
                    <SelectContent>
                      {skillKeys.map((k) => <SelectItem key={k} value={k}>{skillLabels[k]}</SelectItem>)}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input placeholder="ключ навыка" value={statKey} onChange={(e) => setStatKey(e.target.value)} className="min-w-[150px] flex-1" />
                )
              )}
              <Input type="number" placeholder="значение" value={value} onChange={(e) => setValue(e.target.value)} className="w-24" />
              <Button
                size="sm"
                disabled={!canSubmit}
                onClick={() => set.mutate({ job_id: jobId, stat_key: statKey || undefined, value: Number(value) || 0 })}
              >
                Выдать
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {isAll
                ? "Для «всех профессий» бонус применяется ко всем навыкам сразу — навык выбирать не нужно."
                : "Выберите профессию и навык, к которому добавится бонус."}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function TalentsCard({ playerId, canEdit, talents, reference, onDone }: {
  playerId: string; canEdit: boolean; talents: Record<string, string[]>
  reference: RewardReference | undefined; onDone: () => void
}) {
  const [jobId, setJobId] = useState("")
  const [talentId, setTalentId] = useState("")

  const add = useMutation({
    mutationFn: (v: { job_id: string; talent_id: string }) => api.post(`/api/v1/admin/players/${playerId}/rewards/talents`, v),
    onSuccess: () => { toast.success("Талант выдан"); setTalentId(""); onDone() },
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: (v: { job_id: string; talent_id: string }) => api.delete(`/api/v1/admin/players/${playerId}/rewards/talents?job_id=${encodeURIComponent(v.job_id)}&talent_id=${encodeURIComponent(v.talent_id)}`),
    onSuccess: () => { toast.success("Талант снят"); onDone() },
  })

  // Дерево талантов своё у каждой профессии — список подставляется под
  // выбранную. Для «всех профессий» общего дерева нет, остаётся ручной ввод.
  const jobTalents = reference?.talents?.[jobId] ?? []
  const alreadyGranted = new Set(talents[jobId] ?? [])
  const grantable = jobTalents.filter((t) => !alreadyGranted.has(t.identifier))

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">✨ Таланты</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-2">
          {Object.entries(talents).map(([job, ids]) => (
            <div key={job} className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs font-semibold text-muted-foreground">{jobName(reference, job)}:</span>
              {ids.map((id) => (
                <Badge key={id} variant="outline" className="gap-1.5 border-primary/40 text-primary">
                  {talentName(reference, job, id)}
                  {canEdit && <button onClick={() => remove.mutate({ job_id: job, talent_id: id })} className="ml-0.5 hover:text-destructive">✕</button>}
                </Badge>
              ))}
            </div>
          ))}
          {!Object.keys(talents).length && <span className="text-sm text-muted-foreground">Нет талантов</span>}
        </div>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <JobSelect
              className="min-w-[160px] flex-1"
              value={jobId}
              onChange={(v) => { setJobId(v); setTalentId("") }}
              reference={reference}
              includeAll
            />
            {jobId && grantable.length > 0 ? (
              <Select value={talentId} onValueChange={setTalentId}>
                <SelectTrigger className="min-w-[180px] flex-1"><SelectValue placeholder="Талант…" /></SelectTrigger>
                <SelectContent>
                  {grantable.map((t) => <SelectItem key={t.identifier} value={t.identifier}>{t.name}</SelectItem>)}
                </SelectContent>
              </Select>
            ) : (
              <Input
                placeholder={jobId && jobId !== ALL_JOBS ? "talent_id (нет в справочнике)" : "talent_id"}
                value={talentId}
                onChange={(e) => setTalentId(e.target.value)}
                className="min-w-[180px] flex-1"
              />
            )}
            <Button
              size="sm"
              disabled={!jobId || !talentId.trim()}
              onClick={() => add.mutate({ job_id: jobId, talent_id: talentId.trim() })}
            >
              Выдать
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
