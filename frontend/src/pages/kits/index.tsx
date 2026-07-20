import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
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

interface KitRow {
  id: number; job_id: string; kit_level: number; items: unknown
  variation_2_items: unknown; variation_3_items: unknown
  is_special: boolean; special_variation: number | null
}
interface KitsResponse { kits: KitRow[]; qual_kits: KitRow[]; all_job_ids: string[]; all_qual_base_jobs: string[]; kits_mapping: Record<string, string> }

interface VariationsFormState {
  variation2Items: string
  variation3Items: string
  isSpecial: boolean
  specialVariation: string
}

const EMPTY_VARIATIONS: VariationsFormState = { variation2Items: "", variation3Items: "", isSpecial: false, specialVariation: "" }

function variationsToRow(k: KitRow): VariationsFormState {
  return {
    variation2Items: k.variation_2_items != null ? JSON.stringify(k.variation_2_items, null, 2) : "",
    variation3Items: k.variation_3_items != null ? JSON.stringify(k.variation_3_items, null, 2) : "",
    isSpecial: k.is_special,
    specialVariation: k.special_variation != null ? String(k.special_variation) : "",
  }
}

/** Builds the variation_2_items/variation_3_items/is_special/special_variation
 * request fields (item 15) — always included (even as null) so clearing a
 * variation back to "inherit from variation 1" actually persists. */
function buildVariationsPayload(f: VariationsFormState, itemsFallback: string) {
  return {
    variation_2_items: f.variation2Items.trim() ? JSON.parse(f.variation2Items) : null,
    variation_3_items: f.variation3Items.trim() ? JSON.parse(f.variation3Items) : null,
    is_special: f.isSpecial,
    special_variation: f.isSpecial ? Number(f.specialVariation || itemsFallback) : null,
  }
}

function VariationsEditor({ items, onItemsChange, itemsRows, form, onFormChange }: {
  items: string; onItemsChange: (v: string) => void; itemsRows: number
  form: VariationsFormState; onFormChange: (f: VariationsFormState) => void
}) {
  return (
    <div className="space-y-2">
      <Tabs defaultValue="1">
        <TabsList className="h-8">
          <TabsTrigger value="1" className="text-xs">Вариация 1</TabsTrigger>
          <TabsTrigger value="2" className="text-xs">Вариация 2</TabsTrigger>
          <TabsTrigger value="3" className="text-xs">Вариация 3</TabsTrigger>
        </TabsList>
        <TabsContent value="1" className="mt-2">
          <JsonField value={items} onChange={onItemsChange} rows={itemsRows} />
        </TabsContent>
        <TabsContent value="2" className="mt-2">
          <JsonField value={form.variation2Items} onChange={(v) => onFormChange({ ...form, variation2Items: v })} rows={itemsRows} allowEmpty />
        </TabsContent>
        <TabsContent value="3" className="mt-2">
          <JsonField value={form.variation3Items} onChange={(v) => onFormChange({ ...form, variation3Items: v })} rows={itemsRows} allowEmpty />
        </TabsContent>
      </Tabs>
      <div className="flex items-center gap-2 rounded-md border p-2">
        <Checkbox checked={form.isSpecial} onCheckedChange={(c) => onFormChange({ ...form, isSpecial: c === true, specialVariation: c === true ? (form.specialVariation || "1") : "" })} />
        <Label className="text-xs font-normal">Особый кит (полная замена предметов для одной вариации)</Label>
        {form.isSpecial && (
          <Select value={form.specialVariation || "1"} onValueChange={(v) => onFormChange({ ...form, specialVariation: v })}>
            <SelectTrigger className="ml-auto h-7 w-32 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="1">Вариация 1</SelectItem>
              <SelectItem value="2">Вариация 2</SelectItem>
              <SelectItem value="3">Вариация 3</SelectItem>
            </SelectContent>
          </Select>
        )}
      </div>
    </div>
  )
}

