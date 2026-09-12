import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown, Mic2, Play, Radio, RefreshCw, Search, SkipForward, UserCheck } from "lucide-react"
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
  current_replay_id: number | null
  total_replays: number
  processed_replays: number
  mapped_speakers: number
  unresolved_speakers: number
  materialized_clips: number
  error_message: string | null
}

interface VoiceProfileImport {
  id: number
  import_key: string
  status: string
  manifest_sha256: string
  total_profiles: number
  total_clips: number
  imported_profiles: number
  imported_clips: number
  error_message: string | null
}

export interface VoiceProfileClip {
  id: number
  source_replay_id: number | null
  start_ms: number
  end_ms: number
  packet_count: number
  radio_packet_count: number
  storage_size: number
  slot: number | null
  band: string | null
  audio_url: string
}

export interface VoiceProfilePlayer {
  player_id: number | null
  steam_id: string
  display_name: string
  clips: VoiceProfileClip[]
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

function formatBytes(value: number) {
  if (value < 1024) return `${value} Б`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} КБ`
  return `${(value / (1024 * 1024)).toFixed(1)} МБ`
}

function formatDuration(startMs: number, endMs: number) {
  return `${Math.max(0, (endMs - startMs) / 1000).toFixed(1)} с`
}

function bandLabel(band: string | null) {
  if (band === "early") return "early"
  if (band === "middle") return "middle"
  if (band === "latest") return "latest"
  return "без слота"
}

export function VoiceProfilePlayerCard({
  profile,
  initiallyExpanded = false,
  clips = profile.clips,
}: {
  profile: VoiceProfilePlayer
  initiallyExpanded?: boolean
  clips?: VoiceProfileClip[]
}) {
  const [expanded, setExpanded] = useState(initiallyExpanded)
  const [activeClipId, setActiveClipId] = useState<number | null>(
    initiallyExpanded ? (clips.at(-1)?.id ?? null) : null,
  )
  const clipsId = `voice-profile-clips-${profile.steam_id}`

  return (
    <Card className="overflow-hidden">
      <CardHeader className="p-3 sm:p-4">
        <button
          type="button"
          className="flex w-full items-center justify-between gap-3 text-left"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-controls={clipsId}
        >
          <CardTitle className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <span className="truncate">{profile.display_name}</span>
            <Badge variant="outline" className="px-1.5 py-0 text-[10px]">{profile.clips.length} клипов</Badge>
            <span className="font-mono text-[10px] font-normal text-muted-foreground">{profile.steam_id}</span>
          </CardTitle>
          <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            {expanded ? "Скрыть" : "Показать"}
            <ChevronDown className={`size-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
          </span>
        </button>
      </CardHeader>
      {expanded && (
        <CardContent id={clipsId} className="space-y-1 border-t px-3 pb-3 pt-2 sm:px-4 sm:pb-3">
          {clips.map((clip) => (
            <div key={clip.id} className="rounded-md border px-2 py-1.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px]">
                <Badge variant="secondary" className="px-1.5 py-0">slot {(clip.slot ?? 0) + 1}</Badge>
                <Badge variant="outline" className="px-1.5 py-0">{bandLabel(clip.band)}</Badge>
                {clip.radio_packet_count > 0 && <Badge variant="outline" className="px-1.5 py-0"><Radio className="size-3" /> radio</Badge>}
                <span className="text-muted-foreground">Replay #{clip.source_replay_id ?? "—"} · {formatDuration(clip.start_ms, clip.end_ms)} · {clip.packet_count} пакетов · {formatBytes(clip.storage_size)}</span>
                <button
                  type="button"
                  className="ml-auto inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10px] font-medium text-primary hover:bg-accent"
                  onClick={() => setActiveClipId((value) => value === clip.id ? null : clip.id)}
                  aria-label={`${activeClipId === clip.id ? "Скрыть" : "Слушать"} клип ${clip.id}`}
                >
                  <Play className="size-3" /> {activeClipId === clip.id ? "Скрыть" : "Слушать"}
                </button>
              </div>
              {activeClipId === clip.id && (
                <audio
                  controls
                  preload="none"
                  src={clip.audio_url}
                  aria-label={`Клип ${clip.id}, replay ${clip.source_replay_id ?? "неизвестен"}`}
                  className="mt-1 h-8 w-full"
                />
              )}
            </div>
          ))}
        </CardContent>
      )}
    </Card>
  )
}

