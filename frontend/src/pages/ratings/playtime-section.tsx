import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import type { PlaytimeCondition, PlaytimePeriod, PlaytimeStats, RewardGroup } from "./rewards-types"
import { PERIOD_LABELS } from "./rewards-types"

function fmtHours(seconds: number): string {
  return (seconds / 3600).toFixed(1) + "ч"
}

export function PlaytimeSection({ conditions, groups, canEdit }: { conditions: PlaytimeCondition[]; groups: RewardGroup[]; canEdit: boolean }) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const [period, setPeriod] = useState<PlaytimePeriod>("week")
  const [hours, setHours] = useState("10")
  const [groupId, setGroupId] = useState("")
  const [removeIdx, setRemoveIdx] = useState<number | null>(null)

  const { data: stats, refetch: refetchStats } = useQuery({
    queryKey: ["playtime-stats"],
    queryFn: () => api.get<PlaytimeStats>("/api/v1/admin/playtime/stats"),
  })

  const persist = useMutation({
    mutationFn: (next: PlaytimeCondition[]) => api.post("/api/v1/admin/playtime/conditions", next),
    onSuccess: () => { toast.success("Сохранено"); queryClient.invalidateQueries({ queryKey: ["playtime-conditions"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const generatePlaytime = useMutation({
    mutationFn: () => api.post("/api/v1/admin/playtime/generate"),
    onSuccess: () => { toast.success("Пересчёт запущен"); setTimeout(refetchStats, 3000) },
  })

  const applyNow = useMutation({
    mutationFn: () => api.post("/api/v1/admin/rewards/apply-now"),
    onSuccess: () => toast.success("Награды выданы"),
  })

  const openCreate = () => {
    setEditingIdx(null); setPeriod("week"); setHours("10"); setGroupId("")
    setDialogOpen(true)
  }
  const openEdit = (i: number) => {
    const c = conditions[i]
    setEditingIdx(i); setPeriod(c.period); setHours(String(c.min_seconds / 3600)); setGroupId(c.reward_group_id)
    setDialogOpen(true)
  }

  const save = () => {
    if (!groupId) { toast.error("Выберите группу награды"); return }
    const cond: PlaytimeCondition = { period, min_seconds: Math.round((parseFloat(hours) || 1) * 3600), reward_group_id: groupId }
    const next = editingIdx !== null ? conditions.map((c, i) => (i === editingIdx ? cond : c)) : [...conditions, cond]
    persist.mutate(next)
    setDialogOpen(false)
  }

  const remove = (i: number) => {
    persist.mutate(conditions.filter((_, idx) => idx !== i))
  }

  const groupName = (id: string) => groups.find((g) => g.id === id)?.name ?? id
  const leaderboard = Object.values(stats?.players ?? {}).filter((p) => p.week > 0).sort((a, b) => b.week - a.week).slice(0, 20)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <Card><CardContent className="pt-6 text-center"><div className="text-2xl font-bold">{Object.keys(stats?.players ?? {}).length}</div><div className="text-xs text-muted-foreground">Игроков в статистике</div></CardContent></Card>
        <Card><CardContent className="pt-6 text-center"><div className="text-sm font-bold">{stats?.generated_at ? new Date(stats.generated_at).toLocaleString("ru-RU") : "никогда"}</div><div className="text-xs text-muted-foreground">Последний расчёт</div></CardContent></Card>
        {canEdit && (
          <Card><CardContent className="flex flex-col justify-center gap-2 pt-6">
            <Button size="sm" variant="outline" onClick={() => generatePlaytime.mutate()}>🔄 Пересчитать сейчас</Button>
            <Button size="sm" onClick={() => applyNow.mutate()}>🎁 Выдать награды</Button>
          </CardContent></Card>
        )}
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-sm">⏱ Награды за наигранное время</CardTitle>
          {canEdit && <Button size="sm" onClick={openCreate}>+ Добавить условие</Button>}
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-xs text-muted-foreground">Выдавать группу наград игрокам, наигравшим заданное время за период (день/неделя/месяц).</p>
          {conditions.map((c, i) => (
            <div key={i} className="flex flex-wrap items-center gap-3 rounded-md border bg-card p-3 text-sm">
              <span>⏱ <strong>{(c.min_seconds / 3600).toFixed(1)} ч</strong> за <strong>{PERIOD_LABELS[c.period] ?? c.period}</strong></span>
              <span className="flex-1 text-muted-foreground">→ {groupName(c.reward_group_id)}</span>
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
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">📊 Топ игроков по времени (за неделю)</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>#</TableHead><TableHead>Игрок</TableHead><TableHead>За день</TableHead><TableHead>За неделю</TableHead><TableHead>За месяц</TableHead></TableRow></TableHeader>
            <TableBody>
              {leaderboard.map((p, i) => (
                <TableRow key={p.steamid}>
                  <TableCell>{i + 1}</TableCell>
                  <TableCell>{p.name || p.steamid}</TableCell>
                  <TableCell className="text-muted-foreground">{fmtHours(p.day)}</TableCell>
                  <TableCell>{fmtHours(p.week)}</TableCell>
                  <TableCell className="text-muted-foreground">{fmtHours(p.month)}</TableCell>
                </TableRow>
              ))}
              {!leaderboard.length && <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground">Нет данных</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Условие награды по времени</DialogTitle>
            <DialogDescription>Выдавать награду игрокам, набравшим указанное количество часов за период.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Период</Label>
              <Select value={period} onValueChange={(v) => setPeriod(v as PlaytimePeriod)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="day">За день</SelectItem>
                  <SelectItem value="week">За неделю</SelectItem>
                  <SelectItem value="month">За месяц</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div><Label>Минимум часов</Label><Input type="number" min={1} step={0.5} value={hours} onChange={(e) => setHours(e.target.value)} /></div>
            <div>
              <Label>Группа награды</Label>
              <Select value={groupId} onValueChange={setGroupId}>
                <SelectTrigger><SelectValue placeholder="— выберите группу —" /></SelectTrigger>
                <SelectContent>{groups.map((g) => <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>)}</SelectContent>
              </Select>
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
    </div>
  )
}
