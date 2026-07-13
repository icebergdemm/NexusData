import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import { KvRowEditor, type KvRow } from "./kv-rows"
import type { RewardGroup } from "./rewards-types"

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

export function RewardGroupsSection({ groups, canEdit }: { groups: RewardGroup[]; canEdit: boolean }) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [name, setName] = useState("")
  const [id, setId] = useState("")
  const [itemRows, setItemRows] = useState<KvRow[]>([])
  const [statRows, setStatRows] = useState<KvRow[]>([])
  const [talentRows, setTalentRows] = useState<KvRow[]>([])
  const [removeTarget, setRemoveTarget] = useState<RewardGroup | null>(null)

  const persist = useMutation({
    mutationFn: (next: RewardGroup[]) => api.post("/api/v1/admin/reward-groups", next),
    onSuccess: () => { toast.success("Сохранено"); queryClient.invalidateQueries({ queryKey: ["reward-groups"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const openCreate = () => {
    setEditingId(null); setName(""); setId(""); setItemRows([]); setStatRows([]); setTalentRows([])
    setDialogOpen(true)
  }
  const openEdit = (g: RewardGroup) => {
    setEditingId(g.id); setName(g.name); setId(g.id)
    setItemRows(toKvRows(g.items || {}, (v) => String(v)))
    setStatRows(toKvRows(g.stats || {}, (v) => (typeof v === "object" ? JSON.stringify(v) : String(v))))
    setTalentRows(toKvRows(g.talents || {}, (v) => (Array.isArray(v) ? v.join(",") : String(v))))
    setDialogOpen(true)
  }

  const save = () => {
    if (!name.trim() || !id.trim()) { toast.error("Заполните название и ID"); return }
    const grp: RewardGroup = {
      id: id.trim(), name: name.trim(),
      items: parseItemsKv(itemRows), stats: parseStatsKv(statRows), talents: parseTalentsKv(talentRows),
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

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">📦 Группы наград</CardTitle>
        {canEdit && <Button size="sm" onClick={openCreate}>+ Создать группу</Button>}
      </CardHeader>
      <CardContent>
        <p className="mb-3 text-xs text-muted-foreground">Группы предметов, статов и талантов, которые выдаются игрокам по условиям ниже.</p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {groups.map((g) => {
            const items = Object.entries(g.items || {})
            const stats = Object.entries(g.stats || {})
            const talents = Object.entries(g.talents || {})
            const empty = !items.length && !stats.length && !talents.length
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
                {empty && <div className="text-xs text-muted-foreground">Пустая группа</div>}
                {canEdit && (
                  <div className="mt-auto flex gap-1.5 pt-1">
                    <Button size="sm" variant="outline" onClick={() => openEdit(g)}>✏️ Изменить</Button>
                    <Button size="sm" variant="destructive" onClick={() => setRemoveTarget(g)}>🗑</Button>
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
            <DialogDescription>Наборы предметов, статов и талантов, которые можно выдать игроку как награду.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div><Label>Название группы *</Label><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Награда за 1 место" /></div>
            <div><Label>Буквенный ID *</Label><Input value={id} onChange={(e) => setId(e.target.value)} placeholder="top1_reward" disabled={!!editingId} /></div>

            <div>
              <Label className="mb-1.5 block">📦 Предметы (item_id → количество)</Label>
              <KvRowEditor rows={itemRows} onChange={setItemRows} keyPlaceholder="item_id" valuePlaceholder="кол-во" addLabel="+ Добавить предмет" />
            </div>
            <div>
              <Label className="mb-1.5 block">📊 Статы (ключ → значение)</Label>
              <KvRowEditor rows={statRows} onChange={setStatRows} keyPlaceholder="ключ (all/job)" valuePlaceholder="значение" addLabel="+ Добавить стат" />
            </div>
            <div>
              <Label className="mb-1.5 block">✨ Таланты (профессия → talent1,talent2)</Label>
              <KvRowEditor rows={talentRows} onChange={setTalentRows} keyPlaceholder="профессия" valuePlaceholder="talent1,talent2" addLabel="+ Добавить талант" />
            </div>
          </div>
          <DialogFooter><Button onClick={save}>💾 Сохранить</Button></DialogFooter>
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
