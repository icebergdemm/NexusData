import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check, History, Loader2, Play, ShieldAlert, SkipForward, Square, Volume2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { useCurrentAdmin } from "@/lib/auth"

interface VoiceRun {
  id: number
  status: "pending" | "running" | "cancelling" | "cancelled" | "completed" | "failed"
  total: number
  processed: number
  ready_count: number
  partial_count: number
  error_count: number
  segment_count: number
  profile_count: number
  last_replay_id: number | null
  last_message: string | null
  error: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  heartbeat_at: string | null
}

interface SpeakerProfileCandidate {
  player_id: number
  steam_id: string
  name: string
}

interface VoiceSpeaker {
  id: number
  replay_id: number
  speaker_id: number
  names: string[]
  candidate_profiles: SpeakerProfileCandidate[]
  steam_id: string | null
  resolution_status: "mapped" | "unresolved" | "skipped"
  mapping_source: string | null
  packet_count: number
  start_ms: number | null
  end_ms: number | null
  decided_at: string | null
}

interface VoiceSpeakerResponse {
  replay_id: number
  replay_filename: string
  status: string | null
  voice_index_error: string | null
  items: VoiceSpeaker[]
}

interface VoiceStatus {
  active: VoiceRun | null
  latest: VoiceRun | null
  log_retention_days: number
  index_version: number
}

interface VoiceLogResponse {
  items: Array<{
    id: number
    replay_id: number | null
    level: string
    status: string
    message: string
    created_at: string | null
  }>
  total: number
  page: number
  pages: number
  run: VoiceRun
  retention_days: number
}

