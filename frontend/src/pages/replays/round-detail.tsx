import { useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { ArrowLeft, Crown, MapPin, Ship, Target, Video, Link2, Link2Off } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface RoundPlayer { steamid: string; player_name: string; real_name?: string; role?: string }
interface ReplayRow { id: number; server_name: string | null; submarine_name: string | null; file_size: number | null; recorded_at: string | null }
interface RoundDetail {
  id: number
  server_name: string | null
  submarine_name: string | null
  mission: string | null
  location: string | null
  started_at: string | null
  ended_at: string | null
  mission_completed: boolean | null
  reached_station: boolean | null
  players: RoundPlayer[]
  player_count: number
  replay: ReplayRow | null
  manually_linked: boolean
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}

function isCaptain(role: string | undefined): boolean {
  return !!role && role.toLowerCase().includes("captain")
}

export function RoundDetailPage() {
  const { roundId } = useParams<{ roundId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [linkPickerOpen, setLinkPickerOpen] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ["round-detail", roundId],
    queryFn: () => api.get<RoundDetail>(`/api/v1/admin/replays/rounds/${roundId}`),
  })

  const { data: replaysData } = useQuery({
    queryKey: ["replays-for-link"],
    queryFn: () => api.get<{ replays: ReplayRow[] }>("/api/v1/admin/replays?page=1"),
    enabled: linkPickerOpen,
  })

  const linkMutation = useMutation({
    mutationFn: (replayId: number | null) => api.post(`/api/v1/admin/replays/rounds/${roundId}/link-replay`, { replay_id: replayId }),
    onSuccess: () => {
      toast.success("Демозапись привязана")
      setLinkPickerOpen(false)
      queryClient.invalidateQueries({ queryKey: ["round-detail", roundId] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (isLoading || !data) return <div className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-64" /></div>

  const sortedPlayers = [...data.players].sort((a, b) => (isCaptain(b.role) ? 1 : 0) - (isCaptain(a.role) ? 1 : 0))

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="icon" onClick={() => navigate("/replays")}><ArrowLeft className="size-4" /></Button>
        <PageHeader title={data.submarine_name ?? "Раунд"} description={`${fmtDate(data.started_at)} — ${fmtDate(data.ended_at)}`} />
      </div>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 p-4 text-sm sm:grid-cols-3">
          <div className="flex items-center gap-1.5 text-muted-foreground"><MapPin className="size-3.5 shrink-0" /> {data.location ?? "-"}</div>
          <div className="flex items-center gap-1.5 text-muted-foreground"><Ship className="size-3.5 shrink-0" /> {data.submarine_name ?? "-"}</div>
          <div className="flex items-center gap-1.5 text-muted-foreground"><Target className="size-3.5 shrink-0" /> {data.mission ?? "-"}</div>
          <div className="col-span-full flex flex-wrap gap-2">
            <Badge variant="secondary">{data.server_name ?? "-"}</Badge>
            {data.mission_completed != null && (
              <Badge variant={data.mission_completed ? "default" : "destructive"} className={data.mission_completed ? "bg-success text-white" : ""}>
                {data.mission_completed ? "Миссия выполнена" : "Миссия провалена"}
              </Badge>
            )}
            {data.reached_station != null && (
              <Badge variant={data.reached_station ? "default" : "secondary"} className={data.reached_station ? "bg-info text-white" : ""}>
                {data.reached_station ? "Добрались до станции" : "Не добрались до станции"}
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-2 p-4">
          <div className="text-xs font-semibold text-muted-foreground">Игроки ({data.player_count})</div>
          <div className="flex flex-col gap-1.5">
            {sortedPlayers.map((p) => (
              <div key={p.steamid} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                <span className="flex items-center gap-1.5">
                  {isCaptain(p.role) && <Crown className="size-3.5 text-primary" />}
                  {p.player_name}
                  {p.real_name && (
                    <Badge variant="outline" className="border-info/40 text-info" title={`Настоящий ник: ${p.real_name}`}>
                      принудительное имя
                    </Badge>
                  )}
                </span>
                {p.role && <Badge variant="outline" className="text-xs">{p.role}</Badge>}
              </div>
            ))}
            {!sortedPlayers.length && <p className="text-sm text-muted-foreground">Список игроков недоступен для этого раунда.</p>}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-2 p-4">
          <div className="text-sm">
            {data.replay ? (
              <span className="flex items-center gap-1.5">
                <Video className="size-4" /> Демо: {data.replay.submarine_name ?? `#${data.replay.id}`}
                {data.manually_linked && <Badge variant="outline" className="text-[10px]">привязано вручную</Badge>}
              </span>
            ) : (
              <span className="text-muted-foreground">Демозапись не найдена автоматически.</span>
            )}
          </div>
          <div className="flex gap-2">
            {data.replay && (
              <Button size="sm" onClick={() => navigate(`/replays/${data.replay!.id}`)}><Video className="size-3.5" /> Смотреть демо</Button>
            )}
            {data.replay && data.manually_linked && (
              <Button size="sm" variant="outline" onClick={() => linkMutation.mutate(null)}><Link2Off className="size-3.5" /> Отвязать</Button>
            )}
            <Button size="sm" variant="outline" onClick={() => setLinkPickerOpen((o) => !o)}><Link2 className="size-3.5" /> Привязать вручную</Button>
          </div>
        </CardContent>
        {linkPickerOpen && (
          <CardContent className="flex items-center gap-2 border-t p-4">
            <Select onValueChange={(v) => linkMutation.mutate(Number(v))}>
              <SelectTrigger className="max-w-sm"><SelectValue placeholder="Выберите демозапись..." /></SelectTrigger>
              <SelectContent>
                {replaysData?.replays.map((r) => (
                  <SelectItem key={r.id} value={String(r.id)}>
                    #{r.id} {r.submarine_name ?? "-"} · {r.server_name ?? "-"} · {fmtDate(r.recorded_at)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </CardContent>
        )}
      </Card>
    </div>
  )
}
