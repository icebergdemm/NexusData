import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Users2, TrendingUp, Check, X, Ban } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { api } from "@/lib/api"
import { Checkbox } from "@/components/ui/checkbox"
import { PlatformIcon } from "@/components/layout/PlatformIcon"

interface AnomalyPlayer {
  player_id: number
  name: string | null
  real_name: string | null
  steamid: string | null
}

interface Anomaly {
  id: number
  category: "twink" | "behavior"
  subtype: string
  player: AnomalyPlayer | null
  related_player: AnomalyPlayer | null
  score: number
  evidence: Record<string, unknown>
  detected_at: string | null
  last_seen_at: string | null
  status: "new" | "reviewed" | "dismissed"
  auto_banned: boolean
}

interface AnomaliesResponse {
  anomalies: Anomaly[]
  can_manage: boolean
  nd_autoban_enabled: boolean
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}

function PlayerChip({ p, onOpen }: { p: AnomalyPlayer | null; onOpen: (id: number) => void }) {
  if (!p) return <span className="text-muted-foreground">-</span>
  return (
    <button
      className="text-left text-primary underline-offset-2 hover:underline"
      title={p.real_name ? `Настоящий ник: ${p.real_name}` : undefined}
      onClick={() => onOpen(p.player_id)}
    >
      {p.name ?? `#${p.player_id}`}
    </button>
  )
}

function EvidenceDetails({ a }: { a: Anomaly }) {
  if (a.category === "twink") {
    // services_sauron.py's edge-building emits {type, label, weight} objects,
    // never bare strings — rendering `s` directly here previously crashed
    // the whole tab (React error #31, "objects are not valid as a child").
    const signals = (a.evidence.signals as { type: string; label: string; weight: number }[] | undefined) ?? []
    return (
      <div className="flex flex-wrap gap-1">
        {signals.map((s, i) => (
          <Badge key={i} variant="outline" className="text-[10px]" title={`Вес: ${s.weight}`}>{s.label}</Badge>
        ))}
        {a.evidence.ip ? <Badge variant="outline" className="font-mono text-[10px]">{String(a.evidence.ip)}</Badge> : null}
      </div>
    )
  }
  const incidents = (a.evidence.incidents as { label: string; icon: string }[] | undefined) ?? []
  return (
    <div className="flex flex-wrap gap-1.5 text-xs">
      {incidents.map((inc, i) => <span key={i} className="flex items-center gap-1"><PlatformIcon value={inc.icon} />{inc.label}</span>)}
    </div>
  )
}

export function AnomaliesTab({ setSummaryPlayerId }: { setSummaryPlayerId: (id: number) => void }) {
  const queryClient = useQueryClient()
  const [category, setCategory] = useState<string>("all")
  const [status, setStatus] = useState<string>("new")

  const { data, isLoading } = useQuery({
    queryKey: ["moderation-anomalies", category, status],
    queryFn: () => {
      const params = new URLSearchParams({ status })
      if (category !== "all") params.set("category", category)
      return api.get<AnomaliesResponse>(`/api/v1/admin/moderation/anomalies?${params}`)
    },
  })

  const reviewMutation = useMutation({
    mutationFn: ({ id, newStatus }: { id: number; newStatus: string }) =>
      api.post(`/api/v1/admin/moderation/anomalies/${id}/review`, { status: newStatus }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["moderation-anomalies"] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const autobanMutation = useMutation({
    mutationFn: (enabled: boolean) => api.put("/api/v1/admin/moderation/anomalies/autoban", { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["moderation-anomalies"] }),
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={category} onValueChange={setCategory}>
          <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все категории</SelectItem>
            <SelectItem value="twink">Твинк-аккаунты</SelectItem>
            <SelectItem value="behavior">Девиантное поведение</SelectItem>
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="new">Новые</SelectItem>
            <SelectItem value="reviewed">Проверенные</SelectItem>
            <SelectItem value="dismissed">Отклонённые</SelectItem>
            <SelectItem value="all">Все</SelectItem>
          </SelectContent>
        </Select>
        {data?.can_manage && (
          <label className="ml-auto flex items-center gap-2 text-sm">
            Автобан ND
            <Checkbox checked={data.nd_autoban_enabled} disabled={autobanMutation.isPending} onCheckedChange={(enabled) => autobanMutation.mutate(enabled === true)} />
          </label>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        Обновляется фоновой задачей раз в 30 минут (поиск твинков по IP/подсети/имени + статистика девиантного поведения). Полное совпадение IP с активно забаненным игроком, который сейчас онлайн, банится автоматически.
      </p>

      {isLoading ? (
        <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Card key={i} className="h-20 animate-pulse" />)}</div>
      ) : !data?.anomalies.length ? (
        <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">Аномалий не найдено.</CardContent></Card>
      ) : (
        <div className="flex flex-col gap-2">
          {data.anomalies.map((a) => (
            <Card key={a.id} className="overflow-hidden py-0">
              <CardContent className="flex flex-wrap items-center justify-between gap-3 p-3">
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {a.category === "twink" ? (
                      <Badge className="gap-1 bg-warning/15 text-warning"><Users2 className="size-3" /> Твинк</Badge>
                    ) : (
                      <Badge className="gap-1 bg-info/15 text-info"><TrendingUp className="size-3" /> Поведение</Badge>
                    )}
                    <Badge variant="secondary" className="text-[10px]">Счёт: {a.score}</Badge>
                    {a.auto_banned && <Badge variant="destructive" className="gap-1 text-[10px]"><Ban className="size-3" /> Авто-бан</Badge>}
                    {a.status !== "new" && <Badge variant="outline" className="text-[10px]">{a.status === "reviewed" ? "Проверено" : "Отклонено"}</Badge>}
                  </div>
                  <div className="text-sm">
                    <PlayerChip p={a.player} onOpen={setSummaryPlayerId} />
                    {a.related_player && <> ↔ <PlayerChip p={a.related_player} onOpen={setSummaryPlayerId} /></>}
                  </div>
                  <EvidenceDetails a={a} />
                  <div className="text-[11px] text-muted-foreground">Обнаружено: {fmtDate(a.detected_at)} · Последний раз: {fmtDate(a.last_seen_at)}</div>
                </div>
                {data.can_manage && a.status === "new" && (
                  <div className="flex shrink-0 gap-1.5">
                    <Button size="sm" variant="outline" onClick={() => reviewMutation.mutate({ id: a.id, newStatus: "reviewed" })}>
                      <Check className="size-3.5" /> Проверено
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => reviewMutation.mutate({ id: a.id, newStatus: "dismissed" })}>
                      <X className="size-3.5" /> Отклонить
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