function statusLabel(status: VoiceRun["status"]): string {
  return ({ pending: "В очереди", running: "Выполняется", cancelling: "Останавливается", completed: "Завершена", cancelled: "Остановлена", failed: "Ошибка" })[status]
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—"
  const seconds = Math.max(0, Math.round(ms / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}

function statusVariant(status: VoiceRun["status"]): "default" | "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive"
  if (status === "completed") return "default"
  if (status === "running" || status === "cancelling") return "secondary"
  return "outline"
}

export function VoiceIndexPage() {
  const { data: admin } = useCurrentAdmin()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [selectedReplayId, setSelectedReplayId] = useState<number | null>(null)
  const [decisionSteamIds, setDecisionSteamIds] = useState<Record<string, string>>({})
  const [profileSearchInputs, setProfileSearchInputs] = useState<Record<string, string>>({})
  const [activeSpeakerSearch, setActiveSpeakerSearch] = useState<number | null>(null)
  const [profileSearch, setProfileSearch] = useState("")
  const [debouncedProfileSearch, setDebouncedProfileSearch] = useState("")

  const statusQuery = useQuery({
    queryKey: ["voice-index-status"],
    queryFn: () => api.get<VoiceStatus>("/api/v1/admin/voice-index/status"),
    enabled: !!admin?.is_superuser,
    refetchInterval: (query) => query.state.data?.active ? 3000 : 15000,
  })
  const run = statusQuery.data?.active ?? statusQuery.data?.latest

  const speakerDetailsQuery = useQuery({
    queryKey: ["voice-index-speakers", selectedReplayId],
    queryFn: () => api.get<VoiceSpeakerResponse>(`/api/v1/admin/voice-index/replays/${selectedReplayId}/speakers`),
    enabled: !!admin?.is_superuser && detailsOpen && !!selectedReplayId,
  })

  useEffect(() => {
    const timeout = setTimeout(() => setDebouncedProfileSearch(profileSearch.trim()), 300)
    return () => clearTimeout(timeout)
  }, [profileSearch])

  const profileSearchQuery = useQuery({
    queryKey: ["voice-index-profile-search", debouncedProfileSearch],
    queryFn: () => api.get<{ players: Array<{ player_id: number; player_last_time_name: string; forced_display_name: string | null; player_steamid: string; steam_avatar_url: string | null }> }>(`/api/v1/admin/players/search-suggestions?q=${encodeURIComponent(debouncedProfileSearch)}`),
    enabled: !!activeSpeakerSearch && debouncedProfileSearch.length >= 2,
  })

  useEffect(() => {
    if (!speakerDetailsQuery.data) return
    const nextIds: Record<string, string> = {}
    const nextSearchInputs: Record<string, string> = {}
    speakerDetailsQuery.data.items.forEach((item) => {
      const key = String(item.speaker_id)
      const soleCandidate = item.resolution_status === "unresolved" && item.candidate_profiles.length === 1
        ? item.candidate_profiles[0]
        : null
      nextIds[key] = item.steam_id ?? soleCandidate?.steam_id ?? ""
      nextSearchInputs[key] = soleCandidate?.name ?? item.names[0] ?? ""
    })
    setDecisionSteamIds(nextIds)
    setProfileSearchInputs(nextSearchInputs)
    setActiveSpeakerSearch(null)
  }, [speakerDetailsQuery.data])

  const logsQuery = useQuery({
    queryKey: ["voice-index-logs", run?.id, page],
    queryFn: () => api.get<VoiceLogResponse>(`/api/v1/admin/voice-index/runs/${run?.id}/logs?page=${page}&page_size=50`),
    enabled: !!admin?.is_superuser && !!run?.id,
    refetchInterval: statusQuery.data?.active ? 3000 : false,
  })

  const startMutation = useMutation({
    mutationFn: () => api.post<VoiceRun>("/api/v1/admin/voice-index/runs"),
    onSuccess: () => {
      setPage(1)
      queryClient.invalidateQueries({ queryKey: ["voice-index-status"] })
      toast.success("Индексация поставлена в очередь")
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const cancelMutation = useMutation({
    mutationFn: () => api.post<VoiceRun>(`/api/v1/admin/voice-index/runs/${statusQuery.data?.active?.id}/cancel`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["voice-index-status"] })
      toast.success("Остановка индексации запрошена")
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const resolveMutation = useMutation({
    mutationFn: (decision: { speaker_id: number; action: "map" | "skip"; steam_id?: string }) => {
      return api.post(`/api/v1/admin/voice-index/replays/${selectedReplayId}/speakers/resolve`, { decisions: [decision] })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["voice-index-speakers", selectedReplayId] })
      queryClient.invalidateQueries({ queryKey: ["voice-index-status"] })
      queryClient.invalidateQueries({ queryKey: ["voice-index-logs"] })
      toast.success("Решения сохранены, реплей поставлен на переиндексацию")
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const openSpeakerDetails = (replayId: number) => {
    setSelectedReplayId(replayId)
    setDetailsOpen(true)
  }

  const submitSpeakerDecision = (speakerId: number, action: "map" | "skip") => {
    const steamId = decisionSteamIds[String(speakerId)]?.trim()
    if (action === "map" && !steamId) {
      toast.error("Сначала выберите профиль или укажите Steam/EOS ID")
      return
    }
    resolveMutation.mutate({
      speaker_id: speakerId,
      action,
      ...(action === "map" ? { steam_id: steamId } : {}),
    })
  }

  if (admin && !admin.is_superuser) {
    return (
      <Card><CardContent className="flex flex-col items-center gap-2 py-12 text-center text-muted-foreground">
        <ShieldAlert className="size-8" /> Голосовой архив доступен только суперпользователям.
      </CardContent></Card>
    )
  }

  const progress = run && run.total ? Math.round((run.processed / run.total) * 100) : 0

  return (
    <div>
      <PageHeader
        title="Голос"
        description="Индекс голосовых фрагментов из сохранённых replay. Аудио остаётся внутри .barodemo."
        icon={Volume2}
        actions={statusQuery.data?.active ? (
          <Button variant="destructive" onClick={() => cancelMutation.mutate()} disabled={cancelMutation.isPending || statusQuery.data.active.status === "cancelling"}>
            {cancelMutation.isPending || statusQuery.data.active.status === "cancelling" ? <Loader2 className="animate-spin" /> : <Square />}
            {statusQuery.data.active.status === "cancelling" ? "Останавливается" : "Остановить"}
          </Button>
        ) : (
          <Button onClick={() => startMutation.mutate()} disabled={startMutation.isPending}>
            {startMutation.isPending ? <Loader2 className="animate-spin" /> : <Play />}
            Запустить индексацию
          </Button>
        )}
      />

      <Card className="mb-4">
        <CardContent className="grid gap-4 p-4 md:grid-cols-3 lg:grid-cols-6">
          <div><div className="text-xs text-muted-foreground">Состояние</div><div className="mt-1"><Badge variant={run ? statusVariant(run.status) : "outline"}>{run ? statusLabel(run.status) : "Не запускалась"}</Badge></div></div>
          <div><div className="text-xs text-muted-foreground">Прогресс</div><div className="mt-1 text-lg font-semibold">{run ? `${run.processed} / ${run.total}` : "—"}{run?.total ? <span className="ml-2 text-sm font-normal text-muted-foreground">({progress}%)</span> : null}</div></div>
          <div><div className="text-xs text-muted-foreground">Готово</div><div className="mt-1 text-lg font-semibold text-emerald-500">{run ? run.ready_count : "—"}</div></div>
          <div><div className="text-xs text-muted-foreground">Частично</div><div className="mt-1 text-lg font-semibold text-amber-500">{run ? run.partial_count : "—"}</div></div>
          <div><div className="text-xs text-muted-foreground">Фрагменты / профили</div><div className="mt-1 text-lg font-semibold">{run ? `${run.segment_count} / ${run.profile_count}` : "—"}</div></div>
          <div><div className="text-xs text-muted-foreground">Ошибки</div><div className="mt-1 text-lg font-semibold text-destructive">{run ? run.error_count : "—"}</div></div>
          <div><div className="text-xs text-muted-foreground">Последнее сообщение</div><div className="mt-1 truncate text-sm" title={run?.last_message ?? undefined}>{run?.last_message ?? "—"}</div></div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden py-0">
        <div className="flex items-center gap-2 border-b px-4 py-3 font-medium"><History className="size-4" /> Журнал запуска <span className="text-xs font-normal text-muted-foreground">(хранится {statusQuery.data?.log_retention_days ?? 7} дней)</span></div>
        <CardContent className="p-0">
          {logsQuery.isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8" />)}</div> : logsQuery.data?.items.length ? logsQuery.data.items.map((item) => (
            <div key={item.id} className="flex items-start gap-3 border-b px-4 py-2.5 text-sm last:border-0">
              <Badge variant={item.level === "error" ? "destructive" : item.status === "partial" ? "secondary" : "outline"}>{item.status}</Badge>
              <div className="min-w-0 flex-1"><div>{item.message}</div>{item.replay_id && <div className="text-xs text-muted-foreground">replay #{item.replay_id}</div>}</div>
              {item.replay_id && (item.status === "partial" || item.status === "error") && <Button size="xs" variant="outline" onClick={() => openSpeakerDetails(item.replay_id!)}>Разобрать</Button>}
              <span className="shrink-0 text-xs text-muted-foreground">{formatBrowserDate(item.created_at)}</span>
            </div>
          )) : <div className="py-10 text-center text-sm text-muted-foreground">Журнал пуст. Пока даже ошибки не успели организоваться.</div>}
          {logsQuery.data && logsQuery.data.pages > 1 && <div className="flex items-center justify-center gap-2 border-t p-3"><Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Назад</Button><span className="text-sm text-muted-foreground">Страница {page} из {logsQuery.data.pages}</span><Button variant="outline" size="sm" disabled={page >= logsQuery.data.pages} onClick={() => setPage((value) => value + 1)}>Вперёд</Button></div>}
        </CardContent>
      </Card>

      <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
        <DialogContent className="max-h-[88vh] max-w-4xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Сопоставление говорящих{selectedReplayId ? ` в replay #${selectedReplayId}` : ""}</DialogTitle>
            <DialogDescription>
              Голос уже сохранён по найденным профилям. Здесь можно разобрать оставшиеся speaker_id: выбрать найденного кандидата, указать Steam/EOS ID вручную или пропустить говорящего.
            </DialogDescription>
          </DialogHeader>
          {speakerDetailsQuery.isLoading && <div className="py-8 text-center text-sm text-muted-foreground">Загружаю детали реплея…</div>}
          {speakerDetailsQuery.isError && <div className="py-8 text-center text-sm text-destructive">Не удалось загрузить детали реплея.</div>}
          {speakerDetailsQuery.data && (
            <div className="space-y-3">
              <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                {speakerDetailsQuery.data.replay_filename || `replay #${speakerDetailsQuery.data.replay_id}`}
                {speakerDetailsQuery.data.voice_index_error ? ` · ${speakerDetailsQuery.data.voice_index_error}` : ""}
              </div>
              {speakerDetailsQuery.data.items.filter((item) => item.resolution_status === "unresolved").map((item) => {
                const key = String(item.speaker_id)
                const steamId = decisionSteamIds[key] ?? ""
                const soleCandidate = item.candidate_profiles.length === 1 ? item.candidate_profiles[0] : null
                return (
                  <div key={item.id} className="space-y-3 rounded-lg border p-3">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <div className="font-medium">speaker_id: <span className="font-mono">{item.speaker_id}</span></div>
                        <div className="text-xs text-muted-foreground">{item.names.length ? `Ники: ${item.names.join(", ")}` : "Ник в replay не найден"} · {item.packet_count} пакетов · {formatDuration((item.end_ms ?? 0) - (item.start_ms ?? 0))}</div>
                      </div>
                        <Badge variant={item.resolution_status === "mapped" ? "default" : item.resolution_status === "skipped" ? "secondary" : "destructive"}>
                        {item.resolution_status === "mapped" ? `сопоставлен (${item.mapping_source ?? ""})` : item.resolution_status === "skipped" ? "пропущен" : "нужно решение"}
                      </Badge>
                    </div>
                    {soleCandidate && (
                      <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-sm">
                        Единственный кандидат выбран автоматически: <span className="font-medium">{soleCandidate.name}</span>
                        <span className="ml-1 font-mono text-xs text-muted-foreground">{soleCandidate.steam_id}</span>
                      </div>
                    )}
                    {item.candidate_profiles.length > 1 && (
                      <Select value={steamId || undefined} onValueChange={(value) => setDecisionSteamIds((current) => ({ ...current, [key]: value }))}>
                        <SelectTrigger><SelectValue placeholder="Выбрать профиль по нику" /></SelectTrigger>
                        <SelectContent>
                          {item.candidate_profiles.map((candidate) => <SelectItem key={candidate.steam_id} value={candidate.steam_id}>{candidate.name} · {candidate.steam_id}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    )}
                    <div className="relative">
                      <Input
                        value={profileSearchInputs[key] ?? ""}
                        placeholder="Поиск профиля по нику, Steam/EOS ID…"
                        onFocus={() => { setActiveSpeakerSearch(item.speaker_id); setProfileSearch(profileSearchInputs[key] ?? "") }}
                        onChange={(event) => {
                          setActiveSpeakerSearch(item.speaker_id)
                          setProfileSearchInputs((current) => ({ ...current, [key]: event.target.value }))
                          setProfileSearch(event.target.value)
                        }}
                      />
                      {activeSpeakerSearch === item.speaker_id && profileSearchQuery.data?.players.length ? (
                        <div className="absolute top-full left-0 z-30 mt-1 w-full overflow-hidden rounded-md border bg-popover shadow-md">
                          {profileSearchQuery.data.players.map((player) => (
                            <button
                              key={player.player_id}
                              type="button"
                              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm hover:bg-accent"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => {
                                setDecisionSteamIds((current) => ({ ...current, [key]: player.player_steamid }))
                                setProfileSearchInputs((current) => ({ ...current, [key]: player.forced_display_name || player.player_last_time_name }))
                                setProfileSearch("")
                                setActiveSpeakerSearch(null)
                              }}
                            >
                              <span className="min-w-0 flex-1 truncate">{player.forced_display_name || player.player_last_time_name}</span>
                              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{player.player_steamid}</span>
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <Input value={steamId} placeholder="Или введите Steam/EOS ID вручную" onChange={(event) => setDecisionSteamIds((current) => ({ ...current, [key]: event.target.value }))} />
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-xs text-muted-foreground">Будет сопоставлен: {steamId || "ID не указан"}</span>
                      <div className="flex gap-2">
                        <Button size="sm" variant="outline" disabled={resolveMutation.isPending} onClick={() => submitSpeakerDecision(item.speaker_id, "skip")}><SkipForward /> Пропустить</Button>
                        <Button size="sm" variant="default" disabled={!steamId.trim() || resolveMutation.isPending} onClick={() => submitSpeakerDecision(item.speaker_id, "map")}><Check /> Сопоставить</Button>
                      </div>
                    </div>
                  </div>
                )
              })}
              {!speakerDetailsQuery.data.items.some((item) => item.resolution_status === "unresolved") && <div className="py-8 text-center text-sm text-muted-foreground">Все говорящие уже сопоставлены или пропущены.</div>}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDetailsOpen(false)}>Закрыть</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
