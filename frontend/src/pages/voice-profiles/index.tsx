import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Cpu,
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
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
    algorithm: string;
    status: "ready" | "collecting" | "model_missing";
    replay_samples: number;
    aggregate_channels: string[];
    candidates: {
      steam_id: string;
      player_id: number | null;
      display_name: string | null;
      score: number;
      sample_count: number;
      duration_ms: number;
      source_channel: "normal" | "radio";
    }[];
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
  active: {
    replay_id: number;
    replay_kind: "round" | "lobby" | "lobby_dump" | null;
    phase: string;
    percent: number;
    elapsed_ms: number;
    heartbeat_age_ms: number | null;
    packets_read: number;
    bytes_read: number;
    speakers_seen: number;
    speakers_completed: number;
  } | null;
  log: ScannerLogEntry[];
}
interface VoiceprintMonitorLogEntry {
  id: string;
  at: string | null;
  state: "ready" | "low" | "failed";
  replay_id: number;
  channel: "normal" | "radio" | null;
  duration_ms: number | null;
  player: { player_id: number; display_name: string } | null;
  error: string | null;
}
interface VoiceprintMonitor {
  enabled: boolean;
  model: {
    status: "ready" | "missing" | "invalid";
    algorithm: string;
    dimensions: number;
    sample_rate: number;
  };
  counts: {
    fingerprints: number;
    good_fingerprints: number;
    eligible_signatures: number;
    queued_replays: number;
  };
  speech_gate: {
    version: string;
    normal: {
      checked: number;
      accepted: number;
      rejected: number;
      speech_ms: number;
    };
    radio: {
      checked: number;
      accepted: number;
      rejected: number;
      speech_ms: number;
    };
  };
  log: VoiceprintMonitorLogEntry[];
}
interface VoiceSyntheticPlayerReport {
  player_id: number;
  steam_id: string;
  display_name: string;
  result: "synthetic" | "processing" | "waiting" | "clear";
  confidence: number | null;
  replays: number;
  speech_ms: number;
  analyzed_clips: number;
  processed_fragments: number;
  pending_clips: number;
}
interface VoiceSyntheticMonitor {
  enabled: boolean;
  show_worker_labels: boolean;
  model: {
    status: "ready" | "not_configured" | "unavailable" | "revision_mismatch";
    revision?: string;
    device?: "cpu" | "cuda" | null;
  };
  criteria: { min_replays: number; min_speech_ms: number };
  counts: {
    eligible_players: number;
    synthetic_players: number;
    processing_players: number;
    waiting_players: number;
  };
  players: VoiceSyntheticPlayerReport[];
}
interface GlobalVoiceprintRankingPlayer {
  steam_id: string;
  player_id: number | null;
  display_name: string | null;
  sample_count: number;
  duration_ms: number;
}
interface GlobalVoiceprintRanking {
  criteria: { min_replays: number; min_duration_ms: number };
  pairs: {
    score: number;
    source_channel: "normal" | "radio";
    left: GlobalVoiceprintRankingPlayer;
    right: GlobalVoiceprintRankingPlayer;
  }[];
}

const displayName = (player: PlayerSuggestion) =>
  player.forced_display_name || player.player_last_time_name;
const duration = (clip: VoiceClip) =>
  `${Math.max(0, (clip.end_ms - clip.start_ms) / 1000).toFixed(1)} с`;
