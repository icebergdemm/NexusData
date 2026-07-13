import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Wrench } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import { JsonField } from "./json-field"
import { baseKitItemsList, qualKitChips } from "./preview"

interface KitRow { id: number; job_id: string; kit_level: number; items: unknown }
interface KitsResponse { kits: KitRow[]; qual_kits: KitRow[]; all_job_ids: string[]; all_qual_base_jobs: string[]; kits_mapping: Record<string, string> }

const QUAL_LEVEL_NAMES: Record<number, string> = { 1: "I", 2: "II", 3: "III", 4: "IV", 5: "V" }

export function KitsPage() {
  const [newKit, setNewKit] = useState({ job_id: "", kit_level: "0", items: "[]" })
  const [newQual, setNewQual] = useState({
    base_job: "", kit_level: "1",
    items: '{\n  "item_id_1": 1,\n  "ar_delete": ["old_item"],\n  "talents": ["talent_id_1"],\n  "stats": {"all": 0}\n}',
  })
  const [editingBaseId, setEditingBaseId] = useState<number | null>(null)
  const [editingQualId, setEditingQualId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState<{ job_id: string; kit_level: string; items: string }>({ job_id: "", kit_level: "", items: "" })
  const [deleteBaseTarget, setDeleteBaseTarget] = useState<KitRow | null>(null)
  const [deleteQualTarget, setDeleteQualTarget] = useState<KitRow | null>(null)
  const queryClient = useQueryClient()

  const { data } = useQuery({ queryKey: ["kits"], queryFn: () => api.get<KitsResponse>("/api/v1/admin/kits") })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["kits"] })

  const addKit = useMutation({
    mutationFn: () => api.post("/api/v1/admin/kits", { job_id: newKit.job_id, kit_level: Number(newKit.kit_level), items: JSON.parse(newKit.items || "[]") }),
    onSuccess: () => { toast.success("Кит создан"); setNewKit({ job_id: "", kit_level: "0", items: "[]" }); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })
  const updateKit = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/kits/${id}`, { job_id: editDraft.job_id, kit_level: Number(editDraft.kit_level), items: JSON.parse(editDraft.items || "[]") }),
    onSuccess: () => { toast.success("Кит обновлён"); setEditingBaseId(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })
  const deleteKit = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/kits/${id}`),
    onSuccess: () => { toast.success("Кит удалён"); invalidate() },
  })

  const addQual = useMutation({
    mutationFn: () => api.post("/api/v1/admin/qual-kits", { base_job: newQual.base_job, kit_level: Number(newQual.kit_level), items: JSON.parse(newQual.items || "{}") }),
    onSuccess: () => { toast.success("Квал-кит создан"); setNewQual({ ...newQual, base_job: "" }); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })
  const updateQual = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/qual-kits/${id}`, { kit_level: Number(editDraft.kit_level), items: JSON.parse(editDraft.items || "{}") }),
    onSuccess: () => { toast.success("Квал-кит обновлён"); setEditingQualId(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })
  const deleteQual = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/qual-kits/${id}`),
    onSuccess: () => { toast.success("Квал-кит удалён"); invalidate() },
  })

  const startEditBase = (k: KitRow) => {
    setEditingBaseId(k.id)
    setEditDraft({ job_id: k.job_id, kit_level: String(k.kit_level), items: JSON.stringify(k.items, null, 2) })
  }
  const startEditQual = (k: KitRow) => {
    setEditingQualId(k.id)
    setEditDraft({ job_id: k.job_id, kit_level: String(k.kit_level), items: JSON.stringify(k.items, null, 2) })
  }

  return (
    <div>
      <PageHeader title="Редактирование китов" description="Стартовые наборы предметов по профессиям и квалификациям" icon={Wrench} />
      <Tabs defaultValue="base" className="space-y-4">
      <TabsList>
        <TabsTrigger value="base">Базовые киты</TabsTrigger>
        <TabsTrigger value="qual">Квалификационные киты</TabsTrigger>
      </TabsList>

      <TabsContent value="base" className="space-y-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">Добавить новый кит</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
              <div><Label>Профессия (job_id) *</Label><Input value={newKit.job_id} onChange={(e) => setNewKit((f) => ({ ...f, job_id: e.target.value }))} placeholder="Captain, Engineer..." /></div>
              <div>
                <Label>Уровень кита *</Label>
                <Select value={newKit.kit_level} onValueChange={(v) => setNewKit((f) => ({ ...f, kit_level: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Object.entries(data?.kits_mapping ?? {}).map(([id, name]) => <SelectItem key={id} value={id}>{id} - {name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label>Предметы (JSON-массив)</Label>
              <JsonField value={newKit.items} onChange={(v) => setNewKit((f) => ({ ...f, items: v }))} rows={3} />
              <p className="mt-1 text-[11px] text-muted-foreground">Например: <code>[&quot;item_id_1&quot;, &quot;item_id_2&quot;]</code></p>
            </div>
            <Button disabled={!newKit.job_id.trim()} onClick={() => addKit.mutate()}>＋ Создать кит</Button>
          </CardContent>
        </Card>

        <div className="grid gap-3 sm:grid-cols-2">
          {data?.kits.map((k) => {
            const itemsList = baseKitItemsList(k.items)
            const isEditing = editingBaseId === k.id
            return (
              <Card key={k.id}>
                <CardHeader className="flex flex-row items-center justify-between space-y-0">
                  <div>
                    <div className="font-semibold">{k.job_id}</div>
                    <div className="text-xs text-muted-foreground">{data.kits_mapping[String(k.kit_level)] ?? `Уровень ${k.kit_level}`}</div>
                  </div>
                  <div className="flex gap-1.5">
                    <Button size="sm" variant="outline" onClick={() => (isEditing ? setEditingBaseId(null) : startEditBase(k))}>{isEditing ? "Отмена" : "✏️"}</Button>
                    <Button size="sm" variant="destructive" onClick={() => setDeleteBaseTarget(k)}>🗑</Button>
                  </div>
                </CardHeader>
                <CardContent>
                  {isEditing ? (
                    <div className="space-y-2">
                      <div className="grid gap-2 sm:grid-cols-2">
                        <div><Label className="text-xs">Профессия</Label><Input value={editDraft.job_id} onChange={(e) => setEditDraft((d) => ({ ...d, job_id: e.target.value }))} /></div>
                        <div>
                          <Label className="text-xs">Уровень</Label>
                          <Select value={editDraft.kit_level} onValueChange={(v) => setEditDraft((d) => ({ ...d, kit_level: v }))}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>{Object.entries(data?.kits_mapping ?? {}).map(([id, name]) => <SelectItem key={id} value={id}>{id} - {name}</SelectItem>)}</SelectContent>
                          </Select>
                        </div>
                      </div>
                      <JsonField value={editDraft.items} onChange={(v) => setEditDraft((d) => ({ ...d, items: v }))} rows={4} />
                      <Button size="sm" onClick={() => updateKit.mutate(k.id)}>💾 Сохранить</Button>
                    </div>
                  ) : itemsList.length ? (
                    <>
                      <div className="flex flex-wrap gap-1">
                        {itemsList.slice(0, 8).map((item, i) => <Badge key={i} variant="outline" className="font-mono">{item}</Badge>)}
                        {itemsList.length > 8 && <Badge variant="outline">+{itemsList.length - 8} ещё</Badge>}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">{itemsList.length} предметов</div>
                    </>
                  ) : (
                    <div className="text-xs italic text-muted-foreground">Нет предметов</div>
                  )}
                </CardContent>
              </Card>
            )
          })}
          {!data?.kits.length && <div className="col-span-full py-8 text-center text-muted-foreground">Китов пока нет</div>}
        </div>
      </TabsContent>

      <TabsContent value="qual" className="space-y-4">
        <Card>
          <CardHeader><CardTitle className="text-sm">Добавить квалификационный кит</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
              <div><Label>Профессия (без _k) *</Label><Input value={newQual.base_job} onChange={(e) => setNewQual((f) => ({ ...f, base_job: e.target.value }))} placeholder="assistant" /></div>
              <div>
                <Label>Уровень квалификации</Label>
                <Select value={newQual.kit_level} onValueChange={(v) => setNewQual((f) => ({ ...f, kit_level: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{[1, 2, 3, 4, 5].map((l) => <SelectItem key={l} value={String(l)}>{QUAL_LEVEL_NAMES[l]} - {l} уровень</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label>Предметы (JSON-объект)</Label>
              <JsonField value={newQual.items} onChange={(v) => setNewQual((f) => ({ ...f, items: v }))} rows={6} />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Корневые ключи = предметы (значение = количество). <code>ar_delete</code>: массив предметов для удаления.{" "}
                <code>talents</code>: массив ID талантов. <code>stats</code>: объект бонусов (ключ = профессия или &quot;all&quot;).
              </p>
            </div>
            <Button disabled={!newQual.base_job.trim()} onClick={() => addQual.mutate()}>💾 Создать</Button>
          </CardContent>
        </Card>

        <div className="grid gap-3 sm:grid-cols-2">
          {data?.qual_kits.map((k) => {
            const baseJob = k.job_id.endsWith("_k") ? k.job_id.slice(0, -2) : k.job_id
            const { chips, normCount, delCount } = qualKitChips(k.items)
            const isEditing = editingQualId === k.id
            return (
              <Card key={k.id}>
                <CardHeader className="flex flex-row items-center justify-between space-y-0">
                  <div>
                    <div className="font-semibold">{baseJob} <span className="text-xs font-normal text-info">квалификация</span></div>
                    <div className="font-mono text-xs text-muted-foreground">{QUAL_LEVEL_NAMES[k.kit_level] ?? k.kit_level} уровень · {k.job_id}</div>
                  </div>
                  <div className="flex gap-1.5">
                    <Button size="sm" variant="outline" onClick={() => (isEditing ? setEditingQualId(null) : startEditQual(k))}>{isEditing ? "Отмена" : "✏️"}</Button>
                    <Button size="sm" variant="destructive" onClick={() => setDeleteQualTarget(k)}>🗑</Button>
                  </div>
                </CardHeader>
                <CardContent>
                  {isEditing ? (
                    <div className="space-y-2">
                      <Select value={editDraft.kit_level} onValueChange={(v) => setEditDraft((d) => ({ ...d, kit_level: v }))}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>{[1, 2, 3, 4, 5].map((l) => <SelectItem key={l} value={String(l)}>{QUAL_LEVEL_NAMES[l]} - {l} уровень</SelectItem>)}</SelectContent>
                      </Select>
                      <JsonField value={editDraft.items} onChange={(v) => setEditDraft((d) => ({ ...d, items: v }))} rows={6} />
                      <Button size="sm" onClick={() => updateQual.mutate(k.id)}>💾 Сохранить</Button>
                    </div>
                  ) : chips.length ? (
                    <>
                      <div className="flex flex-wrap gap-1">
                        {chips.map((c) => (
                          <Badge key={c.key} variant="outline" className={c.isDelete ? "border-destructive/40 text-destructive" : "font-mono"}>{c.label}</Badge>
                        ))}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {normCount} добавляется{delCount ? ` · ${delCount} удаляется` : ""}
                      </div>
                    </>
                  ) : (
                    <div className="text-xs italic text-muted-foreground">Нет предметов</div>
                  )}
                </CardContent>
              </Card>
            )
          })}
          {!data?.qual_kits.length && <div className="col-span-full py-8 text-center text-muted-foreground">Квалификационных китов пока нет</div>}
        </div>
      </TabsContent>
      </Tabs>

      <ConfirmDialog
        open={deleteBaseTarget != null}
        onOpenChange={(o) => !o && setDeleteBaseTarget(null)}
        title="Удалить кит?"
        description={deleteBaseTarget ? `Кит «${deleteBaseTarget.job_id}» (уровень ${deleteBaseTarget.kit_level}) будет удалён безвозвратно.` : undefined}
        onConfirm={() => { if (deleteBaseTarget) deleteKit.mutate(deleteBaseTarget.id); setDeleteBaseTarget(null) }}
      />
      <ConfirmDialog
        open={deleteQualTarget != null}
        onOpenChange={(o) => !o && setDeleteQualTarget(null)}
        title="Удалить квал-кит?"
        description={deleteQualTarget ? `Квал-кит «${deleteQualTarget.job_id}» будет удалён безвозвратно.` : undefined}
        onConfirm={() => { if (deleteQualTarget) deleteQual.mutate(deleteQualTarget.id); setDeleteQualTarget(null) }}
      />
    </div>
  )
}
