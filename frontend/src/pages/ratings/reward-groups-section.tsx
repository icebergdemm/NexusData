import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { AdaptiveIcon } from "@/components/layout/PlatformIcon"
import { BriefcaseBusiness, ChartNoAxesColumn, Package, Pencil, Save, Sparkles, Trash2 } from "lucide-react"
import { api } from "@/lib/api"
import { KvRowEditor, type KvRow } from "./kv-rows"
import type { JobItemsVariation, JobReferenceJob, RewardGroup } from "./rewards-types"

function toKvRows(obj: Record<string, unknown>, stringify: (v: unknown) => string): KvRow[] {
  return Object.entries(obj).map(([key, value]) => ({ key, value: stringify(value) }))
}

function parseItemsKv(rows: KvRow[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const { key, value } of rows) {
    const k = key.trim()
    if (!k) continue
    const n = parseInt(value, 10)
    out[k] = isNaN(n) ? 1 : n
  }
  return out
}

function parseStatsKv(rows: KvRow[]): Record<string, number | string> {
  const out: Record<string, number | string> = {}
  for (const { key, value } of rows) {
    const k = key.trim()
    if (!k) continue
    const n = parseFloat(value)
    out[k] = isNaN(n) ? value : n
  }
  return out
}

function parseTalentsKv(rows: KvRow[]): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const { key, value } of rows) {
    const k = key.trim()
    if (!k) continue
    out[k] = value.split(",").map((v) => v.trim()).filter(Boolean)
  }
  return out
}

// ── Предметы по ролям (с вариациями снаряжения) ────────────────────────────
//
// Один блок на роль: вариация 1 (`items`) обязательна — это одновременно и
// сама вариация 1, и фолбэк для 2/3, если те не заполнены (см. докстринг
// _resolve_job_variation_items в services_playtime_stats.py). Блок без
// вариации 1 при сохранении отбрасывается целиком — "заполнена только
// вариация 2" не бывает валидным состоянием, роль либо настроена, либо нет.

interface JobItemBlock {
  jobId: string
  items: KvRow[]
  v2: KvRow[]
  v3: KvRow[]
}

function toJobItemBlocks(jobItems: Record<string, JobItemsVariation> | undefined): JobItemBlock[] {
  return Object.entries(jobItems || {}).map(([jobId, cfg]) => ({
    jobId,
    items: toKvRows(cfg.items || {}, String),
    v2: toKvRows(cfg.variation_2_items || {}, String),
    v3: toKvRows(cfg.variation_3_items || {}, String),
  }))
}

function parseJobItemBlocks(blocks: JobItemBlock[]): Record<string, JobItemsVariation> {
  const out: Record<string, JobItemsVariation> = {}
  for (const b of blocks) {
    if (!b.jobId) continue
    const items = parseItemsKv(b.items)
    if (!Object.keys(items).length) continue
    const cfg: JobItemsVariation = { items }
    const v2 = parseItemsKv(b.v2)
    const v3 = parseItemsKv(b.v3)
    if (Object.keys(v2).length) cfg.variation_2_items = v2
    if (Object.keys(v3).length) cfg.variation_3_items = v3
    out[b.jobId] = cfg
  }
  return out
}

function JobItemsEditor({ blocks, onChange, jobs }: {
  blocks: JobItemBlock[]
  onChange: (next: JobItemBlock[]) => void
  jobs: JobReferenceJob[]
}) {
  const jobName = (id: string) => jobs.find((j) => j.identifier === id)?.name ?? id
  const usedIds = new Set(blocks.map((b) => b.jobId))
  const available = jobs.filter((j) => !usedIds.has(j.identifier))

  const update = (i: number, patch: Partial<JobItemBlock>) => {
    onChange(blocks.map((b, idx) => (idx === i ? { ...b, ...patch } : b)))
  }
  const remove = (i: number) => onChange(blocks.filter((_, idx) => idx !== i))
  const addRole = (jobId: string) => onChange([...blocks, { jobId, items: [], v2: [], v3: [] }])

  return (
    <div className="space-y-2">
      {blocks.map((b, i) => (
        <div key={b.jobId} className="space-y-2 rounded-md border p-2">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 font-semibold"><AdaptiveIcon emoji="🎭" icon={BriefcaseBusiness} />{jobName(b.jobId)}</span>
            <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => remove(i)}>✕ Убрать роль</Button>
          </div>
          <Tabs defaultValue="1">
            <TabsList className="w-full">
              <TabsTrigger value="1" className="flex-1 text-xs">Вариация 1</TabsTrigger>
              <TabsTrigger value="2" className="flex-1 text-xs">Вариация 2</TabsTrigger>
              <TabsTrigger value="3" className="flex-1 text-xs">Вариация 3</TabsTrigger>
            </TabsList>
            <TabsContent value="1" className="mt-2">
              <p className="mb-1 text-[11px] text-muted-foreground">
                Обязательна: заодно фолбэк для вариаций 2 и 3, если те ниже не заполнены.
              </p>
              <KvRowEditor rows={b.items} onChange={(rows) => update(i, { items: rows })} keyPlaceholder="item_id" valuePlaceholder="кол-во" addLabel="+ Добавить предмет" />
            </TabsContent>
            <TabsContent value="2" className="mt-2">
              <p className="mb-1 text-[11px] text-muted-foreground">Пусто — выдастся вариация 1.</p>
              <KvRowEditor rows={b.v2} onChange={(rows) => update(i, { v2: rows })} keyPlaceholder="item_id" valuePlaceholder="кол-во" addLabel="+ Добавить предмет" />
            </TabsContent>
            <TabsContent value="3" className="mt-2">
              <p className="mb-1 text-[11px] text-muted-foreground">Пусто — выдастся вариация 1 (даже если вариация 2 заполнена).</p>
              <KvRowEditor rows={b.v3} onChange={(rows) => update(i, { v3: rows })} keyPlaceholder="item_id" valuePlaceholder="кол-во" addLabel="+ Добавить предмет" />
            </TabsContent>
          </Tabs>
        </div>
      ))}
      {available.length > 0 && (
        <Select value="" onValueChange={addRole}>
          <SelectTrigger className="border-dashed"><SelectValue placeholder="+ Добавить роль" /></SelectTrigger>
          <SelectContent>
            {available.map((j) => <SelectItem key={j.identifier} value={j.identifier}>{j.name}</SelectItem>)}
          </SelectContent>
        </Select>
      )}
      {!blocks.length && !available.length && (
        <p className="text-xs text-muted-foreground">Справочник профессий не загрузился — предметы по ролям недоступны.</p>
      )}
    </div>
  )
}