function voiceprintLabel(status: VoiceProfile["voiceprint"]["status"]) {
  return status === "ready"
    ? "готов"
    : status === "collecting"
      ? "копит материал"
      : "модель не установлена";
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
function voiceprintLogClass(state: VoiceprintMonitorLogEntry["state"]) {
  return state === "ready"
    ? "text-emerald-400"
    : state === "low"
      ? "text-amber-300"
      : "text-red-400";
}
function voiceprintDuration(durationMs: number | null) {
  return durationMs === null ? "—" : `${(durationMs / 1000).toFixed(2)} с`;
}
function syntheticResultLabel(result: VoiceSyntheticPlayerReport["result"]) {
  return result === "synthetic"
    ? "Синтетический"
    : result === "clear"
      ? "Не обнаружен"
      : result === "processing"
        ? "Обрабатывается"
        : "Ожидает запуска";
}
function syntheticResultClass(result: VoiceSyntheticPlayerReport["result"]) {
  return result === "synthetic"
    ? "text-red-300"
    : result === "clear"
      ? "text-emerald-400"
      : result === "processing"
        ? "text-sky-300"
        : "text-amber-300";
}
function scannerElapsed(ms: number) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(totalSeconds / 60)}м ${String(totalSeconds % 60).padStart(2, "0")}с`;
}
function scannerBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
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
          {data.active ? (
            <Card className="border-sky-900/70 bg-sky-950/10">
              <CardHeader className="flex-row items-start justify-between space-y-0 pb-3">
                <div>
                  <CardTitle className="font-mono text-sm">
                    replay #{data.active.replay_id} · {data.active.replay_kind || "unknown"}
                  </CardTitle>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {data.enabled
                      ? "Обработка продолжается. Переключатель остановит только следующий claim."
                      : "Сканнер на паузе: текущий replay завершится, новые не запускаются."}
                  </p>
                </div>
                <Badge variant={data.enabled ? "default" : "secondary"}>
                  {data.enabled ? "в работе" : "paused"}
                </Badge>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center justify-between font-mono text-xs">
                  <span className="text-sky-300">{data.active.phase}</span>
                  <span>{data.active.percent}% · {scannerElapsed(data.active.elapsed_ms)}</span>
                </div>
                <div className="h-2 overflow-hidden rounded bg-zinc-800">
                  <div
                    className="h-full bg-sky-400 transition-[width] duration-500"
                    style={{ width: `${Math.min(100, Math.max(0, data.active.percent))}%` }}
                  />
                </div>
                <div className="grid gap-2 font-mono text-xs text-muted-foreground sm:grid-cols-4">
                  <span>{data.active.packets_read.toLocaleString()} packets</span>
                  <span>{scannerBytes(data.active.bytes_read)}</span>
                  <span>{data.active.speakers_completed}/{data.active.speakers_seen} SteamID</span>
                  <span className={data.active.heartbeat_age_ms === null || data.active.heartbeat_age_ms > 900000 ? "text-red-300" : ""}>
                    heartbeat {data.active.heartbeat_age_ms === null ? "нет" : `${Math.round(data.active.heartbeat_age_ms / 1000)}с`}
                  </span>
                </div>
                {(data.active.heartbeat_age_ms === null || data.active.heartbeat_age_ms > 900000) && (
                  <p className="font-mono text-xs text-red-300">
                    heartbeat устарел; recovery вернёт replay в очередь после таймаута.
                  </p>
                )}
              </CardContent>
            </Card>
          ) : !data.enabled ? (
            <Card className="border-amber-900/60 bg-amber-950/10">
              <CardContent className="p-4 text-sm text-amber-200">
                Сканнер на паузе. Новые реплеи не берутся в работу.
              </CardContent>
            </Card>
          ) : null}
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

function VoiceprintMonitorConsole({
  openProfile,
}: {
  openProfile: (player: { player_id: number; display_name: string }) => void;
}) {
  const queryClient = useQueryClient();
  const monitor = useQuery({
    queryKey: ["voiceprint-monitor"],
    queryFn: () =>
      api.get<VoiceprintMonitor>(
        "/api/v1/admin/voice-profiles/voiceprint-monitor",
      ),
    refetchInterval: 15_000,
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.patch<VoiceprintMonitor>(
        "/api/v1/admin/voice-profiles/voiceprint-monitor",
        { enabled },
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(["voiceprint-monitor"], data);
      toast.success(
        data.enabled
          ? "ONNX-обработка включена"
          : "ONNX-обработка приостановлена",
      );
    },
    onError: (error: ApiError) => toast.error(error.message),
  });
  const data = monitor.data;
  const modelReady = data?.model.status === "ready";
  return (
    <div className="space-y-4">
      <Card
        className={
          modelReady ? "border-sky-900/70 bg-sky-950/20" : "border-red-900/60"
        }
      >
        <CardHeader className="flex-row items-center justify-between gap-4 space-y-0">
          <div className="flex min-w-0 items-start gap-3">
            <div className="rounded-md border border-sky-900/70 bg-sky-950/60 p-2 text-sky-300">
              <Cpu className="size-5" />
            </div>
            <div>
              <CardTitle className="text-base">
                Обработка и embeddings
              </CardTitle>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                {data
                  ? `${data.model.algorithm} · ${data.model.dimensions} измерения · ${data.model.sample_rate / 1000} kHz`
                  : "Чтение состояния ONNX-процессора…"}
              </p>
            </div>
          </div>
          <Switch
            checked={data?.enabled ?? false}
            disabled={!data || toggle.isPending}
            onCheckedChange={(enabled) => toggle.mutate(enabled)}
            aria-label="Включить ONNX-обработку голосов"
          />
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Badge variant={modelReady ? "default" : "destructive"}>
            {modelReady ? "модель готова" : "модель недоступна"}
          </Badge>
          <Badge variant={data?.enabled ? "secondary" : "outline"}>
            {data?.enabled ? "обработка включена" : "обработка на паузе"}
          </Badge>
          <span className="text-sm text-muted-foreground">
            Пауза останавливает только ONNX backfill: новые nwdemo-клипы scanner
            продолжает собирать.
          </span>
          <Button
            className="ml-auto"
            size="sm"
            variant="ghost"
            onClick={() => monitor.refetch()}
            disabled={monitor.isFetching}
          >
            <RefreshCw
              className={monitor.isFetching ? "size-4 animate-spin" : "size-4"}
            />{" "}
            Обновить
          </Button>
        </CardContent>
      </Card>
      {monitor.isError && (
        <p className="text-sm text-destructive">
          Статус ONNX-обработки не загрузился.
        </p>
      )}
      {data && (
        <>
          <div className="grid overflow-hidden rounded-lg border bg-card sm:grid-cols-2 xl:grid-cols-4">
            {[
              [
                "fingerprints",
                "Векторы",
                data.counts.fingerprints,
                "text-sky-300",
              ],
              [
                "good",
                "Хорошие",
                data.counts.good_fingerprints,
                "text-emerald-400",
              ],
              [
                "signatures",
                "Готовые профили",
                data.counts.eligible_signatures,
                "text-violet-300",
              ],
              [
                "queue",
                "Очередь",
                data.counts.queued_replays,
                "text-amber-300",
              ],
            ].map(([key, label, value, color]) => (
              <div
                key={String(key)}
                className="border-b p-4 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0"
              >
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className={`mt-1 font-mono text-2xl ${color}`}>{value}</p>
              </div>
            ))}
          </div>
          <Card className="border-zinc-800 bg-zinc-950/70">
            <CardHeader className="flex-row items-center gap-2 space-y-0 pb-3">
              <CardTitle className="text-sm">Фильтр речи</CardTitle>
              <Badge variant="outline" className="font-mono text-[10px]">
                {data.speech_gate.version}
              </Badge>
              <span className="ml-auto text-xs text-muted-foreground">
                учитывается только подтверждённая речь
              </span>
            </CardHeader>
            <CardContent className="grid gap-3 pt-0 sm:grid-cols-2">
              {(["normal", "radio"] as const).map((channel) => {
                const stats = data.speech_gate[channel];
                return (
                  <div
                    key={channel}
                    className="rounded-md border border-zinc-800 px-3 py-2 font-mono text-xs"
                  >
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-zinc-100">
                        {channel === "normal" ? "normal" : "radio"}
                      </span>
                      <span className="text-zinc-500">
                        {voiceprintDuration(stats.speech_ms)} речи
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-zinc-500">
                      <span>
                        checked <b className="text-zinc-200">{stats.checked}</b>
                      </span>
                      <span>
                        accepted <b className="text-emerald-400">{stats.accepted}</b>
                      </span>
                      <span>
                        rejected <b className="text-red-300">{stats.rejected}</b>
                      </span>
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>
          <Card className="overflow-hidden border-zinc-800 bg-zinc-950 text-zinc-300">
            <CardHeader className="flex-row items-center gap-2 space-y-0 border-b border-zinc-800 py-3">
              <Terminal className="size-4 text-sky-400" />
              <CardTitle className="font-mono text-sm font-medium text-zinc-100">
                voiceprint.log
              </CardTitle>
              <span className="ml-auto font-mono text-xs text-zinc-500">
                последние {data.log.length}
              </span>
            </CardHeader>
            <CardContent className="max-h-[32rem] overflow-auto p-0 font-mono text-xs leading-6">
              {!data.log.length && (
                <p className="px-4 py-6 text-zinc-500">
                  Пока нет обработанных nwdemo-v2 voiceprint-ов.
                </p>
              )}
              {data.log.map((entry) => (
                <div
                  key={entry.id}
                  className="border-b border-zinc-900 px-4 py-2 last:border-0 hover:bg-zinc-900/60"
                >
                  <div className="flex flex-wrap gap-x-3 gap-y-1">
                    <span className="text-zinc-500">
                      {formatBrowserDate(entry.at) || "—"}
                    </span>
                    <span className={voiceprintLogClass(entry.state)}>
                      {entry.state.toUpperCase()}
                    </span>
                    <span className="text-zinc-200">
                      replay #{entry.replay_id}
                    </span>
                    <span className="text-zinc-500">
                      {entry.channel || "—"}
                    </span>
                    <span className="text-zinc-500">
                      {voiceprintDuration(entry.duration_ms)}
                    </span>
                    {entry.player && (
                      <button
                        type="button"
                        onClick={() => openProfile(entry.player!)}
                        className="text-sky-300 underline decoration-sky-800 underline-offset-2 hover:text-sky-100"
                      >
                        {entry.player.display_name}
                      </button>
                    )}
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

function SyntheticVoiceConsole({
  openProfile,
}: {
  openProfile: (player: { player_id: number; display_name: string }) => void;
}) {
  const queryClient = useQueryClient();
  const monitor = useQuery({
    queryKey: ["voice-synthetic-monitor"],
    queryFn: () =>
      api.get<VoiceSyntheticMonitor>(
        "/api/v1/admin/voice-profiles/voice-synthetic-monitor",
      ),
    refetchInterval: 15_000,
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.patch<VoiceSyntheticMonitor>(
        "/api/v1/admin/voice-profiles/voice-synthetic-monitor",
        { enabled },
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(["voice-synthetic-monitor"], data);
      toast.success(
        data.enabled ? "Анализ синтетического голоса включён" : "Анализ приостановлен",
      );
    },
    onError: (error: ApiError) => toast.error(error.message),
  });
  const labelVisibility = useMutation({
    mutationFn: (show_worker_labels: boolean) =>
      api.patch<VoiceSyntheticMonitor>(
        "/api/v1/admin/voice-profiles/voice-synthetic-monitor",
        { show_worker_labels },
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(["voice-synthetic-monitor"], data);
      toast.success(data.show_worker_labels ? "Метки воркера показываются" : "Метки воркера скрыты");
    },
    onError: (error: ApiError) => toast.error(error.message),
  });
  const data = monitor.data;
  const modelReady = data?.model.status === "ready";

  return (
    <div className="space-y-4">
      <Card className={modelReady ? "border-sky-900/70 bg-sky-950/10" : "border-amber-900/60"}>
        <CardHeader className="flex-row items-center justify-between gap-4 space-y-0">
          <div className="min-w-0">
            <CardTitle className="text-base">Анализ синтетического голоса</CardTitle>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Игрок попадает в отчёт после {data?.criteria.min_replays ?? 3} реплеев и {voiceprintDuration(data?.criteria.min_speech_ms ?? 15_000)} подтверждённой речи.
            </p>
          </div>
          <Switch
            checked={data?.enabled ?? false}
            disabled={!data || !modelReady || toggle.isPending}
            onCheckedChange={(enabled) => toggle.mutate(enabled)}
            aria-label="Включить анализ синтетического голоса"
          />
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Badge variant={modelReady ? "default" : "destructive"}>
            {modelReady
              ? "удалённая модель готова"
              : data?.model.status === "not_configured"
                ? "сервис не настроен"
                : data?.model.status === "revision_mismatch"
                  ? "версия модели не совпадает"
                  : "сервис недоступен"}
          </Badge>
          <Badge variant={data?.enabled ? "secondary" : "outline"}>
            {data?.enabled ? "анализ включён" : "анализ выключен"}
          </Badge>
          <span className="text-sm text-muted-foreground">
            {modelReady
              ? `Положительный вывод автоматически ставит игроку метку «Модуляция голоса». Устройство: ${data.model.device === "cuda" ? "GPU" : "CPU"}.`
              : "Нужен доступный защищённый сервис с закреплённой версией модели."}
          </span>
          <Button
            className="ml-auto"
            size="sm"
            variant="ghost"
            onClick={() => monitor.refetch()}
            disabled={monitor.isFetching}
          >
            <RefreshCw className={monitor.isFetching ? "size-4 animate-spin" : "size-4"} />{" "}
            Обновить
          </Button>
          <label className="flex basis-full items-center gap-2 border-t border-border/70 pt-3 text-sm text-muted-foreground">
            <Checkbox
              checked={data?.show_worker_labels ?? true}
              disabled={!data || labelVisibility.isPending}
              onCheckedChange={(checked) => labelVisibility.mutate(checked === true)}
              aria-label="Показывать метки, поставленные воркером"
            />
            <span>Показывать метки, поставленные воркером</span>
          </label>
        </CardContent>
      </Card>
      {monitor.isError && (
        <p className="text-sm text-destructive">Состояние анализа не загрузилось.</p>
      )}
      {data && (
        <>
          <div className="grid overflow-hidden rounded-lg border bg-card sm:grid-cols-2 xl:grid-cols-4">
            {[
              ["eligible", "В отчёте", data.counts.eligible_players, "text-sky-300"],
              ["synthetic", "Синтетический голос", data.counts.synthetic_players, "text-red-300"],
              ["processing", "Обрабатываются", data.counts.processing_players, "text-emerald-400"],
              ["waiting", "Ожидают анализа", data.counts.waiting_players, "text-amber-300"],
            ].map(([key, label, value, color]) => (
              <div key={String(key)} className="border-b p-4 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className={`mt-1 font-mono text-2xl ${color}`}>{value}</p>
              </div>
            ))}
          </div>
          <Card className="overflow-hidden">
            <CardHeader className="border-b border-border/70 py-3">
              <CardTitle className="text-sm">Отчёт по игрокам</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground sm:hidden">
                Прокрутите таблицу по горизонтали, чтобы увидеть оценку.
              </p>
            </CardHeader>
            <CardContent className="p-0">
              {monitor.isLoading ? (
                <p className="p-4 text-sm text-muted-foreground">Загружаем результаты…</p>
              ) : !data.players.length ? (
                <p className="p-4 text-sm text-muted-foreground">
                  Пока нет игроков с {data.criteria.min_replays} реплеями и {voiceprintDuration(data.criteria.min_speech_ms)} подтверждённой речи.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Игрок</TableHead>
                        <TableHead>Результат</TableHead>
                        <TableHead>Материал</TableHead>
                        <TableHead>Обработано</TableHead>
                        <TableHead className="text-right">Оценка</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.players.map((player) => (
                        <TableRow key={player.steam_id}>
                          <TableCell>
                            <button
                              type="button"
                              onClick={() => openProfile({ player_id: player.player_id, display_name: player.display_name })}
                              className="max-w-64 text-left font-medium text-foreground underline decoration-transparent underline-offset-2 hover:text-sky-300 hover:decoration-sky-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            >
                              <span className="block truncate">{player.display_name}</span>
                              <span className="block font-mono text-xs text-muted-foreground">{player.steam_id}</span>
                            </button>
                          </TableCell>
                          <TableCell className={syntheticResultClass(player.result)}>
                            {syntheticResultLabel(player.result)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-muted-foreground">
                            {player.replays} репл. · {voiceprintDuration(player.speech_ms)} речи
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-muted-foreground">
                            {player.analyzed_clips} клип. · {player.processed_fragments} фрагм.
                            {player.pending_clips > 0 && ` · ${player.pending_clips} в очереди`}
                          </TableCell>
                          <TableCell className="text-right font-mono tabular-nums">
                            {player.confidence === null ? "—" : player.confidence.toFixed(3)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function GlobalVoiceprintRanking({
  openProfile,
}: {
  openProfile: (player: { player_id: number; display_name: string }) => void;
}) {
  const ranking = useQuery({
    queryKey: ["global-voiceprint-ranking"],
    queryFn: () =>
      api.get<GlobalVoiceprintRanking>(
        "/api/v1/admin/voice-profiles/global-voiceprint-ranking",
      ),
    refetchInterval: 15_000,
  });
  const data = ranking.data;
  const openPlayer = (player: GlobalVoiceprintRankingPlayer) => {
    if (player.player_id !== null) {
      openProfile({
        player_id: player.player_id,
        display_name: player.display_name || player.steam_id,
      });
    }
  };
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>Общий рейтинг похожести</CardTitle>
        <p className="text-sm font-normal text-muted-foreground">
          Кандидаты для ручной проверки: каналы не смешиваются, а совпадение —
          не приговор.
        </p>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {ranking.isLoading && (
          <p className="text-muted-foreground">Считаем пары…</p>
        )}
        {ranking.isError && (
          <p className="text-destructive">Общий рейтинг не загрузился.</p>
        )}
        {data && !data.pairs.length && (
          <p className="text-muted-foreground">
            Пока нет подходящих пар: каждому профилю нужны {data.criteria.min_replays} good-реплея и {voiceprintDuration(data.criteria.min_duration_ms)} материала одного канала.
          </p>
        )}
        {data?.pairs.map((pair) => (
          <div
            key={`${pair.left.steam_id}-${pair.right.steam_id}`}
            className="grid gap-2 rounded-md border p-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-center"
          >
            <button
              type="button"
              disabled={pair.left.player_id === null}
              onClick={() => openPlayer(pair.left)}
              className={
                "min-w-0 text-left disabled:cursor-default" +
                (pair.left.player_id !== null ? " hover:text-sky-300" : "")
              }
            >
              <span className="block truncate font-medium">{pair.left.display_name || pair.left.steam_id}</span>
              <span className="block font-mono text-xs text-muted-foreground">{pair.left.steam_id}</span>
              <span className="block text-xs text-muted-foreground">
                {pair.left.sample_count} репл. · {voiceprintDuration(pair.left.duration_ms)}
              </span>
            </button>
            <div className="flex items-center justify-center gap-1 text-center text-xs text-muted-foreground">
              <span className="font-medium text-sky-300">{(pair.score * 100).toFixed(1)}%</span>
              <span>· {pair.source_channel}</span>
            </div>
            <button
              type="button"
              disabled={pair.right.player_id === null}
              onClick={() => openPlayer(pair.right)}
              className={
                "min-w-0 text-left md:text-right disabled:cursor-default" +
                (pair.right.player_id !== null ? " hover:text-sky-300" : "")
              }
            >
              <span className="block truncate font-medium">{pair.right.display_name || pair.right.steam_id}</span>
              <span className="block font-mono text-xs text-muted-foreground">{pair.right.steam_id}</span>
              <span className="block text-xs text-muted-foreground">
                {pair.right.sample_count} репл. · {voiceprintDuration(pair.right.duration_ms)}
              </span>
            </button>
          </div>
        ))}
      </CardContent>
    </Card>
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
        <TabsList variant="line" className="mb-5 max-w-full justify-start overflow-x-auto">
          <TabsTrigger value="profiles">Профили игроков</TabsTrigger>
          <TabsTrigger value="scanner">Сканнер и журнал</TabsTrigger>
          <TabsTrigger value="voiceprint">Обработка и embeddings</TabsTrigger>
          <TabsTrigger value="synthetic">Синтетический голос</TabsTrigger>
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
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle>Похожие голоса</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {!data.voiceprint.candidates.length ? (
                    <p className="text-muted-foreground">
                      Рейтинг появится после 3 независимых реплеев и 15 секунд
                      материала на каждом профиле.
                    </p>
                  ) : (
                    data.voiceprint.candidates.map((candidate) => (
                      <button
                        key={`${candidate.steam_id}-${candidate.source_channel}`}
                        type="button"
                        disabled={candidate.player_id === null}
                        onClick={() =>
                          candidate.player_id !== null &&
                          openProfile({
                            player_id: candidate.player_id,
                            display_name:
                              candidate.display_name || candidate.steam_id,
                          })
                        }
                        className="flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left enabled:hover:bg-accent disabled:cursor-default"
                      >
                        <span>
                          {candidate.display_name || candidate.steam_id}
                          <span className="ml-2 font-mono text-xs text-muted-foreground">
                            {candidate.steam_id}
                          </span>
                        </span>
                        <span className="text-muted-foreground">
                          {(candidate.score * 100).toFixed(1)}% ·{" "}
                          {candidate.source_channel} · {candidate.sample_count}{" "}
                          репл.
                        </span>
                      </button>
                    ))
                  )}
                </CardContent>
              </Card>
            </>
          )}
          <GlobalVoiceprintRanking openProfile={openProfile} />
        </TabsContent>
        <TabsContent value="scanner">
          <ScannerConsole openProfile={openProfile} />
        </TabsContent>
        <TabsContent value="voiceprint">
          <VoiceprintMonitorConsole openProfile={openProfile} />
        </TabsContent>
        <TabsContent value="synthetic">
          <SyntheticVoiceConsole openProfile={openProfile} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
