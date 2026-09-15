import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  Mic2,
  Radio,
  RefreshCw,
  Search,
  Terminal,
} from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, api } from "@/lib/api";
import { formatBrowserDate } from "@/lib/format-date";

interface PlayerSuggestion {
  player_id: number;
  player_last_time_name: string;
  forced_display_name: string | null;
  player_steamid: string;
}
interface VoiceClip {
  id: number;
  source_replay_id: number | null;
  source_replay_filename: string;
  source_kind: "round" | "lobby" | "lobby_dump" | null;
  radio: boolean;
  start_ms: number;
  end_ms: number;
  packet_count: number;
  recorded_at: string | null;
  slot: number;
  band: "early" | "middle" | "latest";
  audio_url: string;
}
interface VoiceProfile {
  player: { player_id: number; steam_id: string; display_name: string };
  clips: VoiceClip[];
  stats: {
    selected_clips: number;
    radio_clips: number;
    replay_kinds: string[];
  };
  voiceprint: {
    status: "ready" | "low_quality" | "pending";
    replay_samples: number;
    aggregate_channels: string[];
  };
}
interface ScannerLogEntry {
  replay_id: number;
  state: "pending" | "processing" | "ready" | "failed";
  replay_kind: "round" | "lobby" | "lobby_dump" | null;
  recorded_at: string | null;
  processed_at: string | null;
  error: string | null;
  players: { player_id: number; display_name: string }[];
}
interface ScannerStatus {
  enabled: boolean;
  counts: Record<"pending" | "processing" | "ready" | "failed", number>;
  log: ScannerLogEntry[];
}

const displayName = (player: PlayerSuggestion) =>
  player.forced_display_name || player.player_last_time_name;
const duration = (clip: VoiceClip) =>
  `${Math.max(0, (clip.end_ms - clip.start_ms) / 1000).toFixed(1)} с`;
function voiceprintLabel(status: VoiceProfile["voiceprint"]["status"]) {
  return status === "ready"
    ? "готов"
    : status === "low_quality"
      ? "мало материала"
      : "ожидает обработки";
}
function stateClass(state: ScannerLogEntry["state"]) {
  return state === "ready"
    ? "text-emerald-400"
    : state === "failed"
      ? "text-red-400"
      : state === "processing"
        ? "text-amber-300"
        : "text-sky-300";
}

function ClipCard({ clip }: { clip: VoiceClip }) {
  return (
    <div className="rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="secondary">
          Replay #{clip.source_replay_id ?? "—"}
        </Badge>
        <Badge variant="outline">{clip.source_kind || "unknown"}</Badge>
        {clip.radio && (
          <Badge variant="outline">
            <Radio className="size-3" /> radio
          </Badge>
        )}
        <span className="text-muted-foreground">
          {duration(clip)} · {clip.packet_count} пакетов
        </span>
        <a
          className="ml-auto"
          href={clip.audio_url}
          download={`voice-profile-${clip.id}.wav`}
        >
          <Button size="sm" variant="ghost">
            <Download className="size-4" /> WAV
          </Button>
        </a>
      </div>
      <audio
        controls
        preload="none"
        src={clip.audio_url}
        className="mt-2 h-8 w-full"
        aria-label={`Голосовой клип ${clip.id}`}
      />
    </div>
  );
}

function ClipGroup({ title, clips }: { title: string; clips: VoiceClip[] }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          {title} <Badge variant="outline">{clips.length}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {clips.length ? (
          clips.map((clip) => <ClipCard key={clip.id} clip={clip} />)
        ) : (
          <p className="text-sm text-muted-foreground">Пока нет клипов.</p>
        )}
      </CardContent>
    </Card>
  );
}