function VariationBadges({ k }: { k: KitRow }) {
  if (!k.is_special && k.variation_2_items == null && k.variation_3_items == null) return null
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {k.variation_2_items != null && <Badge variant="secondary" className="text-[10px]">Вариация 2 задана</Badge>}
      {k.variation_3_items != null && <Badge variant="secondary" className="text-[10px]">Вариация 3 задана</Badge>}
      {k.is_special && <Badge className="text-[10px]">Особый · вариация {k.special_variation}</Badge>}
    </div>
  )
}

const QUAL_LEVEL_NAMES: Record<number, string> = { 1: "I", 2: "II", 3: "III", 4: "IV", 5: "V" }

export function KitsPage() {
  const [newKit, setNewKit] = useState({ job_id: "", kit_level: "0", items: "[]" })
  const [newKitVariations, setNewKitVariations] = useState<VariationsFormState>(EMPTY_VARIATIONS)
  const [newQual, setNewQual] = useState({
    base_job: "", kit_level: "1",
    items: '{\n  "item_id_1": 1,\n  "ar_delete": ["old_item"],\n  "talents": ["talent_id_1"],\n  "stats": {"all": 0}\n}',
  })
  const [newQualVariations, setNewQualVariations] = useState<VariationsFormState>(EMPTY_VARIATIONS)
  const [editingBaseId, setEditingBaseId] = useState<number | null>(null)
  const [editingQualId, setEditingQualId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState<{ job_id: string; kit_level: string; items: string }>({ job_id: "", kit_level: "", items: "" })
  const [editVariations, setEditVariations] = useState<VariationsFormState>(EMPTY_VARIATIONS)
  const [deleteBaseTarget, setDeleteBaseTarget] = useState<KitRow | null>(null)
  const [deleteQualTarget, setDeleteQualTarget] = useState<KitRow | null>(null)
  const queryClient = useQueryClient()

  const { data } = useQuery({ queryKey: ["kits"], queryFn: () => api.get<KitsResponse>("/api/v1/admin/kits") })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["kits"] })

  const addKit = useMutation({
    mutationFn: () => api.post("/api/v1/admin/kits", {
      job_id: newKit.job_id, kit_level: Number(newKit.kit_level), items: JSON.parse(newKit.items || "[]"),
      ...buildVariationsPayload(newKitVariations, "1"),
    }),
    onSuccess: () => { toast.success("Кит создан"); setNewKit({ job_id: "", kit_level: "0", items: "[]" }); setNewKitVariations(EMPTY_VARIATIONS); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })
  const updateKit = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/kits/${id}`, {
      job_id: editDraft.job_id, kit_level: Number(editDraft.kit_level), items: JSON.parse(editDraft.items || "[]"),
      ...buildVariationsPayload(editVariations, "1"),
    }),
    onSuccess: () => { toast.success("Кит обновлён"); setEditingBaseId(null); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })
  const deleteKit = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/kits/${id}`),
    onSuccess: () => { toast.success("Кит удалён"); invalidate() },
  })

  const addQual = useMutation({
    mutationFn: () => api.post("/api/v1/admin/qual-kits", {
      base_job: newQual.base_job, kit_level: Number(newQual.kit_level), items: JSON.parse(newQual.items || "{}"),
      ...buildVariationsPayload(newQualVariations, "1"),
    }),
    onSuccess: () => { toast.success("Квал-кит создан"); setNewQual({ ...newQual, base_job: "" }); setNewQualVariations(EMPTY_VARIATIONS); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })
  const updateQual = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/qual-kits/${id}`, {
      kit_level: Number(editDraft.kit_level), items: JSON.parse(editDraft.items || "{}"),
      ...buildVariationsPayload(editVariations, "1"),
    }),
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
    setEditVariations(variationsToRow(k))
  }
  const startEditQual = (k: KitRow) => {
    setEditingQualId(k.id)
    setEditDraft({ job_id: k.job_id, kit_level: String(k.kit_level), items: JSON.stringify(k.items, null, 2) })
    setEditVariations(variationsToRow(k))
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
              <div><Label className="mb-1.5 block">Профессия (job_id) *</Label><Input value={newKit.job_id} onChange={(e) => setNewKit((f) => ({ ...f, job_id: e.target.value }))} placeholder="Captain, Engineer..." /></div>
              <div>
                <Label className="mb-1.5 block">Уровень кита *</Label>
                <Select value={newKit.kit_level} onValueChange={(v) => setNewKit((f) => ({ ...f, kit_level: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Object.entries(data?.kits_mapping ?? {}).map(([id, name]) => <SelectItem key={id} value={id}>{id} - {name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label className="mb-1.5 block">Предметы по вариациям</Label>
              <VariationsEditor
                items={newKit.items} onItemsChange={(v) => setNewKit((f) => ({ ...f, items: v }))} itemsRows={3}
                form={newKitVariations} onFormChange={setNewKitVariations}
              />
              <p className="mt-1 text-[11px] text-muted-foreground">Вариация 1: например <code>[&quot;item_id_1&quot;, &quot;item_id_2&quot;]</code>. Вариации 2/3 не обязательны.</p>
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
                    <div className="animate-in fade-in slide-in-from-top-2 space-y-2 duration-200">
                      <div className="grid gap-2 sm:grid-cols-2">
                        <div><Label className="mb-1.5 block text-xs">Профессия</Label><Input value={editDraft.job_id} onChange={(e) => setEditDraft((d) => ({ ...d, job_id: e.target.value }))} /></div>
                        <div>
                          <Label className="mb-1.5 block text-xs">Уровень</Label>
                          <Select value={editDraft.kit_level} onValueChange={(v) => setEditDraft((d) => ({ ...d, kit_level: v }))}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>{Object.entries(data?.kits_mapping ?? {}).map(([id, name]) => <SelectItem key={id} value={id}>{id} - {name}</SelectItem>)}</SelectContent>
                          </Select>
                        </div>
                      </div>
                      <VariationsEditor
                        items={editDraft.items} onItemsChange={(v) => setEditDraft((d) => ({ ...d, items: v }))} itemsRows={4}
                        form={editVariations} onFormChange={setEditVariations}
                      />
                      <Button size="sm" onClick={() => updateKit.mutate(k.id)}>💾 Сохранить</Button>
                    </div>
                  ) : itemsList.length ? (
                    <>
                      <div className="flex flex-wrap gap-1">
                        {itemsList.slice(0, 8).map((item, i) => <Badge key={i} variant="outline" className="font-mono">{item}</Badge>)}
                        {itemsList.length > 8 && <Badge variant="outline">+{itemsList.length - 8} ещё</Badge>}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">{itemsList.length} предметов</div>
                      <VariationBadges k={k} />
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
              <div><Label className="mb-1.5 block">Профессия (без _k) *</Label><Input value={newQual.base_job} onChange={(e) => setNewQual((f) => ({ ...f, base_job: e.target.value }))} placeholder="assistant" /></div>
              <div>
                <Label className="mb-1.5 block">Уровень квалификации</Label>
                <Select value={newQual.kit_level} onValueChange={(v) => setNewQual((f) => ({ ...f, kit_level: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{[1, 2, 3, 4, 5].map((l) => <SelectItem key={l} value={String(l)}>{QUAL_LEVEL_NAMES[l]} - {l} уровень</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label className="mb-1.5 block">Предметы по вариациям (JSON-объект)</Label>
              <VariationsEditor
                items={newQual.items} onItemsChange={(v) => setNewQual((f) => ({ ...f, items: v }))} itemsRows={6}
                form={newQualVariations} onFormChange={setNewQualVariations}
              />
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
                    <div className="animate-in fade-in slide-in-from-top-2 space-y-2 duration-200">
                      <Select value={editDraft.kit_level} onValueChange={(v) => setEditDraft((d) => ({ ...d, kit_level: v }))}>
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>{[1, 2, 3, 4, 5].map((l) => <SelectItem key={l} value={String(l)}>{QUAL_LEVEL_NAMES[l]} - {l} уровень</SelectItem>)}</SelectContent>
                      </Select>
                      <VariationsEditor
                        items={editDraft.items} onItemsChange={(v) => setEditDraft((d) => ({ ...d, items: v }))} itemsRows={6}
                        form={editVariations} onFormChange={setEditVariations}
                      />
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
                      <VariationBadges k={k} />
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