export function VoiceProfilesPage() {
  const queryClient = useQueryClient()
  const [replayIds, setReplayIds] = useState("")
  const [importKey, setImportKey] = useState("")
  const [profileSearch, setProfileSearch] = useState("")
  const [clipFilter, setClipFilter] = useState<"all" | "radio" | "plain">("all")

  const profiles = useQuery({
    queryKey: ["voice-profile-players"],
    queryFn: () => api.get<{ players: VoiceProfilePlayer[] }>("/api/v1/admin/voice-profiles/players"),
    refetchInterval: 5000,
  })
  const speakers = useQuery({
    queryKey: ["voice-profile-speakers"],
    queryFn: () => api.get<{ speakers: Speaker[] }>("/api/v1/admin/voice-profiles/speakers?status=unresolved"),
    refetchInterval: 5000,
  })
  const latestRun = useQuery({
    queryKey: ["voice-profile-latest-run"],
    queryFn: () => api.get<{ run: Job | null }>("/api/v1/admin/voice-profiles/runs/latest"),
    refetchInterval: (query) => {
      const status = query.state.data?.run?.status
      return status === "queued" || status === "running" ? 3000 : false
    },
  })
  const latestImport = useQuery({
    queryKey: ["voice-profile-latest-import"],
    queryFn: () => api.get<{ import: VoiceProfileImport | null }>("/api/v1/admin/voice-profiles/imports/latest"),
    refetchInterval: (query) => query.state.data?.import?.status === "importing" ? 3000 : false,
  })

  const start = useMutation({
    mutationFn: () => {
      const ids = replayIds.split(/[\s,;]+/).filter(Boolean).map(Number)
      return api.post<Job>("/api/v1/admin/voice-profiles/runs", { replay_ids: ids, mode: "backfill" })
    },
    onSuccess: () => {
      setReplayIds("")
      toast.success("Backfill поставлен в очередь")
      void queryClient.invalidateQueries({ queryKey: ["voice-profile-latest-run"] })
      void queryClient.invalidateQueries({ queryKey: ["voice-profile-speakers"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const validateImport = useMutation({
    mutationFn: () => api.post<VoiceProfileImport>("/api/v1/admin/voice-profiles/imports", { import_key: importKey.trim() }),
    onSuccess: (result) => {
      toast.success(`Пакет проверен: ${result.total_profiles} профилей, ${result.total_clips} клипов`)
      void queryClient.invalidateQueries({ queryKey: ["voice-profile-latest-import"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const commitImport = useMutation({
    mutationFn: (id: number) => api.post<VoiceProfileImport>(`/api/v1/admin/voice-profiles/imports/${id}/commit`),
    onSuccess: (result) => {
      toast.success(`Импорт завершён: ${result.imported_clips} клипов`)
      void queryClient.invalidateQueries({ queryKey: ["voice-profile-latest-import"] })
      void queryClient.invalidateQueries({ queryKey: ["voice-profile-players"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "resolve" | "skip" }) => {
      if (action === "skip") return api.post(`/api/v1/admin/voice-profiles/speakers/${id}/skip`)
      const steamId = window.prompt("SteamID игрока из NexusData")?.trim()
      if (!steamId) throw new Error("SteamID не указан")
      return api.post(`/api/v1/admin/voice-profiles/speakers/${id}/resolve`, { steam_id: steamId })
    },
    onSuccess: () => {
      toast.success("Решение сохранено")
      void queryClient.invalidateQueries({ queryKey: ["voice-profile-speakers"] })
      void queryClient.invalidateQueries({ queryKey: ["voice-profile-players"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const refresh = () => {
    void profiles.refetch()
    void speakers.refetch()
    void latestRun.refetch()
    void latestImport.refetch()
  }
  const run = latestRun.data?.run
  const normalizedSearch = profileSearch.trim().toLocaleLowerCase()
  const filteredProfiles = profiles.data?.players.flatMap((profile) => {
    const searchable = `${profile.display_name} ${profile.steam_id}`.toLocaleLowerCase()
    if (normalizedSearch && !searchable.includes(normalizedSearch)) return []
    const clips = profile.clips.filter((clip) => clipFilter === "all" || (clipFilter === "radio" ? clip.radio_packet_count > 0 : clip.radio_packet_count === 0))
    return clips.length ? [{ profile, clips }] : []
  }) ?? []

  return (
    <div className="space-y-6">
      <PageHeader
        title="Голосовые профили"
        description="Backfill старых реплеев, ручное сопоставление speaker_id и прослушивание выбранных клипов. Новые реплеи здесь пока не сканируются."
        icon={Mic2}
        actions={<Button variant="outline" onClick={refresh}><RefreshCw className="size-4" /> Обновить</Button>}
      />

      <Card>
        <CardHeader><CardTitle>Ручной backfill</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Input className="min-w-80 flex-1" value={replayIds} onChange={(event) => setReplayIds(event.target.value)} placeholder="ID реплеев: 101, 102, 103" />
          <Button onClick={() => start.mutate()} disabled={start.isPending || !replayIds.trim()}><Play className="size-4" /> Запустить</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3"><CardTitle>Импорт локального корпуса</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Input className="min-w-80 flex-1 font-mono text-xs" value={importKey} onChange={(event) => setImportKey(event.target.value)} placeholder="Ключ пакета после загрузки по SFTP" />
            <Button variant="outline" onClick={() => validateImport.mutate()} disabled={validateImport.isPending || !importKey.trim()}>Проверить пакет</Button>
          </div>
          {latestImport.data?.import && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border px-3 py-2 text-xs">
              <span className="font-mono">{latestImport.data.import.import_key}</span>
              <Badge variant={latestImport.data.import.status === "failed" ? "destructive" : "secondary"}>{latestImport.data.import.status}</Badge>
              <span>{latestImport.data.import.total_profiles} профилей · {latestImport.data.import.total_clips} клипов</span>
              {latestImport.data.import.status === "validated" && (
                <Button size="sm" onClick={() => commitImport.mutate(latestImport.data!.import!.id)} disabled={commitImport.isPending}>Импортировать</Button>
              )}
              {latestImport.data.import.error_message && <span className="basis-full text-destructive">{latestImport.data.import.error_message}</span>}
            </div>
          )}
          <p className="text-xs text-muted-foreground">Сначала положите пакет в staging-каталог NexusData по SFTP. Проверка сверит manifest и SHA-256; импорт идемпотентный.</p>
        </CardContent>
      </Card>

      {run && (
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2">Последний запуск #{run.id}<Badge variant={run.status === "failed" ? "destructive" : "secondary"}>{run.status}</Badge></CardTitle></CardHeader>
          <CardContent className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-5">
            <span>Реплеи: {run.processed_replays}/{run.total_replays}</span>
            <span>Текущий: {run.current_replay_id ?? "—"}</span>
            <span>Сопоставлено: {run.mapped_speakers}</span>
            <span>Unresolved: {run.unresolved_speakers}</span>
            <span>Клипы: {run.materialized_clips}</span>
            {run.error_message && <span className="text-destructive sm:col-span-2 lg:col-span-5">{run.error_message}</span>}
          </CardContent>
        </Card>
      )}

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-base font-semibold">Готовые профили</h2>
          <span className="text-xs text-muted-foreground">Клипы скрыты до нажатия</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-64 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
            <Input className="pl-9" value={profileSearch} onChange={(event) => setProfileSearch(event.target.value)} placeholder="Игрок или SteamID" />
          </div>
          <div className="flex gap-1" role="group" aria-label="Фильтр клипов">
            <Button size="sm" variant={clipFilter === "all" ? "secondary" : "ghost"} onClick={() => setClipFilter("all")}>Все</Button>
            <Button size="sm" variant={clipFilter === "plain" ? "secondary" : "ghost"} onClick={() => setClipFilter("plain")}>Обычные</Button>
            <Button size="sm" variant={clipFilter === "radio" ? "secondary" : "ghost"} onClick={() => setClipFilter("radio")}>Radio</Button>
          </div>
        </div>
        {profiles.isLoading && <p className="text-sm text-muted-foreground">Загрузка профилей…</p>}
        {!profiles.isLoading && !filteredProfiles.length && <p className="text-sm text-muted-foreground">По этому фильтру профилей нет.</p>}
        <div className="space-y-2">
          {filteredProfiles.map(({ profile, clips }) => <VoiceProfilePlayerCard key={profile.steam_id} profile={profile} clips={clips} />)}
        </div>
      </section>

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
