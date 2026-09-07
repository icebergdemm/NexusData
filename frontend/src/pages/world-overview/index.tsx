import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Globe2, Users2, Clock, Wind, Droplets } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PageHeader } from "@/components/layout/PageHeader"
import { PlayerAvatar } from "@/components/layout/PlayerAvatar"
import { ApiError } from "@/lib/api"
import { worldStateApi, type WorldCharacter } from "./api"
import { WorldCanvas } from "./WorldCanvas"

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return "-"
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, "0")}`
}

const TEAM_LABELS: Record<string, string> = {
  Team1: "Команда 1",
  Team2: "Команда 2",
  None: "Без команды",
  FriendlyNPC: "Дружественный NPC",
}

function CharacterRow({ c, selected, onSelect }: { c: WorldCharacter; selected: boolean; onSelect: () => void }) {
  return (
    <button
      onClick={onSelect}
      className={`flex w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-accent ${selected ? "border-primary bg-accent" : "border-transparent"}`}
    >
      <PlayerAvatar url={null} name={c.name} size={22} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={`truncate font-medium ${c.dead ? "text-muted-foreground line-through" : ""}`}>{c.name}</span>
          {c.ragdoll && !c.dead && <Badge variant="outline" className="text-[9px]">оглушён</Badge>}
        </div>
        <div className="text-[11px] text-muted-foreground">{c.job ?? c.species} · {TEAM_LABELS[c.team] ?? c.team}</div>
      </div>
      <div className="shrink-0 text-right text-[11px]">
        <div className={c.hp < 30 ? "text-destructive" : "text-muted-foreground"}>{Math.round(c.hp)} HP</div>
        {c.water && <Droplets className="ml-auto size-3 text-info" />}
      </div>
    </button>
  )
}

export function WorldOverviewPage() {
  const [selectedRoundId, setSelectedRoundId] = useState<string | null>(null)
  const [selectedCharacterId, setSelectedCharacterId] = useState<number | null>(null)
  const [selectedServer, setSelectedServer] = useState<string | null>(() => {
    try { return localStorage.getItem("world-overview:server") || null } catch { return null }
  })

  const { data: serversData } = useQuery({
    queryKey: ["world-servers"],
    queryFn: () => worldStateApi.listServers(),
    refetchInterval: 10000,
    retry: false,
  })

  const { data: roundsData, isLoading: roundsLoading, isError: roundsError, error: roundsErrorObj } = useQuery({
    queryKey: ["world-rounds", selectedServer],
    queryFn: () => worldStateApi.listRounds(selectedServer),
    refetchInterval: 5000,
    retry: false,
  })

  useEffect(() => {
    if (!roundsData) return
    const stillListed = roundsData.rounds.some((r) => r.round_id === selectedRoundId)
    if (!stillListed) setSelectedRoundId(roundsData.rounds[0]?.round_id ?? null)
  }, [roundsData, selectedRoundId])

  const { data: geometry } = useQuery({
    queryKey: ["world-geometry", selectedRoundId],
    // Static per round_id — fetched once, never refetched on its own.
    queryFn: () => worldStateApi.getGeometry(selectedRoundId as string),
    enabled: !!selectedRoundId,
    staleTime: Infinity,
  })

  const { data: state } = useQuery({
    queryKey: ["world-state", selectedRoundId],
    queryFn: () => worldStateApi.getState(selectedRoundId as string),
    enabled: !!selectedRoundId,
    // The game plugin publishes live state at 3 Hz. WorldCanvas interpolates
    // positions between responses, so the map remains smooth without asking
    // the game server for a higher-rate JSON stream.
    refetchInterval: 333,
  })

  useEffect(() => { setSelectedCharacterId(null) }, [selectedRoundId])

  useEffect(() => {
    try {
      if (selectedServer) localStorage.setItem("world-overview:server", selectedServer)
      else localStorage.removeItem("world-overview:server")
    } catch { /* ignore */ }
  }, [selectedServer])

  // If selected server disappeared (no active rounds), reset to "all"
  useEffect(() => {
    if (!serversData) return
    if (selectedServer && !serversData.servers.includes(selectedServer)) {
      // keep selection — server may have no active rounds right now, don't force-reset
    }
  }, [serversData, selectedServer])

  const handleServerChange = (v: string) => {
    const next = v === "__all__" ? null : v
    setSelectedServer(next)
    setSelectedRoundId(null)
  }

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col">
      <PageHeader
        title="Обзор мира"
        description={selectedServer ? `Просмотр раунда — ${selectedServer}` : "Просмотр раунда — все серверы"}
        icon={Globe2}
        actions={
          <div className="flex gap-2">
            <Select value={selectedServer ?? "__all__"} onValueChange={handleServerChange}>
              <SelectTrigger className="w-36"><SelectValue placeholder="Сервер" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">Все серверы</SelectItem>
                {(serversData?.servers ?? []).map((s) => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
                {/* fallback: if rounds contain a server not yet in /servers (race), show it too */}
                {roundsData?.rounds
                  .map((r) => r.server_name?.toUpperCase())
                  .filter((s): s is string => !!s)
                  .filter((s, i, a) => a.indexOf(s) === i && !(serversData?.servers ?? []).includes(s))
                  .map((s) => (
                    <SelectItem key={`fb-${s}`} value={s}>{s}</SelectItem>
                  ))}
              </SelectContent>
            </Select>
            {!!roundsData?.rounds.length && (
              <Select value={selectedRoundId ?? undefined} onValueChange={setSelectedRoundId}>
                <SelectTrigger className="w-64"><SelectValue placeholder="Выберите раунд" /></SelectTrigger>
                <SelectContent>
                  {roundsData.rounds.map((r) => (
                    <SelectItem key={r.round_id} value={r.round_id}>
                      {r.server_name ?? "?"} — {r.level_name ?? "?"} ({fmtDuration(r.t)})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
        }
      />

      {roundsLoading ? (
        <Skeleton className="flex-1" />
      ) : roundsError ? (
        <Card className="flex-1"><CardContent className="flex h-full flex-col items-center justify-center gap-1 py-12 text-center text-sm">
          <span className="font-medium text-destructive">Не удалось загрузить раунды</span>
          <span className="text-muted-foreground">
            {roundsErrorObj instanceof ApiError && roundsErrorObj.status === 403
              ? "Недостаточно прав для просмотра этой страницы."
              : roundsErrorObj instanceof ApiError ? roundsErrorObj.message : "Проверьте соединение и попробуйте снова."}
          </span>
        </CardContent></Card>
      ) : !roundsData?.rounds.length ? (
        <Card className="flex-1"><CardContent className="flex h-full items-center justify-center py-12 text-center text-sm text-muted-foreground">
          {selectedServer ? `На сервере ${selectedServer} сейчас нет активных раундов` : "Похоже раунд не начался :("}
        </CardContent></Card>
      ) : !geometry || !state ? (
        <Skeleton className="flex-1" />
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[1fr_320px]">
          <WorldCanvas
            geometry={geometry}
            state={state}
            selectedCharacterId={selectedCharacterId}
            onSelectCharacter={setSelectedCharacterId}
          />

          <div className="flex min-h-0 flex-col gap-3">
            <Card className="shrink-0">
              <CardContent className="space-y-1.5 py-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{geometry.level_name ?? "-"}</span>
                  <Badge variant={state.is_active ? "secondary" : "destructive"}>{state.is_active ? "Активен" : "Нет данных"}</Badge>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Clock className="size-3.5" /> {fmtDuration(state.t)}
                </div>
                <div className="text-xs text-muted-foreground">{geometry.sub_name} · {geometry.server_name}</div>
              </CardContent>
            </Card>

            <div className="min-h-0 flex-1 overflow-y-auto">
              <div className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                <Users2 className="size-3.5" /> Персонажи ({state.characters.length})
              </div>
              <div className="flex flex-col gap-1.5 pr-0.5">
                {state.characters.map((c) => (
                  <CharacterRow key={c.id} c={c} selected={c.id === selectedCharacterId} onSelect={() => setSelectedCharacterId(c.id)} />
                ))}
                {!state.characters.length && <p className="py-4 text-center text-xs text-muted-foreground">Никого нет</p>}
              </div>
            </div>

            <p className="flex shrink-0 items-center gap-1 px-1 text-[10px] text-muted-foreground">
              <Wind className="size-3" /> Обновляется 3 раза/с · seq {state.seq ?? "-"}
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