function ScannerConsole({
  openProfile,
}: {
  openProfile: (player: { player_id: number; display_name: string }) => void;
}) {
  const queryClient = useQueryClient();
  const scanner = useQuery({
    queryKey: ["voice-profile-scanner"],
    queryFn: () =>
      api.get<ScannerStatus>("/api/v1/admin/voice-profiles/scanner"),
    refetchInterval: 15_000,
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.patch<ScannerStatus>("/api/v1/admin/voice-profiles/scanner", {
        enabled,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["voice-profile-scanner"], data);
      toast.success(data.enabled ? "Сканнер включён" : "Сканнер выключен");
    },
    onError: (error: ApiError) => toast.error(error.message),
  });
  const data = scanner.data;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-base">Сканнер nwdemo-v2</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Раз в минуту берёт один необработанный архив. Только `.nwdemo`;
              легаси остаётся за бортом.
            </p>
          </div>
          <Switch
            checked={data?.enabled ?? false}
            disabled={!data || toggle.isPending}
            onCheckedChange={(enabled) => toggle.mutate(enabled)}
            aria-label="Включить сканнер голосовых профилей"
          />
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Badge variant={data?.enabled ? "default" : "secondary"}>
            {data?.enabled ? "включён" : "выключен"}
          </Badge>
          <span className="text-sm text-muted-foreground">
            Переключатель сохраняется в NexusData и переживает рестарт. Чудес,
            конечно, не обещает.
          </span>
          <Button
            className="ml-auto"
            size="sm"
            variant="ghost"
            onClick={() => scanner.refetch()}
            disabled={scanner.isFetching}
          >
            <RefreshCw
              className={scanner.isFetching ? "size-4 animate-spin" : "size-4"}
            />{" "}
            Обновить
          </Button>
        </CardContent>
      </Card>
      {scanner.isError && (
        <p className="text-sm text-destructive">
          Статус сканнера не загрузился.
        </p>
      )}
      {data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {(
              [
                ["pending", "Ожидают"],
                ["processing", "В работе"],
                ["ready", "Готовы"],
                ["failed", "Ошибки"],
              ] as const
            ).map(([state, label]) => (
              <Card
                key={state}
                className={
                  state === "failed" && data.counts.failed
                    ? "border-red-900/60"
                    : undefined
                }
              >
                <CardContent className="p-4">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className={`mt-1 font-mono text-2xl ${stateClass(state)}`}>
                    {data.counts[state]}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
          <Card className="overflow-hidden border-zinc-800 bg-zinc-950 text-zinc-300">
            <CardHeader className="flex-row items-center gap-2 space-y-0 border-b border-zinc-800 py-3">
              <Terminal className="size-4 text-sky-400" />
              <CardTitle className="font-mono text-sm font-medium text-zinc-100">
                scanner.log
              </CardTitle>
              <span className="ml-auto font-mono text-xs text-zinc-500">
                последние {data.log.length}
              </span>
            </CardHeader>
            <CardContent className="max-h-[32rem] overflow-auto p-0 font-mono text-xs leading-6">
              {!data.log.length && (
                <p className="px-4 py-6 text-zinc-500">
                  Нет nwdemo-v2 реплеев для журнала.
                </p>
              )}
              {data.log.map((entry) => (
                <div
                  key={entry.replay_id}
                  className="border-b border-zinc-900 px-4 py-2 last:border-0 hover:bg-zinc-900/60"
                >
                  <div className="flex flex-wrap gap-x-3 gap-y-1">
                    <span className="text-zinc-500">
                      {formatBrowserDate(
                        entry.processed_at || entry.recorded_at,
                      ) || "—"}
                    </span>
                    <span className={stateClass(entry.state)}>
                      {entry.state.toUpperCase()}
                    </span>
                    <span className="text-zinc-200">
                      replay #{entry.replay_id}
                    </span>
                    <span className="text-zinc-500">
                      {entry.replay_kind || "unknown"}
                    </span>
                    {entry.players.map((player) => (
                      <button
                        key={player.player_id}
                        type="button"
                        onClick={() => openProfile(player)}
                        className="text-sky-300 underline decoration-sky-800 underline-offset-2 hover:text-sky-100"
                      >
                        {player.display_name}
                      </button>
                    ))}
                  </div>
                  {entry.error && (
                    <p className="break-words text-red-300">{entry.error}</p>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

export function VoiceProfilesPage() {
  const [tab, setTab] = useState("profiles");
  const [query, setQuery] = useState("");
  const [selectedPlayerId, setSelectedPlayerId] = useState<number | null>(null);
  const suggestions = useQuery({
    queryKey: ["voice-profile-player-search", query.trim()],
    queryFn: () =>
      api.get<{ players: PlayerSuggestion[] }>(
        `/api/v1/admin/players/search-suggestions?q=${encodeURIComponent(query.trim())}`,
      ),
    enabled: query.trim().length >= 2,
  });
  const profile = useQuery({
    queryKey: ["voice-profile", selectedPlayerId],
    queryFn: () =>
      api.get<VoiceProfile>(
        `/api/v1/admin/voice-profiles/players/${selectedPlayerId}`,
      ),
    enabled: selectedPlayerId !== null,
  });
  const data = profile.data;
  const openProfile = (player: { player_id: number; display_name: string }) => {
    setSelectedPlayerId(player.player_id);
    setQuery(player.display_name);
    setTab("profiles");
  };
  return (
    <div className="space-y-6">
      <PageHeader
        title="Голосовые профили"
        description="Только NewWorldStreamer nwdemo-v2: прямой SteamID, выбранные 3 ранних, 3 средних и 5 последних клипов."
        icon={Mic2}
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList variant="line" className="mb-5">
          <TabsTrigger value="profiles">Профили игроков</TabsTrigger>
          <TabsTrigger value="scanner">Сканнер и журнал</TabsTrigger>
        </TabsList>
        <TabsContent value="profiles" className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle>Игрок</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="pl-9"
                  placeholder="Ник или SteamID"
                />
              </div>
              {query.trim().length >= 2 && (
                <div className="overflow-hidden rounded-md border">
                  {suggestions.isLoading && (
                    <p className="p-3 text-sm text-muted-foreground">Поиск…</p>
                  )}
                  {!suggestions.isLoading &&
                    !suggestions.data?.players.length && (
                      <p className="p-3 text-sm text-muted-foreground">
                        Игрок не найден.
                      </p>
                    )}
                  {suggestions.data?.players.map((player) => (
                    <button
                      key={player.player_id}
                      type="button"
                      className="flex w-full items-center justify-between gap-3 border-b px-3 py-2 text-left last:border-0 hover:bg-accent"
                      onClick={() => {
                        setSelectedPlayerId(player.player_id);
                        setQuery(displayName(player));
                      }}
                    >
                      <span>{displayName(player)}</span>
                      <span className="font-mono text-xs text-muted-foreground">
                        {player.player_steamid}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
          {profile.isLoading && (
            <p className="text-sm text-muted-foreground">Загрузка профиля…</p>
          )}
          {profile.isError && (
            <p className="text-sm text-destructive">Профиль не загрузился.</p>
          )}
          {data && (
            <>
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex flex-wrap items-center gap-2">
                    {data.player.display_name}
                    <span className="font-mono text-xs font-normal text-muted-foreground">
                      {data.player.steam_id}
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted-foreground">
                  <span>{data.stats.selected_clips} выбранных клипов</span>
                  <span>{data.stats.radio_clips} radio</span>
                  <span>
                    Voiceprint: {voiceprintLabel(data.voiceprint.status)} ·{" "}
                    {data.voiceprint.replay_samples} реплеев
                  </span>
                </CardContent>
              </Card>
              <div className="grid gap-4 xl:grid-cols-3">
                <ClipGroup
                  title="Ранние 3"
                  clips={data.clips.filter((clip) => clip.band === "early")}
                />
                <ClipGroup
                  title="Середина 3"
                  clips={data.clips.filter((clip) => clip.band === "middle")}
                />
                <ClipGroup
                  title="Последние 5"
                  clips={data.clips.filter((clip) => clip.band === "latest")}
                />
              </div>
            </>
          )}
        </TabsContent>
        <TabsContent value="scanner">
          <ScannerConsole openProfile={openProfile} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