export function RewardGroupsSection({ groups, canEdit }: { groups: RewardGroup[]; canEdit: boolean }) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [name, setName] = useState("")
  const [id, setId] = useState("")
  const [itemRows, setItemRows] = useState<KvRow[]>([])
  const [statRows, setStatRows] = useState<KvRow[]>([])
  const [talentRows, setTalentRows] = useState<KvRow[]>([])
  const [jobItemBlocks, setJobItemBlocks] = useState<JobItemBlock[]>([])
  const [removeTarget, setRemoveTarget] = useState<RewardGroup | null>(null)

  const { data: jobRef } = useQuery({
    queryKey: ["reward-groups-job-reference"],
    queryFn: () => api.get<{ jobs: JobReferenceJob[] }>("/api/v1/admin/reward-groups/job-reference"),
    staleTime: Infinity,   // выгрузка из игрового контента, при работе не меняется
  })
  const jobs = jobRef?.jobs ?? []

  const persist = useMutation({
    mutationFn: (next: RewardGroup[]) => api.post("/api/v1/admin/reward-groups", next),
    onSuccess: () => { toast.success("Сохранено"); queryClient.invalidateQueries({ queryKey: ["reward-groups"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const openCreate = () => {
    setEditingId(null); setName(""); setId(""); setItemRows([]); setStatRows([]); setTalentRows([]); setJobItemBlocks([])
    setDialogOpen(true)
  }
  const openEdit = (g: RewardGroup) => {
    setEditingId(g.id); setName(g.name); setId(g.id)
    setItemRows(toKvRows(g.items || {}, (v) => String(v)))
    setStatRows(toKvRows(g.stats || {}, (v) => (typeof v === "object" ? JSON.stringify(v) : String(v))))
    setTalentRows(toKvRows(g.talents || {}, (v) => (Array.isArray(v) ? v.join(",") : String(v))))
    setJobItemBlocks(toJobItemBlocks(g.job_items))
    setDialogOpen(true)
  }

  const save = () => {
    if (!name.trim() || !id.trim()) { toast.error("Заполните название и ID"); return }
    const jobItems = parseJobItemBlocks(jobItemBlocks)
    const grp: RewardGroup = {
      id: id.trim(), name: name.trim(),
      items: parseItemsKv(itemRows), stats: parseStatsKv(statRows), talents: parseTalentsKv(talentRows),
      ...(Object.keys(jobItems).length ? { job_items: jobItems } : {}),
    }
    let next: RewardGroup[]
    if (editingId) {
      next = groups.map((g) => (g.id === editingId ? grp : g))
    } else {
      if (groups.some((g) => g.id === grp.id)) { toast.error("Группа с таким ID уже есть"); return }
      next = [...groups, grp]
    }
    persist.mutate(next)
    setDialogOpen(false)
  }

  const remove = (g: RewardGroup) => {
    setRemoveTarget(null)
    persist.mutate(groups.filter((x) => x.id !== g.id))
  }

  const jobName = (jid: string) => jobs.find((j) => j.identifier === jid)?.name ?? jid

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-1.5 text-sm"><AdaptiveIcon emoji="📦" icon={Package} />Группы наград</CardTitle>
        {canEdit && <Button size="sm" onClick={openCreate}>+ Создать группу</Button>}
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-muted-foreground">Группы предметов, статов, талантов и ролевых предметов, которые выдаются игрокам по условиям ниже.</p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {groups.map((g) => {
            const items = Object.entries(g.items || {})
            const stats = Object.entries(g.stats || {})
            const talents = Object.entries(g.talents || {})
            const jobItems = Object.entries(g.job_items || {})
            const empty = !items.length && !stats.length && !talents.length && !jobItems.length
            return (
              <div key={g.id} className="flex flex-col gap-2 rounded-md border bg-card p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold">{g.name}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">{g.id}</span>
                </div>
                {items.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {items.map(([k, v]) => <Badge key={k} variant="outline" className="border-warning/40 text-warning">{k} ×{v}</Badge>)}
                  </div>
                )}
                {stats.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {stats.map(([k, v]) => <Badge key={k} variant="outline" className="border-success/40 text-success">{k}: {typeof v === "object" ? JSON.stringify(v) : v}</Badge>)}
                  </div>
                )}
                {talents.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {talents.map(([k, v]) => <Badge key={k} variant="outline" className="border-info/40 text-info">{k}: {v.join(", ")}</Badge>)}
                  </div>
                )}
                {jobItems.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {jobItems.map(([jid, cfg]) => {
                      const extra = [cfg.variation_2_items && "в2", cfg.variation_3_items && "в3"].filter(Boolean).join("+")
                      return (
                        <Badge key={jid} variant="outline" className="border-primary/40 text-primary">
                          <AdaptiveIcon emoji="🎭" icon={BriefcaseBusiness} /> {jobName(jid)}: {Object.keys(cfg.items || {}).length} предм.{extra ? ` (${extra})` : ""}
                        </Badge>
                      )
                    })}
                  </div>
                )}
                {empty && <div className="text-xs text-muted-foreground">Пустая группа</div>}
                {canEdit && (
                  <div className="mt-auto flex gap-1.5 pt-1">
                    <Button size="sm" variant="outline" onClick={() => openEdit(g)}><AdaptiveIcon emoji="✏️" icon={Pencil} /> Изменить</Button>
                    <Button size="sm" variant="destructive" onClick={() => setRemoveTarget(g)}><AdaptiveIcon emoji="🗑" icon={Trash2} /></Button>
                  </div>
                )}
              </div>
            )
          })}
          {!groups.length && <div className="col-span-full py-8 text-center text-muted-foreground">Групп пока нет. Создайте первую!</div>}
        </div>
      </CardContent>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? "Изменить группу" : "Создать группу наград"}</DialogTitle>
            <DialogDescription>Наборы предметов, статов, талантов и ролевых предметов, которые можно выдать игроку как награду.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div><Label className="mb-1.5 block">Название группы *</Label><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Награда за 1 место" /></div>
            <div><Label className="mb-1.5 block">Буквенный ID *</Label><Input value={id} onChange={(e) => setId(e.target.value)} placeholder="top1_reward" disabled={!!editingId} /></div>

            <div>
              <Label className="mb-1.5 flex items-center gap-1.5"><AdaptiveIcon emoji="📦" icon={Package} />Предметы (item_id → количество)</Label>
              <p className="mb-1 text-[11px] text-muted-foreground">Выдаются независимо от того, на какой профессии игрок зашёл.</p>
              <KvRowEditor rows={itemRows} onChange={setItemRows} keyPlaceholder="item_id" valuePlaceholder="кол-во" addLabel="+ Добавить предмет" />
            </div>
            <div>
              <Label className="mb-1.5 flex items-center gap-1.5"><AdaptiveIcon emoji="📊" icon={ChartNoAxesColumn} />Статы (ключ → значение)</Label>
              <KvRowEditor rows={statRows} onChange={setStatRows} keyPlaceholder="ключ (all/job)" valuePlaceholder="значение" addLabel="+ Добавить стат" />
            </div>
            <div>
              <Label className="mb-1.5 flex items-center gap-1.5"><AdaptiveIcon emoji="✨" icon={Sparkles} />Таланты (профессия → talent1,talent2)</Label>
              <KvRowEditor rows={talentRows} onChange={setTalentRows} keyPlaceholder="профессия" valuePlaceholder="talent1,talent2" addLabel="+ Добавить талант" />
            </div>
            <div>
              <Label className="mb-1.5 flex items-center gap-1.5"><AdaptiveIcon emoji="🎭" icon={BriefcaseBusiness} />Предметы по ролям</Label>
              <p className="mb-1 text-[11px] text-muted-foreground">
                Выдаются, только если игрок зашёл на выбранную роль — под конкретную вариацию его снаряжения
                (см. подсказки в каждой вкладке ниже про фолбэк вариаций).
              </p>
              <JobItemsEditor blocks={jobItemBlocks} onChange={setJobItemBlocks} jobs={jobs} />
            </div>
          </div>
          <DialogFooter><Button onClick={save}><AdaptiveIcon emoji="💾" icon={Save} /> Сохранить</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={removeTarget != null}
        onOpenChange={(o) => !o && setRemoveTarget(null)}
        title="Удалить группу наград?"
        description={removeTarget ? `Группа «${removeTarget.name}» будет удалена. Условия наград, ссылающиеся на неё, перестанут работать.` : undefined}
        onConfirm={() => removeTarget && remove(removeTarget)}
      />
    </Card>
  )
}
