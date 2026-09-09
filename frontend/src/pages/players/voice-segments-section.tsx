import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { Play, Volume2 } from "lucide-react"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

interface VoiceSegment {
  id: number
  replay_id: number
  replay_filename: string
  server_name: string | null
  recorded_at: string | null
  uploaded_at: string | null
  start_ms: number
  end_ms: number
  duration_ms: number
  packet_count: number
  replay_url: string
}

interface VoiceSegmentsResponse {
  items: VoiceSegment[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`
}

export function VoiceSegmentsSection({ playerId }: { playerId: string }) {
  const [page, setPage] = useState(1)
  const { data, isLoading, isError } = useQuery({
    queryKey: ["player-voice-segments", playerId, page],
    queryFn: () => api.get<VoiceSegmentsResponse>(`/api/v1/admin/players/${playerId}/voice-segments?page=${page}&page_size=25`),
    enabled: !!playerId,
  })

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle className="flex items-center gap-1.5 text-sm"><Volume2 className="size-4" /> Голосовые фрагменты</CardTitle>
        {data && <Badge variant="secondary">{data.total}</Badge>}
      </CardHeader>
      <CardContent className="space-y-3 p-0">
        {isLoading && <div className="px-4 py-8 text-center text-sm text-muted-foreground">Индекс ещё грузится…</div>}
        {isError && <div className="px-4 py-8 text-center text-sm text-destructive">Не удалось загрузить голосовой архив.</div>}
        {!isLoading && !isError && data && data.items.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">Фрагментов пока нет. Индексация работает в фоне, как и должна.</div>
        )}
        {data && data.items.length > 0 && (
          <>
            <Table>
              <TableHeader><TableRow><TableHead>Дата</TableHead><TableHead>Сервер</TableHead><TableHead>Replay</TableHead><TableHead>Длительность</TableHead><TableHead className="text-right">Прослушать</TableHead></TableRow></TableHeader>
              <TableBody>
                {data.items.map((segment) => (
                  <TableRow key={segment.id}>
                    <TableCell className="text-sm">{formatBrowserDate(segment.recorded_at || segment.uploaded_at)}</TableCell>
                    <TableCell>{segment.server_name || "-"}</TableCell>
                    <TableCell className="font-mono text-xs" title={segment.replay_filename}>{segment.replay_filename || `#${segment.replay_id}`}</TableCell>
                    <TableCell>{formatDuration(segment.duration_ms)}</TableCell>
                    <TableCell className="text-right"><Button asChild size="sm" variant="outline"><Link to={segment.replay_url}><Play className="size-3.5" /> Слушать</Link></Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {data.total_pages > 1 && (
              <div className="flex items-center justify-between border-t px-4 py-3 text-xs text-muted-foreground">
                <span>Страница {data.page} из {data.total_pages}</span>
                <div className="flex gap-2"><Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Назад</Button><Button size="sm" variant="outline" disabled={page >= data.total_pages} onClick={() => setPage((value) => value + 1)}>Дальше</Button></div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
