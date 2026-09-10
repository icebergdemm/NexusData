import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Mic2, Play, RefreshCw, SkipForward, UserCheck } from "lucide-react"
import { toast } from "sonner"
import { PageHeader } from "@/components/layout/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

interface Job {
  id: number
  status: string
  replay_ids: number[]
  total_replays: number
  processed_replays: number
  mapped_speakers: number
  unresolved_speakers: number
  materialized_clips: number
  error_message: string | null
}

interface Speaker {
  id: number
  replay_id: number
  speaker_id: number
  names: string[]
  candidate_profiles: Array<{ player_steamid?: string; player_id?: number; player_last_time_name?: string }>
  packet_count: number
  radio_packet_count: number
  resolution_status: string
}

export function VoiceProfilesPage() {
  const queryClient = useQueryClient()
  const [replayIds, setReplayIds] = useState("")
  const [runId, setRunId] = useState<number | null>(null)

  const speakers = useQuery({
    queryKey: ["voice-profile-speakers"],
    queryFn: () => api.get<{ speakers: Speaker[] }>("/api/v1/admin/voice-profiles/speakers?status=unresolved"),
  })
  const run = useQuery({
    queryKey: ["voice-profile-run", runId],
    queryFn: () => api.get<Job>(`/api/v1/admin/voice-profiles/runs/${runId}`),
    enabled: runId !== null,
    refetchInterval: (query) => query.state.data?.status === "queued" || query.state.data?.status === "running" ? 3000 : false,
  })

  const start = useMutation({
    mutationFn: () => {
      const ids = replayIds.split(/[\s,;]+/).filter(Boolean).map(Number)
      return api.post<Job>("/api/v1/admin/voice-profiles/runs", { replay_ids: ids, mode: "backfill" })
    },
    onSuccess: (job) => { setRunId(job.id); toast.success(`Backfill #${job.id} поставлен в очередь`); queryClient.invalidateQueries({ queryKey: ["voice-profile-speakers"] }) },
    onError: (error: Error) => toast.error(error.message),
  })

  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "resolve" | "skip" }) => {
      if (action === "skip") return api.post(`/api/v1/admin/voice-profiles/speakers/${id}/skip`)
      const steamId = window.prompt("SteamID игрока из NexusData")?.trim()
      if (!steamId) throw new Error("SteamID не указан")
      return api.post(`/api/v1/admin/voice-profiles/speakers/${id}/resolve`, { steam_id: steamId })
    },
    onSuccess: () => { toast.success("Решение сохранено"); queryClient.invalidateQueries({ queryKey: ["voice-profile-speakers"] }) },
    onError: (error: Error) => toast.error(error.message),
  })

  return (
    <div className="space-y-6">
      <PageHeader
        title="Голосовые профили"
        description="Backfill старых реплеев, ручное сопоставление speaker_id и прослушивание выбранных клипов. Новые реплеи здесь пока не сканируются."
        icon={Mic2}
        actions={<Button variant="outline" onClick={() => { void speakers.refetch(); if (runId !== null) void run.refetch() }}><RefreshCw className="size-4" /> Обновить</Button>}
      />

      <Card>
        <CardHeader><CardTitle>Ручной backfill</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Input className="min-w-80 flex-1" value={replayIds} onChange={(event) => setReplayIds(event.target.value)} placeholder="ID реплеев: 101, 102, 103" />
          <Button onClick={() => start.mutate()} disabled={start.isPending || !replayIds.trim()}><Play className="size-4" /> Запустить</Button>
        </CardContent>
      </Card>

      {run.data && (
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2">Запуск #{run.data.id}<Badge variant={run.data.status === "failed" ? "destructive" : "secondary"}>{run.data.status}</Badge></CardTitle></CardHeader>
          <CardContent className="grid gap-2 text-sm sm:grid-cols-4">
            <span>Реплеи: {run.data.processed_replays}/{run.data.total_replays}</span>
            <span>Сопоставлено: {run.data.mapped_speakers}</span>
            <span>Unresolved: {run.data.unresolved_speakers}</span>
            <span>Клипы: {run.data.materialized_clips}</span>
            {run.data.error_message && <span className="text-destructive sm:col-span-4">{run.data.error_message}</span>}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>Неразрешённые speaker’ы ({speakers.data?.speakers.length ?? 0})</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {speakers.isLoading && <p className="text-sm text-muted-foreground">Загрузка…</p>}
          {!speakers.isLoading && !speakers.data?.speakers.length && <p className="text-sm text-muted-foreground">Пусто. Редкая удача.</p>}
          {speakers.data?.speakers.map((speaker) => (
            <div key={speaker.id} className="flex flex-col gap-3 rounded-md border p-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0 text-sm">
                <div className="font-medium">Replay #{speaker.replay_id} · speaker {speaker.speaker_id}</div>
                <div className="text-muted-foreground">{speaker.names.join(", ") || "имя не найдено"} · пакетов {speaker.packet_count} · radio {speaker.radio_packet_count}</div>
                {!!speaker.candidate_profiles.length && <div className="mt-1 text-xs text-muted-foreground">Кандидаты: {speaker.candidate_profiles.map((candidate) => candidate.player_last_time_name || candidate.player_steamid).join(", ")}</div>}
              </div>
              <div className="flex shrink-0 gap-2">
                <Button size="sm" onClick={() => decide.mutate({ id: speaker.id, action: "resolve" })}><UserCheck className="size-4" /> Сопоставить</Button>
                <Button size="sm" variant="outline" onClick={() => decide.mutate({ id: speaker.id, action: "skip" })}><SkipForward className="size-4" /> Пропустить</Button>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}
