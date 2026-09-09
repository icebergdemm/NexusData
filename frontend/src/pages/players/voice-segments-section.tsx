import { useState } from "react"
import { Volume2 } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { VoiceClipPlayer } from "./voice-clip-player"

interface VoiceClip {
  id: number
  speaker_id: number
  start_ms: number
  end_ms: number
  duration_ms: number
  packet_count: number
  band: "early" | "middle" | "latest"
  audio_url: string
}

interface VoiceReplayGroup {
  replay_id: number
  replay_filename: string
  server_name: string | null
  recorded_at: string | null
  uploaded_at: string | null
  clips: VoiceClip[]
}

interface VoiceClipsResponse {
  items: VoiceReplayGroup[]
  total_groups: number
  total_clips: number
  page: number
  page_size: number
  total_pages: number
}

function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}

function bandLabel(band: VoiceClip["band"]): string {
  return ({ early: "ранний", middle: "середина", latest: "последний" })[band]
}

export function VoiceSegmentsSection({ playerId }: { playerId: string }) {
  const [page, setPage] = useState(1)
  const { data, isLoading, isError } = useQuery({
    queryKey: ["player-voice-clips", playerId, page],
    queryFn: () => api.get<VoiceClipsResponse>(`/api/v1/admin/players/${playerId}/voice-clips?page=${page}&page_size=8`),
    enabled: !!playerId,
  })

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle className="flex items-center gap-1.5 text-sm"><Volume2 className="size-4" /> Голосовые фрагменты</CardTitle>
        {data && <Badge variant="secondary">{data.total_clips}</Badge>}
      </CardHeader>
      <CardContent className="space-y-3 p-0">
        {isLoading && <div className="px-4 py-8 text-center text-sm text-muted-foreground">Голосовой архив ещё грузится…</div>}
        {isError && <div className="px-4 py-8 text-center text-sm text-destructive">Не удалось загрузить голосовой архив.</div>}
        {!isLoading && !isError && data && data.items.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">Сохранённых фрагментов пока нет.</div>
        )}
        {data && data.items.length > 0 && (
          <>
            <div className="divide-y">
              {data.items.map((group, index) => (
                <details key={group.replay_id} open={index === 0} className="group">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 hover:bg-muted/30 [&::-webkit-details-marker]:hidden">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">Replay #{group.replay_id} · {group.server_name || "сервер неизвестен"}</div>
                      <div className="truncate text-xs text-muted-foreground" title={group.replay_filename}>
                        {formatBrowserDate(group.recorded_at || group.uploaded_at)} · {group.replay_filename}
                      </div>
                    </div>
                    <Badge variant="outline">{group.clips.length} фрагм.</Badge>
                  </summary>
                  <div className="border-t bg-muted/10 px-4 py-2">
                    {group.clips.map((clip) => (
                      <div key={clip.id} className="flex items-center justify-between gap-3 border-b py-2 last:border-0">
                        <div className="min-w-0 text-xs text-muted-foreground">
                          <span className="font-mono text-foreground">{formatDuration(clip.start_ms)}</span>
                          <span className="mx-1">·</span>
                          <span>{formatDuration(clip.duration_ms)}</span>
                          <span className="mx-1">·</span>
                          <span>{bandLabel(clip.band)}</span>
                        </div>
                        <VoiceClipPlayer audioUrl={clip.audio_url} />
                      </div>
                    ))}
                  </div>
                </details>
              ))}
            </div>
            {data.total_pages > 1 && (
              <div className="flex items-center justify-between border-t px-4 py-3 text-xs text-muted-foreground">
                <span>Replay-группы: {data.page} из {data.total_pages}</span>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Назад</Button>
                  <Button size="sm" variant="outline" disabled={page >= data.total_pages} onClick={() => setPage((value) => value + 1)}>Дальше</Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
