import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { AdaptiveIcon } from "@/components/layout/PlatformIcon"
import { Award, Medal, Pencil, Save, Trash2, Trophy, Zap } from "lucide-react"
import { api } from "@/lib/api"
import type { RatingRewardCondition, RatingRewardHolder, RewardGroup } from "./rewards-types"
import { ROLES, ROLE_LABELS, WEEKDAY_LABELS } from "./rewards-types"

function fmtDate(iso: string | null): string {
  if (!iso) return "-"
  const hasTz = iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso)
  const normalized = hasTz ? iso : iso + "Z"
  return new Date(normalized).toLocaleString("ru-RU")
}

export function RatingRewardsSection({ conditions, groups, canEdit }: { conditions: RatingRewardCondition[]; groups: RewardGroup[]; canEdit: boolean }) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const [role, setRole] = useState<string>(ROLES[0])
  const [weekday, setWeekday] = useState("0")
  const [timeMsk, setTimeMsk] = useState("12:00")
  const [places, setPlaces] = useState<{ place: string; reward_group_id: string }[]>([])
  const [removeIdx, setRemoveIdx] = useState<number | null>(null)

  const { data: holdersData } = useQuery({
    queryKey: ["rating-reward-holders"],
    queryFn: () => api.get<{ holders: RatingRewardHolder[] }>("/api/v1/admin/rating-rewards/holders"),
    refetchInterval: 30000,
  })
  const holders = holdersData?.holders ?? []

  const persist = useMutation({
    mutationFn: (next: RatingRewardCondition[]) => api.post("/api/v1/admin/rating-rewards", next),
    onSuccess: () => {
      toast.success("Сохранено")
      queryClient.invalidateQueries({ queryKey: ["rating-rewards"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const applyNow = useMutation({
    mutationFn: () => api.post("/api/v1/admin/rewards/apply-now"),
    onSuccess: () => {
      toast.success("Полная перевыдача запущена — награды будут сняты и выданы заново, держатели обновятся в течение нескольких секунд")
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["rating-reward-holders"] }), 3000)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const openCreate = () => {
    setEditingIdx(null); setRole(ROLES[0]); setWeekday("0"); setTimeMsk("12:00")
    setPlaces([{ place: "1", reward_group_id: "" }])
    setDialogOpen(true)
  }
  const openEdit = (i: number) => {
    const c = conditions[i]
    setEditingIdx(i); setRole(c.role)
    setWeekday(String(c.schedule?.weekday ?? 0)); setTimeMsk(c.schedule?.time_msk ?? "12:00")
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
    const cond: RatingRewardCondition = {
      id: editingIdx !== null ? conditions[editingIdx].id : undefined,
      role, schedule: { weekday: parseInt(weekday, 10), time_msk: timeMsk }, places: parsedPlaces,
    }
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
        <CardTitle className="flex items-center gap-1.5 text-sm"><AdaptiveIcon emoji="🏆" icon={Trophy} />Награды по местам в рейтинге</CardTitle>
        <div className="flex gap-1.5">
          {canEdit && (
            <Button size="sm" variant="outline" disabled={applyNow.isPending} onClick={() => applyNow.mutate()}>
              <AdaptiveIcon emoji="⚡" icon={Zap} /> Проверить сейчас
            </Button>
          )}
          {canEdit && <Button size="sm" onClick={openCreate}>+ Добавить условие</Button>}
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-xs text-muted-foreground">
          Автоматическая выдача групп наград топ-игрокам по каждой профессии — по расписанию (день недели + время МСК).
          При смене лидера награда автоматически снимается с прежнего держателя и выдаётся новому — задваивания и
          «зависших» наград у потерявших место больше нет. «Проверить сейчас» — принудительная итерация вне
          расписания: у ВСЕХ текущих держателей награда снимается и выдаётся заново по актуальному содержимому
          группы (полезно после правки самой группы — например, добавления предметов по ролям).
        </p>
        {conditions.map((c, i) => {
          const ruleHolders = c.id ? holders.filter((h) => h.rule_id === c.id) : []
          return (
            <div key={i} className="space-y-1.5 rounded-md border bg-card p-3 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <span className="flex items-center gap-1.5"><AdaptiveIcon emoji="🎖" icon={Award} /><strong>{ROLE_LABELS[c.role] ?? c.role}</strong></span>
                <span>{WEEKDAY_LABELS[c.schedule?.weekday ?? 0]}, <strong>{c.schedule?.time_msk ?? "-"}</strong> МСК</span>
                <span className="flex-1 text-muted-foreground">
                  {(c.places || []).map((p) => `${p.place} место → ${groupName(p.reward_group_id)}`).join("; ") || "-"}
                </span>
                {canEdit && (
                  <div className="flex gap-1.5">
                    <Button size="sm" variant="outline" onClick={() => openEdit(i)} aria-label="Изменить"><AdaptiveIcon emoji="✏️" icon={Pencil} /></Button>
                    <Button size="sm" variant="destructive" onClick={() => setRemoveIdx(i)} aria-label="Удалить"><AdaptiveIcon emoji="🗑" icon={Trash2} /></Button>
                  </div>
                )}
              </div>
              {!!ruleHolders.length && (
                <div className="flex flex-wrap gap-1.5 border-t pt-1.5">
                  {ruleHolders.map((h) => (
                    <Badge key={h.place} variant="secondary" className="font-normal" title={`Выдано ${fmtDate(h.granted_at)}`}>
                      {h.place} место: {h.player_name ?? h.steam_id}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          )
        })}
        {!conditions.length && <div className="py-6 text-center text-muted-foreground">Условий пока нет.</div>}
      </CardContent>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Условие награды по рейтингу</DialogTitle>
            <DialogDescription>Выдавать награду за место в рейтинге по выбранной профессии в назначенный день недели и время (МСК).</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label className="mb-1.5 block">Профессия</Label>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{ROLES.map((r) => <SelectItem key={r} value={r}>{ROLE_LABELS[r]}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label className="mb-1.5 block">День недели</Label>
                <Select value={weekday} onValueChange={setWeekday}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{WEEKDAY_LABELS.map((label, idx) => <SelectItem key={idx} value={String(idx)}>{label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label className="mb-1.5 block">Время (МСК)</Label>
                <Input type="time" value={timeMsk} onChange={(e) => setTimeMsk(e.target.value)} />
              </div>
            </div>
            <div>
              <Label className="mb-1.5 flex items-center gap-1.5"><AdaptiveIcon emoji="🏅" icon={Medal} />Награды по местам</Label>
              <div className="space-y-1.5">
                {places.map((p, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <Input type="number" min={1} className="flex-1" placeholder="место" value={p.place} onChange={(e) => updatePlace(i, "place", e.target.value)} />
                    <Select value={p.reward_group_id} onValueChange={(v) => updatePlace(i, "reward_group_id", v)}>
                      <SelectTrigger className="flex-[2]"><SelectValue placeholder="- группа -" /></SelectTrigger>
                      <SelectContent>{groups.map((g) => <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>)}</SelectContent>
                    </Select>
                    <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => removePlace(i)}>✕</Button>
                  </div>
                ))}
                <Button type="button" variant="outline" size="sm" className="w-full border-dashed" onClick={addPlace}>+ Добавить место</Button>
              </div>
            </div>
          </div>
          <DialogFooter><Button onClick={save}><AdaptiveIcon emoji="💾" icon={Save} /> Сохранить</Button></DialogFooter>
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
