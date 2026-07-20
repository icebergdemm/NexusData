import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { api } from "@/lib/api"
import type { AvailableKvalKit } from "./detail-types"

interface RewardsSectionProps {
  playerId: string
  canEdit: boolean
  kvalKits: Record<string, number>
  items: Record<string, number>
  stats: Record<string, number | Record<string, number>>
  talents: Record<string, string[]>
  availableKvalKits: AvailableKvalKit[]
  onChanged: () => void
}

export function RewardsSection({ playerId, canEdit, kvalKits, items, stats, talents, availableKvalKits, onChanged }: RewardsSectionProps) {
  const queryClient = useQueryClient()

  const invalidateAndRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ["player-detail", playerId] })
    onChanged()
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <KvalKitsCard playerId={playerId} canEdit={canEdit} kvalKits={kvalKits} available={availableKvalKits} onDone={invalidateAndRefresh} />
      <ItemsCard playerId={playerId} canEdit={canEdit} items={items} onDone={invalidateAndRefresh} />
      <StatsCard playerId={playerId} canEdit={canEdit} stats={stats} onDone={invalidateAndRefresh} />
      <TalentsCard playerId={playerId} canEdit={canEdit} talents={talents} onDone={invalidateAndRefresh} />
    </div>
  )
}

function KvalKitsCard({ playerId, canEdit, kvalKits, available, onDone }: {
  playerId: string; canEdit: boolean; kvalKits: Record<string, number>; available: AvailableKvalKit[]; onDone: () => void
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
              {job} - ур. {level}
              {canEdit && <button onClick={() => remove.mutate(job)} className="ml-0.5 hover:text-destructive">✕</button>}
            </Badge>
          ))}
          {!Object.keys(kvalKits).length && <span className="text-sm text-muted-foreground">Нет квалификаций</span>}
        </div>
        {canEdit && (
          <div className="flex gap-2">
            <Select value={jobId} onValueChange={setJobId}>
              <SelectTrigger className="flex-1"><SelectValue placeholder="Выбрать квал-кит..." /></SelectTrigger>
              <SelectContent>
                {grantable.map((k) => <SelectItem key={`${k.job}-${k.level}`} value={`${k.job}|${k.level}`}>{k.label}</SelectItem>)}
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

function ItemsCard({ playerId, canEdit, items, onDone }: { playerId: string; canEdit: boolean; items: Record<string, number>; onDone: () => void }) {
  const [itemId, setItemId] = useState("")
  const [qty, setQty] = useState("1")

  const set = useMutation({
    mutationFn: (v: { item_id: string; quantity: number }) => api.post(`/api/v1/admin/players/${playerId}/rewards/items`, v),
    onSuccess: () => { toast.success("Предмет выдан"); setItemId(""); setQty("1"); onDone() },
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: (item_id: string) => api.delete(`/api/v1/admin/players/${playerId}/rewards/items?item_id=${encodeURIComponent(item_id)}`),
    onSuccess: () => { toast.success("Предмет удалён"); onDone() },
  })

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">📦 Дополнительные предметы</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {Object.entries(items).map(([id, q]) => (
            <Badge key={id} variant="outline" className="gap-1.5 border-warning/40 text-warning">
              {id} ×{q}
              {canEdit && <button onClick={() => remove.mutate(id)} className="ml-0.5 hover:text-destructive">✕</button>}
            </Badge>
          ))}
          {!Object.keys(items).length && <span className="text-sm text-muted-foreground">Нет доп. предметов</span>}
        </div>
        {canEdit && (
          <div className="flex gap-2">
            <Input placeholder="item_id" value={itemId} onChange={(e) => setItemId(e.target.value)} className="flex-1" />
            <Input type="number" min={1} placeholder="кол-во" value={qty} onChange={(e) => setQty(e.target.value)} className="w-20" />
            <Button size="sm" disabled={!itemId.trim()} onClick={() => set.mutate({ item_id: itemId.trim(), quantity: Number(qty) || 1 })}>Выдать</Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function StatsCard({ playerId, canEdit, stats, onDone }: {
  playerId: string; canEdit: boolean; stats: Record<string, number | Record<string, number>>; onDone: () => void
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

  const rows: { job: string; key?: string; value: number }[] = []
  for (const [job, v] of Object.entries(stats)) {
    if (typeof v === "number") rows.push({ job, value: v })
    else for (const [k, vv] of Object.entries(v)) rows.push({ job, key: k, value: vv })
  }

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">📊 Бонусы к статам</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {rows.map((r) => (
            <Badge key={`${r.job}-${r.key ?? ""}`} variant="outline" className="gap-1.5 border-success/40 text-success">
              {r.job === "all" ? "все профессии" : r.job}{r.key ? ` · ${r.key}` : ""}: {r.value > 0 ? "+" : ""}{r.value}
              {canEdit && <button onClick={() => remove.mutate({ job_id: r.job, stat_key: r.key })} className="ml-0.5 hover:text-destructive">✕</button>}
            </Badge>
          ))}
          {!rows.length && <span className="text-sm text-muted-foreground">Нет бонусов</span>}
        </div>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <Input placeholder="профессия или &quot;all&quot;" value={jobId} onChange={(e) => setJobId(e.target.value)} className="flex-1" />
            <Input placeholder="ключ стата (если не all)" value={statKey} onChange={(e) => setStatKey(e.target.value)} className="flex-1" />
            <Input type="number" placeholder="значение" value={value} onChange={(e) => setValue(e.target.value)} className="w-24" />
            <Button
              size="sm"
              disabled={!jobId.trim()}
              onClick={() => set.mutate({ job_id: jobId.trim(), stat_key: statKey.trim() || undefined, value: Number(value) || 0 })}
            >
              Выдать
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function TalentsCard({ playerId, canEdit, talents, onDone }: { playerId: string; canEdit: boolean; talents: Record<string, string[]>; onDone: () => void }) {
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

  return (
    <Card>
      <CardHeader><CardTitle className="text-sm">✨ Таланты</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-2">
          {Object.entries(talents).map(([job, ids]) => (
            <div key={job} className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs font-semibold text-muted-foreground">{job === "all" ? "все профессии" : job}:</span>
              {ids.map((id) => (
                <Badge key={id} variant="outline" className="gap-1.5 border-primary/40 text-primary">
                  {id}
                  {canEdit && <button onClick={() => remove.mutate({ job_id: job, talent_id: id })} className="ml-0.5 hover:text-destructive">✕</button>}
                </Badge>
              ))}
            </div>
          ))}
          {!Object.keys(talents).length && <span className="text-sm text-muted-foreground">Нет талантов</span>}
        </div>
        {canEdit && (
          <div className="flex gap-2">
            <Input placeholder="профессия или &quot;all&quot;" value={jobId} onChange={(e) => setJobId(e.target.value)} className="flex-1" />
            <Input placeholder="talent_id" value={talentId} onChange={(e) => setTalentId(e.target.value)} className="flex-1" />
            <Button size="sm" disabled={!jobId.trim() || !talentId.trim()} onClick={() => add.mutate({ job_id: jobId.trim(), talent_id: talentId.trim() })}>Выдать</Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
