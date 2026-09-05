import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { Filter, MessageSquare, RotateCcw, Search, Volume2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { buildCommunicationQuery, communicationChannelLabels } from "./communications-utils"

interface Recipient {
  id: number
  player_steamid: string | null
  player_id: number | null
  player_name: string | null
  delivered_text: string | null
  server_queued: boolean
  delivery_allowed: boolean
  channel: string | null
  is_radio: boolean
  is_dead: boolean
  distance_factor: number | null
  packet_count: number
  first_observed_at: string | null
  last_observed_at: string | null
}

interface Communication {
  id: number
  source: "chat" | "voice"
  channel: string
  server_name: string
  server_session_id: string
  round_id: number | null
  round_finished: boolean
  player_id: number | null
  player_name: string | null
  role: string | null
  submarine_name: string | null
  hull_name: string | null
  position_x: number | null
  position_y: number | null
  original_text: string | null
  transcript_text: string | null
  asr_status: string
  asr_confidence: number | null
  asr_duration_ms: number | null
  asr_error: string | null
  occurred_at: string
  recipient_count: number
  recipients?: Recipient[]
}

interface CommunicationsResponse {
  events: Communication[]
  total: number
  page: number
  page_size: number
  total_pages: number
  has_next: boolean
}

function SourceIcon({ source }: { source: Communication["source"] }) {
  if (source === "voice") return <Volume2 className="size-3.5" />
  return <MessageSquare className="size-3.5" />
}

export function CommunicationsView() {
  const [search, setSearch] = useState("")
  const [source, setSource] = useState("all")
  const [channel, setChannel] = useState("all")
  const [server, setServer] = useState("all")
  const [asrStatus, setAsrStatus] = useState("all")
  const [lobby, setLobby] = useState(false)
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [applied, setApplied] = useState({ search: "", source: "all", channel: "all", server: "all", asrStatus: "all", lobby: false })

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["player-communications", applied, page],
    queryFn: () => {
      return api.get<CommunicationsResponse>(`/api/v1/admin/communications?${buildCommunicationQuery(applied, page)}`)
    },
  })

  const detail = useQuery({
    queryKey: ["player-communication", selectedId],
    queryFn: () => api.get<Communication>(`/api/v1/admin/communications/${selectedId}`),
    enabled: selectedId != null,
  })

  const apply = () => {
    setPage(1)
    setApplied({ search: search.trim(), source, channel, server, asrStatus, lobby })
  }

  const reset = () => {
    setSearch("")
    setSource("all")
    setChannel("all")
    setServer("all")
    setAsrStatus("all")
    setLobby(false)
    setPage(1)
    setApplied({ search: "", source: "all", channel: "all", server: "all", asrStatus: "all", lobby: false })
  }

  const appliedCount = Number(applied.search.length > 0)
    + Number(applied.source !== "all")
    + Number(applied.channel !== "all")
    + Number(applied.server !== "all")
    + Number(applied.asrStatus !== "all")
    + Number(applied.lobby)

  return (
    <div className="space-y-4">
      <form onSubmit={(event) => { event.preventDefault(); apply() }}>
        <Card className="mb-4 gap-0 py-0">
          <CardHeader className="border-b py-4">
            <CardTitle className="flex items-center gap-2 text-base"><Filter className="size-4 text-primary" />Поиск и фильтры</CardTitle>
            <CardDescription>Найдите текст чата, расшифровку голоса или игрока и сузьте выдачу.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 py-4">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-12">
              <div className="space-y-1.5 xl:col-span-4"><Label>Текст, транскрипция или игрок</Label><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="найти сообщение…" /></div>
              <div className="space-y-1.5 xl:col-span-2"><Label>Источник</Label><Select value={source} onValueChange={setSource}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все</SelectItem><SelectItem value="chat">Чат</SelectItem><SelectItem value="voice">Голос</SelectItem></SelectContent></Select></div>
              <div className="space-y-1.5 xl:col-span-2"><Label>Канал</Label><Select value={channel} onValueChange={setChannel}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все каналы</SelectItem>{Object.entries(communicationChannelLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-1.5 xl:col-span-2"><Label>Сервер</Label><Select value={server} onValueChange={setServer}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все серверы</SelectItem><SelectItem value="ASPER1">ASPER1</SelectItem><SelectItem value="ASPER2">ASPER2</SelectItem></SelectContent></Select></div>
              <div className="space-y-1.5 xl:col-span-2"><Label>ASR</Label><Select value={asrStatus} onValueChange={setAsrStatus}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Любой статус</SelectItem><SelectItem value="pending">В очереди</SelectItem><SelectItem value="recognized">Распознано</SelectItem><SelectItem value="failed">Ошибка</SelectItem><SelectItem value="not_applicable">Не применимо</SelectItem></SelectContent></Select></div>
            </div>
            <div className="flex flex-wrap items-center gap-3 border-t pt-3">
              <label className="flex items-center gap-2 text-xs text-muted-foreground"><Checkbox checked={lobby} onCheckedChange={(value) => setLobby(value === true)} />Только lobby</label>
              {appliedCount > 0 && <Badge variant="outline">Фильтров: {appliedCount}</Badge>}
              <div className="ml-auto flex gap-2"><Button type="button" variant="ghost" size="sm" onClick={reset}><RotateCcw className="size-4" />Сбросить</Button><Button type="submit" disabled={isFetching}><Search className="size-4" />Применить</Button></div>
            </div>
          </CardContent>
        </Card>
      </form>

      <Card className="gap-0 py-0">
        <CardHeader className="border-b py-3"><CardTitle className="flex items-center gap-2 text-sm">Результаты <Badge variant="secondary">{data?.total?.toLocaleString("ru-RU") ?? 0} сообщений</Badge></CardTitle><CardDescription className="ml-auto text-right">Серверная доставка, не телепатия клиента</CardDescription></CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table className="min-w-[1050px]">
              <TableHeader className="bg-muted/35"><TableRow><TableHead>Время</TableHead><TableHead>Источник</TableHead><TableHead>Канал</TableHead><TableHead>Игрок</TableHead><TableHead>Сервер / раунд</TableHead><TableHead>Отсек</TableHead><TableHead>Текст</TableHead><TableHead>Получатели</TableHead></TableRow></TableHeader>
              <TableBody>
                {isLoading ? <TableRow><TableCell colSpan={8} className="py-12 text-center">Загрузка…</TableCell></TableRow> : null}
                {!isLoading && !(data?.events.length) ? <TableRow><TableCell colSpan={8} className="py-12 text-center text-muted-foreground">Сообщений не найдено</TableCell></TableRow> : null}
                {data?.events.map((event) => {
                  const text = event.source === "voice" ? event.transcript_text : event.original_text
                  return <TableRow key={event.id} className="cursor-pointer align-top" onClick={() => setSelectedId(selectedId === event.id ? null : event.id)}>
                    <TableCell className="whitespace-nowrap font-mono text-[11px] text-muted-foreground">{formatBrowserDate(event.occurred_at)}</TableCell>
                    <TableCell><Badge variant="outline" className="flex w-fit items-center gap-1"><SourceIcon source={event.source} /> {event.source === "voice" ? "голос" : "чат"}</Badge></TableCell>
                    <TableCell><Badge variant="secondary">{communicationChannelLabels[event.channel] ?? event.channel}</Badge></TableCell>
                    <TableCell>{event.player_id ? <PlayerHoverLink playerId={event.player_id}>{event.player_name ?? "Без имени"}</PlayerHoverLink> : (event.player_name ?? "-")}</TableCell>
                    <TableCell><div><Badge variant="outline">{event.server_name}</Badge>{event.round_id && event.round_finished ? <Link className="ml-2 text-xs text-primary hover:underline" to={`/replays/rounds/${event.round_id}`} onClick={(e) => e.stopPropagation()}>раунд #{event.round_id}</Link> : event.round_id ? <span className="ml-2 text-xs text-muted-foreground">раунд #{event.round_id} · идёт</span> : <span className="ml-2 text-xs text-muted-foreground">lobby</span>}</div></TableCell>
                    <TableCell className="text-xs">{event.hull_name ?? "-"}</TableCell>
                    <TableCell className="max-w-[360px] whitespace-normal break-words text-xs">{text ?? <span className="text-muted-foreground">{event.asr_status}</span>}</TableCell>
                    <TableCell className="text-xs">{event.recipient_count}</TableCell>
                  </TableRow>
                })}
                {selectedId != null && detail.data && <TableRow><TableCell colSpan={8} className="bg-muted/20 p-4"><div className="grid gap-4 lg:grid-cols-2"><div className="space-y-2 text-sm"><div className="font-semibold">Детали сообщения</div>{detail.data.original_text && <div><span className="text-muted-foreground">Оригинал:</span> {detail.data.original_text}</div>}{detail.data.transcript_text && <div><span className="text-muted-foreground">Расшифровка:</span> {detail.data.transcript_text} ({detail.data.asr_confidence != null ? `${Math.round(detail.data.asr_confidence * 100)}%` : "без confidence"})</div>}<div className="text-xs text-muted-foreground">{detail.data.role ?? "роль неизвестна"} · {detail.data.submarine_name ?? "подлодка неизвестна"} · {detail.data.position_x != null ? `${Math.round(detail.data.position_x)}, ${Math.round(detail.data.position_y ?? 0)}` : "координаты неизвестны"}</div>{detail.data.asr_error && <div className="text-xs text-destructive">ASR: {detail.data.asr_error}</div>}</div><div><div className="mb-2 text-sm font-semibold">Получатели</div><div className="space-y-1 text-xs">{detail.data.recipients?.map((recipient) => <div key={recipient.id} className="flex flex-wrap gap-2 border-b pb-1"><span>{recipient.player_id ? <PlayerHoverLink playerId={recipient.player_id}>{recipient.player_name ?? "Без имени"}</PlayerHoverLink> : recipient.player_name ?? "-"}</span>{recipient.delivered_text && <span className="text-muted-foreground">«{recipient.delivered_text}»</span>}{recipient.is_radio && <Badge variant="outline">radio</Badge>}{recipient.is_dead && <Badge variant="outline">dead</Badge>}{recipient.distance_factor != null && <span className="text-muted-foreground">factor {recipient.distance_factor.toFixed(2)}</span>}<span className="text-muted-foreground">пакетов: {recipient.packet_count}</span>{recipient.first_observed_at && recipient.last_observed_at && <span className="text-muted-foreground">{formatBrowserDate(recipient.first_observed_at)} — {formatBrowserDate(recipient.last_observed_at)}</span>}<span className={recipient.delivery_allowed ? "text-emerald-600" : "text-destructive"}>{recipient.delivery_allowed ? "допущен" : "не допущен"}</span></div>)}</div></div></div></TableCell></TableRow>}
              </TableBody>
            </Table>
          </div>
          <div className="flex items-center justify-between border-t px-4 py-3 text-xs text-muted-foreground"><span>Страница {data?.page ?? page} из {data?.total_pages ?? 1}</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1 || isFetching} onClick={() => setPage((value) => value - 1)}>Назад</Button><Button variant="outline" size="sm" disabled={!data?.has_next || isFetching} onClick={() => setPage((value) => value + 1)}>Дальше</Button></div></div>
        </CardContent>
      </Card>
    </div>
  )
}
