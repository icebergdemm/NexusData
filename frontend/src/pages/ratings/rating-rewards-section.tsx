import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import type { RatingRewardCondition, RewardGroup } from "./rewards-types"
import { ROLES, ROLE_LABELS } from "./rewards-types"

export function RatingRewardsSection({ conditions, groups, canEdit }: { conditions: RatingRewardCondition[]; groups: RewardGroup[]; canEdit: boolean }) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const [role, setRole] = useState<string>(ROLES[0])
  const [interval, setInterval] = useState("7")
  const [places, setPlaces] = useState<{ place: string; reward_group_id: string }[]>([])
  const [removeIdx, setRemoveIdx] = useState<number | null>(null)

  const persist = useMutation({
    mutationFn: (next: RatingRewardCondition[]) => api.post("/api/v1/admin/rating-rewards", next),
    onSuccess: () => { toast.success("Сохранено"); queryClient.invalidateQueries({ queryKey: ["rating-rewards"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const openCreate = () => {
    setEditingIdx(null); setRole(ROLES[0]); setInterval("7"); setPlaces([{ place: "1", reward_group_id: "" }])
    setDialogOpen(true)
  }
  const openEdit = (i: number) => {
    const c = conditions[i]
    setEditingIdx(i); setRole(c.role); setInterval(String(c.check_interval_days || 7))
    setPlaces((c.places || []).map((p) => ({ place: String(p.place), reward_group_id: p.reward_group_id })))
    setDialogOpen(true)
  }

  const addPlace = () => setPlaces((p) => [...p, { place: "", reward_group_id: "" }])
  const updatePlace = (i: number, field: "place" | "reward_group_id", v: string) => {
    setPlaces((prev) => prev.map((p, idx) => (idx === i ? { ...p, [field]: v } : p)))
  }
  const removePlace = (i: number) => setPlaces((prev) => prev.filter((_, idx) => idx !== i))

  const save = () => {
    const parsedPlaces = places
      .map((p) => ({ place: parseInt(p.place, 10), reward_group_id: p.reward_group_id }))
      .filter((p) => p.place && p.reward_group_id)
    if (!parsedPlaces.length) { toast.error("Добавьте хотя бы одно место"); return }
    const cond: RatingRewardCondition = { role, check_interval_days: parseInt(interval, 10) || 7, places: parsedPlaces }
    const next = editingIdx !== null ? conditions.map((c, i) => (i === editingIdx ? cond : c)) : [...conditions, cond]
    persist.mutate(next)
    setDialogOpen(false)
  }

  const remove = (i: number) => {
    persist.mutate(conditions.filter((_, idx) => idx !== i))
  }

  const groupName = (id: string) => groups.find((g) => g.id === id)?.name ?? id

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">🏆 Награды по местам в рейтинге</CardTitle>
        {canEdit && <Button size="sm" onClick={openCreate}>+ Добавить условие</Button>}
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-xs text-muted-foreground">Автоматическая выдача групп наград топ-игрокам по каждой профессии. Проверяется каждые N дней.</p>
        {conditions.map((c, i) => (
          <div key={i} className="flex flex-wrap items-center gap-3 rounded-md border bg-card p-3 text-sm">
            <span>🎖 <strong>{ROLE_LABELS[c.role] ?? c.role}</strong></span>
            <span>каждые <strong>{c.check_interval_days || 7}</strong> дн.</span>
            <span className="flex-1 text-muted-foreground">
              {(c.places || []).map((p) => `${p.place} место → ${groupName(p.reward_group_id)}`).join("; ") || "—"}
            </span>
            {canEdit && (
              <div className="flex gap-1.5">
                <Button size="sm" variant="outline" onClick={() => openEdit(i)}>✏️</Button>
                <Button size="sm" variant="destructive" onClick={() => setRemoveIdx(i)}>🗑</Button>
              </div>
            )}
          </div>
        ))}
        {!conditions.length && <div className="py-6 text-center text-muted-foreground">Условий пока нет.</div>}
      </CardContent>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Условие награды по рейтингу</DialogTitle>
            <DialogDescription>Выдавать награду за место в рейтинге по выбранной профессии.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Профессия</Label>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{ROLES.map((r) => <SelectItem key={r} value={r}>{ROLE_LABELS[r]}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div><Label>Проверять каждые (дней)</Label><Input type="number" min={1} value={interval} onChange={(e) => setInterval(e.target.value)} /></div>
            <div>
              <Label className="mb-1.5 block">🏅 Награды по местам</Label>
              <div className="space-y-1.5">
                {places.map((p, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <Input type="number" min={1} className="flex-1" placeholder="место" value={p.place} onChange={(e) => updatePlace(i, "place", e.target.value)} />
                    <Select value={p.reward_group_id} onValueChange={(v) => updatePlace(i, "reward_group_id", v)}>
                      <SelectTrigger className="flex-[2]"><SelectValue placeholder="— группа —" /></SelectTrigger>
                      <SelectContent>{groups.map((g) => <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>)}</SelectContent>
                    </Select>
                    <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => removePlace(i)}>✕</Button>
                  </div>
                ))}
                <Button type="button" variant="outline" size="sm" className="w-full border-dashed" onClick={addPlace}>+ Добавить место</Button>
              </div>
            </div>
          </div>
          <DialogFooter><Button onClick={save}>💾 Сохранить</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={removeIdx != null}
        onOpenChange={(o) => !o && setRemoveIdx(null)}
        title="Удалить условие?"
        onConfirm={() => { if (removeIdx != null) remove(removeIdx); setRemoveIdx(null) }}
      />
    </Card>
  )
}
