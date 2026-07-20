import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import {
  History, PlayCircle, MapPin, Ship, Target, Users2, CheckCircle2, XCircle, Video, Crown,
} from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface ReplayRow {
  id: number; server_name: string | null; submarine_name: string | null; level_name: string | null
  file_size: number | null; has_voice: boolean; recorded_at: string | null; uploaded_at: string | null
}
interface ReplaysResponse {
  replays: ReplayRow[]; total: number; page: number; total_pages: number
  retention_days: number; can_delete: boolean; is_superuser: boolean
}
interface RoundPlayer { steamid: string; player_name: string; role?: string }
interface RoundRow {
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
}
interface RoundsResponse {
  rounds: RoundRow[]; total: number; page: number; total_pages: number; selected_server: string | null
}

function fmtSize(n: number | null): string {
  if (!n) return "0 B"
  const units = ["B", "KB", "MB", "GB"]
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}

function isCaptain(role: string | undefined): boolean {
  return !!role && role.toLowerCase().includes("captain")
}

function fmtDuration(startIso: string | null, endIso: string | null): string {
  if (!startIso || !endIso) return "-"
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (ms <= 0) return "-"
  const totalMin = Math.round(ms / 60000)
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  return h > 0 ? `${h}ч ${m}м` : `${m}м`
}

function RoundsTab() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)

  const { data, isLoading } = useQuery({
    queryKey: ["rounds", page],
    queryFn: () => api.get<RoundsResponse>(`/api/v1/admin/replays/rounds?page=${page}`),
  })

  if (isLoading) return <div className="space-y-3">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-32" />)}</div>

  return (
    <div className="space-y-3">
      {data?.rounds.map((r) => (
        <Card key={r.id} className="cursor-pointer overflow-hidden py-0 transition-colors hover:border-primary/40" onClick={() => navigate(`/replays/rounds/${r.id}`)}>
          <CardContent className="p-4">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Badge variant="secondary">{r.server_name ?? "-"}</Badge>
              <span className="font-semibold">{r.submarine_name ?? "Раунд"}</span>
              <span className="text-xs text-muted-foreground">{fmtDate(r.started_at)} - {fmtDate(r.ended_at)} · {fmtDuration(r.started_at, r.ended_at)}</span>
              <div className="flex-1" />
              {r.mission_completed != null && (
                <Badge variant={r.mission_completed ? "default" : "destructive"} className={r.mission_completed ? "bg-success text-white" : ""}>
                  {r.mission_completed ? <CheckCircle2 className="size-3" /> : <XCircle className="size-3" />}
                  {r.mission_completed ? "Миссия выполнена" : "Миссия провалена"}
                </Badge>
              )}
              {r.reached_station != null && (
                <Badge variant={r.reached_station ? "default" : "secondary"} className={r.reached_station ? "bg-info text-white" : ""}>
                  {r.reached_station ? "Добрались до станции" : "Не добрались до станции"}
                </Badge>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              <div className="flex items-center gap-1.5 text-muted-foreground"><MapPin className="size-3.5 shrink-0" /> {r.location ?? "-"}</div>
              <div className="flex items-center gap-1.5 text-muted-foreground"><Ship className="size-3.5 shrink-0" /> {r.submarine_name ?? "-"}</div>
              <div className="flex items-center gap-1.5 text-muted-foreground"><Target className="size-3.5 shrink-0" /> {r.mission ?? "-"}</div>
              <div className="flex items-center gap-1.5 text-muted-foreground"><Users2 className="size-3.5 shrink-0" /> {r.player_count} игроков</div>
            </div>
            <div className="mt-3 flex items-center justify-between border-t pt-3">
              <div className="flex flex-wrap gap-1">
                {r.players.slice(0, 8).map((p) => (
                  <Badge key={p.steamid} variant={isCaptain(p.role) ? "default" : "outline"} className="gap-1 text-xs">
                    {isCaptain(p.role) && <Crown className="size-3" />} {p.player_name}
                  </Badge>
                ))}
                {r.players.length > 8 && <Badge variant="outline" className="text-xs">+{r.players.length - 8}</Badge>}
              </div>
              {r.replay ? (
                <Button size="sm" onClick={(e) => { e.stopPropagation(); navigate(`/replays/${r.replay!.id}`) }}><Video className="size-3.5" /> Смотреть демо</Button>
              ) : (
                <span className="shrink-0 text-xs text-muted-foreground">Демо не найдено</span>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
      {!data?.rounds.length && (
        <Card><CardContent className="py-12 text-center text-muted-foreground">Прошедших раундов пока нет</CardContent></Card>
      )}
      {data && data.total_pages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
          <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages}</span>
          <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
        </div>
      )}
    </div>
  )
}

function DemosTab() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ["replays", page],
    queryFn: () => api.get<ReplaysResponse>(`/api/v1/admin/replays?page=${page}`),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/replays/${id}`),
    onSuccess: () => { toast.success("Демо-запись удалена"); queryClient.invalidateQueries({ queryKey: ["replays"] }) },
  })

  return (
    <Card className="overflow-hidden py-0">
      <CardContent className="p-0">
        {isLoading ? <Skeleton className="m-4 h-64" /> : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>Корабль</TableHead><TableHead>Сервер</TableHead><TableHead>Размер</TableHead><TableHead>Голос</TableHead><TableHead>Загружен</TableHead>{data?.can_delete && <TableHead />}</TableRow>
            </TableHeader>
            <TableBody>
              {data?.replays.map((r) => (
                <TableRow key={r.id} className="cursor-pointer" onClick={() => navigate(`/replays/${r.id}`)}>
                  <TableCell className="font-medium">{r.submarine_name ?? "Раунд"} #{r.id}{r.level_name && <span className="text-xs text-muted-foreground"> · {r.level_name}</span>}</TableCell>
                  <TableCell><Badge variant="secondary">{r.server_name ?? "-"}</Badge></TableCell>
                  <TableCell>{fmtSize(r.file_size)}</TableCell>
                  <TableCell>{r.has_voice ? "🔊" : "-"}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{fmtDate(r.uploaded_at)}</TableCell>
                  {data?.can_delete && (
                    <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                      <Button size="sm" variant="destructive" onClick={() => deleteMutation.mutate(r.id)}>Удалить</Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
              {!data?.replays.length && <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">Демо-записей нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        )}
        {data && data.total_pages > 1 && (
          <div className="flex items-center justify-center gap-2 border-t bg-muted/30 py-3">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
            <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages}</span>
            <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function ReplaysListPage() {
  return (
    <div>
      <PageHeader title="Прошедшие раунды" description="История раундов и записи демо для просмотра" icon={PlayCircle} />
      <Tabs defaultValue="rounds" className="space-y-4">
        <TabsList>
          <TabsTrigger value="rounds" className="gap-1.5"><History className="size-3.5" /> Раунды</TabsTrigger>
          <TabsTrigger value="demos" className="gap-1.5"><Video className="size-3.5" /> Демо записи</TabsTrigger>
        </TabsList>
        <TabsContent value="rounds"><RoundsTab /></TabsContent>
        <TabsContent value="demos"><DemosTab /></TabsContent>
      </Tabs>
    </div>
  )
}
