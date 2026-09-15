import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Download, Mic2, Radio, Search } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

interface PlayerSuggestion {
  player_id: number
  player_last_time_name: string
  forced_display_name: string | null
  player_steamid: string
}

interface VoiceClip {
  id: number
  source_replay_id: number | null
  source_replay_filename: string
  source_kind: "round" | "lobby" | "lobby_dump" | null
  radio: boolean
  start_ms: number
  end_ms: number
  packet_count: number
  recorded_at: string | null
  slot: number
  band: "early" | "middle" | "latest"
  audio_url: string
}

interface VoiceProfile {
  player: { player_id: number; steam_id: string; display_name: string }
  clips: VoiceClip[]
  stats: { selected_clips: number; radio_clips: number; replay_kinds: string[] }
  voiceprint: { status: "ready" | "low_quality" | "pending"; replay_samples: number; aggregate_channels: string[] }
}

function displayName(player: PlayerSuggestion) {
  return player.forced_display_name || player.player_last_time_name
}

function duration(clip: VoiceClip) {
  return `${Math.max(0, (clip.end_ms - clip.start_ms) / 1000).toFixed(1)} с`
}

function voiceprintLabel(status: VoiceProfile["voiceprint"]["status"]) {
  if (status === "ready") return "готов"
  if (status === "low_quality") return "мало материала"
  return "ожидает обработки"
}

function ClipCard({ clip }: { clip: VoiceClip }) {
  return (
    <div className="rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="secondary">Replay #{clip.source_replay_id ?? "—"}</Badge>
        <Badge variant="outline">{clip.source_kind || "unknown"}</Badge>
        {clip.radio && <Badge variant="outline"><Radio className="size-3" /> radio</Badge>}
        <span className="text-muted-foreground">{duration(clip)} · {clip.packet_count} пакетов</span>
        <a className="ml-auto" href={clip.audio_url} download={`voice-profile-${clip.id}.wav`}>
          <Button size="sm" variant="ghost"><Download className="size-4" /> WAV</Button>
        </a>
      </div>
      <audio controls preload="none" src={clip.audio_url} className="mt-2 h-8 w-full" aria-label={`Голосовой клип ${clip.id}`} />
    </div>
  )
}

function ClipGroup({ title, clips }: { title: string; clips: VoiceClip[] }) {
  return (
    <Card>
      <CardHeader className="pb-3"><CardTitle className="text-base">{title} <Badge variant="outline">{clips.length}</Badge></CardTitle></CardHeader>
      <CardContent className="space-y-2">
        {clips.length ? clips.map((clip) => <ClipCard key={clip.id} clip={clip} />) : <p className="text-sm text-muted-foreground">Пока нет клипов.</p>}
      </CardContent>
    </Card>
  )
}

export function VoiceProfilesPage() {
  const [query, setQuery] = useState("")
  const [selectedPlayerId, setSelectedPlayerId] = useState<number | null>(null)
  const suggestions = useQuery({
    queryKey: ["voice-profile-player-search", query.trim()],
    queryFn: () => api.get<{ players: PlayerSuggestion[] }>(`/api/v1/admin/players/search-suggestions?q=${encodeURIComponent(query.trim())}`),
    enabled: query.trim().length >= 2,
  })
  const profile = useQuery({
    queryKey: ["voice-profile", selectedPlayerId],
    queryFn: () => api.get<VoiceProfile>(`/api/v1/admin/voice-profiles/players/${selectedPlayerId}`),
    enabled: selectedPlayerId !== null,
  })
  const data = profile.data

  return (
    <div className="space-y-6">
      <PageHeader
        title="Голосовые профили"
        description="Только NewWorldStreamer nwdemo-v2: прямой SteamID, выбранные 3 ранних, 3 средних и 5 последних клипов."
        icon={Mic2}
      />

      <Card>
        <CardHeader className="pb-3"><CardTitle>Игрок</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" />
            <Input value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" placeholder="Ник или SteamID" />
          </div>
          {query.trim().length >= 2 && (
            <div className="overflow-hidden rounded-md border">
              {suggestions.isLoading && <p className="p-3 text-sm text-muted-foreground">Поиск…</p>}
              {!suggestions.isLoading && !(suggestions.data?.players.length) && <p className="p-3 text-sm text-muted-foreground">Игрок не найден.</p>}
              {suggestions.data?.players.map((player) => (
                <button key={player.player_id} type="button" className="flex w-full items-center justify-between gap-3 border-b px-3 py-2 text-left last:border-0 hover:bg-accent" onClick={() => { setSelectedPlayerId(player.player_id); setQuery(displayName(player)) }}>
                  <span>{displayName(player)}</span>
                  <span className="font-mono text-xs text-muted-foreground">{player.player_steamid}</span>
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {profile.isLoading && <p className="text-sm text-muted-foreground">Загрузка профиля…</p>}
      {profile.isError && <p className="text-sm text-destructive">Профиль не загрузился.</p>}
      {data && (
        <>
          <Card>
            <CardHeader className="pb-3"><CardTitle className="flex flex-wrap items-center gap-2">{data.player.display_name}<span className="font-mono text-xs font-normal text-muted-foreground">{data.player.steam_id}</span></CardTitle></CardHeader>
            <CardContent className="flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted-foreground">
              <span>{data.stats.selected_clips} выбранных клипов</span>
              <span>{data.stats.radio_clips} radio</span>
              <span>Voiceprint: {voiceprintLabel(data.voiceprint.status)} · {data.voiceprint.replay_samples} реплеев</span>
            </CardContent>
          </Card>
          <div className="grid gap-4 xl:grid-cols-3">
            <ClipGroup title="Ранние 3" clips={data.clips.filter((clip) => clip.band === "early")} />
            <ClipGroup title="Середина 3" clips={data.clips.filter((clip) => clip.band === "middle")} />
            <ClipGroup title="Последние 5" clips={data.clips.filter((clip) => clip.band === "latest")} />
          </div>
        </>
      )}
    </div>
  )
}
