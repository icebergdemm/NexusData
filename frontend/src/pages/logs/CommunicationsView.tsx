import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { Copy, ExternalLink, Filter, MessageSquare, RotateCcw, Search, Volume2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PlayerHoverLink } from "@/components/layout/PlayerHoverTooltip"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import {
  buildCommunicationQuery,
  communicationChannelLabels,
  communicationContextLabels,
  recipientTextVariant,
} from "./communications-utils"

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
  event_uid: string
  source: "chat" | "voice"
  channel: string
  server_name: string
  server_session_id: string
  round_token: string | null
  round_context: "round" | "lobby" | "unmatched"
  round_id: number | null
  round_finished: boolean
  player_steamid: string | null
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
  asr_error: string | null
  occurred_at: string
  recipient_count: number
  recipients?: Recipient[]
}

interface CommunicationsResponse {
  events: Communication[]
  total: number
  page: number
  total_pages: number
  has_next: boolean
}

interface CommunicationFilters {
  search: string
  source: string
  channel: string
  server: string
  asrStatus: string
  context: string
}

const EMPTY_FILTERS: CommunicationFilters = {
  search: "",
  source: "all",
  channel: "all",
  server: "all",
  asrStatus: "all",
  context: "all",
}

function filtersFromUrl(params: URLSearchParams): CommunicationFilters {
  return {
    search: params.get("comm_search") ?? "",
    source: params.get("comm_source") ?? "all",
    channel: params.get("comm_channel") ?? "all",
    server: params.get("comm_server") ?? "all",
    asrStatus: params.get("comm_asr") ?? "all",
    context: params.get("comm_context") ?? "all",
  }
}

function SourceIcon({ source }: { source: Communication["source"] }) {
  return source === "voice" ? <Volume2 className="size-3.5" /> : <MessageSquare className="size-3.5" />
}

function sourceLabel(source: Communication["source"]) {
  return source === "voice" ? "голос" : "чат"
}

function contextFor(event: Communication) {
  if (event.round_context) return event.round_context
  if (event.round_id != null) return "round"
  if (event.round_token) return "unmatched"
  return "lobby"
}

function contextLabel(event: Communication) {
  return communicationContextLabels[contextFor(event)] ?? "контекст неизвестен"
}

function CopyButton({ value, label }: { value: string | null | undefined; label: string }) {
  if (!value) return null
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-7"
      title={`Скопировать ${label}`}
      aria-label={`Скопировать ${label}`}
      onClick={() => navigator.clipboard?.writeText(value)}
    >
      <Copy className="size-3.5" />
    </Button>
  )
}

function PlayerName({ playerId, playerName, stopPropagation = false }: {
  playerId: number | null
  playerName: string | null
  stopPropagation?: boolean
}) {
  const content = playerId
    ? <PlayerHoverLink playerId={playerId}>{playerName ?? "Без имени"}</PlayerHoverLink>
    : <span>{playerName ?? "-"}</span>
  return stopPropagation ? <span onClick={(event) => event.stopPropagation()}>{content}</span> : content
}

function RoundContext({ event }: { event: Communication }) {
  const context = contextFor(event)
  return (
    <div className="space-y-1">
      <Badge variant="outline">{event.server_name}</Badge>
      {context === "round" && event.round_id != null && event.round_finished ? (
        <Link className="block text-xs text-primary hover:underline" to={`/replays/rounds/${event.round_id}`} onClick={(mouseEvent) => mouseEvent.stopPropagation()}>
          раунд #{event.round_id}
        </Link>
      ) : context === "round" ? (
        <span className="block text-xs text-muted-foreground">раунд · идёт</span>
      ) : context === "unmatched" ? (
        <span className="block text-xs text-warning" title={event.round_token ?? undefined}>раунд не сопоставлен</span>
      ) : (
        <span className="block text-xs text-muted-foreground">лобби · сессия {event.server_session_id.slice(0, 8)}</span>
      )}
    </div>
  )
}

function AsrStatus({ event }: { event: Communication }) {
  if (event.source !== "voice") return null
  const labels: Record<string, string> = { pending: "в очереди", recognized: "распознано", failed: "ошибка", not_applicable: "не применимо" }
  return <Badge variant={event.asr_status === "failed" ? "destructive" : "secondary"}>{labels[event.asr_status] ?? event.asr_status}</Badge>
}

function CommunicationDrawer({ event, communicationId, loading, onClose }: { event: Communication | undefined; communicationId: number | null; loading: boolean; onClose: () => void }) {
  return (
    <Sheet open={communicationId != null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="flex w-[min(96vw,38rem)] flex-col gap-0 p-0 sm:max-w-none">
        {!event ? <div className="p-5 text-sm text-muted-foreground">{loading ? "Загрузка деталей…" : "Детали записи недоступны"}</div> : (
          <>
            <SheetHeader className="border-b px-5 py-4 text-left">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="gap-1"><SourceIcon source={event.source} />{sourceLabel(event.source)}</Badge>
                <Badge variant="secondary">{communicationChannelLabels[event.channel] ?? event.channel}</Badge>
                {event.source === "voice" && <AsrStatus event={event} />}
              </div>
              <SheetTitle className="text-base"><PlayerName playerId={event.player_id} playerName={event.player_name} /></SheetTitle>
              <SheetDescription>{formatBrowserDate(event.occurred_at)} · {event.server_name} · {contextLabel(event)}</SheetDescription>
            </SheetHeader>
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Сообщение</h3>
                {event.original_text && <div className="rounded-md border bg-muted/30 p-3 text-sm"><div className="mb-1 text-xs text-muted-foreground">Оригинал</div>{event.original_text}</div>}
                {event.transcript_text && <div className="rounded-md border bg-muted/30 p-3 text-sm"><div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">Расшифровка <AsrStatus event={event} /></div>{event.transcript_text}{event.asr_confidence != null && <span className="ml-2 text-xs text-muted-foreground">({Math.round(event.asr_confidence * 100)}%)</span>}</div>}
                {!event.original_text && !event.transcript_text && <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">Текст недоступен · статус: {event.asr_status}</div>}
                {event.asr_error && <div className="text-xs text-destructive">ASR: {event.asr_error}</div>}
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Контекст</h3>
                <div className="grid grid-cols-2 gap-2 rounded-md border bg-muted/20 p-3 text-xs">
                  <div><div className="text-muted-foreground">Тип общения</div><div>{communicationChannelLabels[event.channel] ?? event.channel}</div></div>
                  <div><div className="text-muted-foreground">Сервер / раунд</div><RoundContext event={event} /></div>
                  <div><div className="text-muted-foreground">Роль</div><div>{event.role ?? "неизвестна"}</div></div>
                  <div><div className="text-muted-foreground">Подлодка</div><div>{event.submarine_name ?? "неизвестна"}</div></div>
                  <div><div className="text-muted-foreground">Отсек</div><div>{event.hull_name ?? "-"}</div></div>
                  <div><div className="text-muted-foreground">Координаты</div><div>{event.position_x != null ? `${Math.round(event.position_x)}, ${Math.round(event.position_y ?? 0)}` : "неизвестны"}</div></div>
                </div>
                {event.round_token && <div className="break-all text-[11px] text-muted-foreground">round token: {event.round_token}</div>}
              </section>

              <section className="space-y-2">
                <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">Доставка · {event.recipient_count}</h3><span className="text-[11px] text-muted-foreground">серверное наблюдение</span></div>
                {event.recipients?.length ? <div className="space-y-2">{event.recipients.map((recipient) => {
                  const variant = recipientTextVariant(event.original_text, recipient.delivered_text)
                  return <div key={recipient.id} className="space-y-1 rounded-md border p-3 text-xs">
                    <div className="flex flex-wrap items-center gap-2"><PlayerName playerId={recipient.player_id} playerName={recipient.player_name} /><span className={recipient.delivery_allowed ? "text-emerald-600" : "text-destructive"}>{recipient.delivery_allowed ? "допущен" : "не допущен"}</span>{recipient.server_queued && <Badge variant="outline">в очереди сервера</Badge>}{recipient.is_radio && <Badge variant="outline">радио</Badge>}{recipient.is_dead && <Badge variant="outline">мёртвый чат</Badge>}</div>
                    {variant && <div className="rounded bg-muted/30 p-2 text-muted-foreground">Вариант доставки: «{variant}»</div>}
                    <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground"><span>пакетов: {recipient.packet_count}</span>{recipient.distance_factor != null && <span>factor: {recipient.distance_factor.toFixed(2)}</span>}{recipient.first_observed_at && recipient.last_observed_at && <span>{formatBrowserDate(recipient.first_observed_at)} — {formatBrowserDate(recipient.last_observed_at)}</span>}</div>
                  </div>
                })}</div> : <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">Фактические получатели не зафиксированы.</div>}
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Идентификаторы</h3>
                <div className="divide-y rounded-md border text-xs">
                  <div className="flex items-center justify-between gap-2 p-2"><span className="text-muted-foreground">event_uid</span><span className="flex min-w-0 items-center gap-1"><code className="truncate">{event.event_uid}</code><CopyButton value={event.event_uid} label="event_uid" /></span></div>
                  <div className="flex items-center justify-between gap-2 p-2"><span className="text-muted-foreground">SteamID</span><span className="flex min-w-0 items-center gap-1"><code className="truncate">{event.player_steamid ?? "-"}</code><CopyButton value={event.player_steamid} label="SteamID" /></span></div>
                  <div className="flex items-center justify-between gap-2 p-2"><span className="text-muted-foreground">Текст</span><span className="flex min-w-0 items-center gap-1"><span className="truncate">{event.transcript_text ?? event.original_text ?? "-"}</span><CopyButton value={event.transcript_text ?? event.original_text} label="текст" /></span></div>
                </div>
                {event.round_id != null && event.round_finished && <Link to={`/replays/rounds/${event.round_id}`} className="inline-flex items-center gap-1 text-xs text-primary hover:underline"><ExternalLink className="size-3.5" />Открыть раунд для анализа</Link>}
              </section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}

export function CommunicationsView() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialFilters = filtersFromUrl(searchParams)
  const [search, setSearch] = useState(initialFilters.search)
  const [source, setSource] = useState(initialFilters.source)
  const [channel, setChannel] = useState(initialFilters.channel)
  const [server, setServer] = useState(initialFilters.server)
  const [asrStatus, setAsrStatus] = useState(initialFilters.asrStatus)
  const [context, setContext] = useState(initialFilters.context)
  const [page, setPage] = useState(Number(searchParams.get("comm_page") ?? "1") || 1)
  const [selectedId, setSelectedId] = useState<number | null>(() => {
    const value = Number(searchParams.get("communication_id"))
    return Number.isFinite(value) && value > 0 ? value : null
  })
  const [applied, setApplied] = useState<CommunicationFilters>(initialFilters)

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["player-communications", applied, page],
    queryFn: () => api.get<CommunicationsResponse>(`/api/v1/admin/communications?${buildCommunicationQuery(applied, page)}`),
  })

  const detail = useQuery({
    queryKey: ["player-communication", selectedId],
    queryFn: () => api.get<Communication>(`/api/v1/admin/communications/${selectedId}`),
    enabled: selectedId != null,
  })

  const syncUrl = (next: CommunicationFilters, nextPage: number, communicationId: number | null = selectedId) => {
    setSearchParams((current) => {
      const params = new URLSearchParams(current)
      const values: Record<string, string> = { comm_search: next.search, comm_source: next.source, comm_channel: next.channel, comm_server: next.server, comm_asr: next.asrStatus, comm_context: next.context }
      Object.entries(values).forEach(([key, value]) => value && value !== "all" ? params.set(key, value) : params.delete(key))
      if (nextPage > 1) params.set("comm_page", String(nextPage)); else params.delete("comm_page")
      if (communicationId != null) params.set("communication_id", String(communicationId)); else params.delete("communication_id")
      return params
    })
  }

  const selectCommunication = (id: number | null) => {
    setSelectedId(id)
    syncUrl(applied, page, id)
  }

  const apply = () => {
    const next = { search: search.trim(), source, channel, server, asrStatus, context }
    setPage(1); setApplied(next); syncUrl(next, 1)
  }

  const reset = () => {
    setSearch(""); setSource("all"); setChannel("all"); setServer("all"); setAsrStatus("all"); setContext("all"); setPage(1); setApplied(EMPTY_FILTERS); syncUrl(EMPTY_FILTERS, 1)
  }

  const appliedCount = Number(applied.search.length > 0) + Number(applied.source !== "all") + Number(applied.channel !== "all") + Number(applied.server !== "all") + Number(applied.asrStatus !== "all") + Number(applied.context !== "all")

  return (
    <div className="space-y-4">
      <form onSubmit={(event) => { event.preventDefault(); apply() }}>
        <Card className="mb-4 gap-0 py-0">
          <CardHeader className="border-b py-4"><CardTitle className="flex items-center gap-2 text-base"><Filter className="size-4 text-primary" />Поиск и фильтры</CardTitle><CardDescription>Найдите текст чата, расшифровку голоса или игрока и сузьте выдачу.</CardDescription></CardHeader>
          <CardContent className="space-y-4 py-4">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-12">
              <div className="space-y-1.5 xl:col-span-4"><Label>Текст, транскрипция или игрок</Label><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="найти сообщение…" /></div>
              <div className="space-y-1.5 xl:col-span-2"><Label>Источник</Label><Select value={source} onValueChange={setSource}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все</SelectItem><SelectItem value="chat">Чат</SelectItem><SelectItem value="voice">Голос</SelectItem></SelectContent></Select></div>
              <div className="space-y-1.5 xl:col-span-2"><Label>Тип общения</Label><Select value={channel} onValueChange={setChannel}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все типы</SelectItem>{Object.entries(communicationChannelLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-1.5 xl:col-span-2"><Label>Сервер</Label><Select value={server} onValueChange={setServer}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все серверы</SelectItem><SelectItem value="ASPER1">ASPER1</SelectItem><SelectItem value="ASPER2">ASPER2</SelectItem></SelectContent></Select></div>
              <div className="space-y-1.5 xl:col-span-2"><Label>ASR</Label><Select value={asrStatus} onValueChange={setAsrStatus}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Любой статус</SelectItem><SelectItem value="pending">В очереди</SelectItem><SelectItem value="recognized">Распознано</SelectItem><SelectItem value="failed">Ошибка</SelectItem><SelectItem value="not_applicable">Не применимо</SelectItem></SelectContent></Select></div>
            </div>
            <div className="flex flex-wrap items-end gap-3 border-t pt-3"><div className="space-y-1.5"><Label>Контекст</Label><Select value={context} onValueChange={setContext}><SelectTrigger className="w-52"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">Все контексты</SelectItem>{Object.entries(communicationContextLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>{appliedCount > 0 && <Badge variant="outline">Фильтров: {appliedCount}</Badge>}<div className="ml-auto flex gap-2"><Button type="button" variant="ghost" size="sm" onClick={reset}><RotateCcw className="size-4" />Сбросить</Button><Button type="submit" disabled={isFetching}><Search className="size-4" />Применить</Button></div></div>
          </CardContent>
        </Card>
      </form>

      <Card className="gap-0 py-0">
        <CardHeader className="border-b py-3"><CardTitle className="flex items-center gap-2 text-sm">Результаты <Badge variant="secondary">{data?.total?.toLocaleString("ru-RU") ?? 0} сообщений</Badge></CardTitle><CardDescription className="ml-auto text-right">Серверная доставка, не телепатия клиента</CardDescription></CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto"><Table className="min-w-[1180px]"><TableHeader className="bg-muted/35"><TableRow><TableHead>Время</TableHead><TableHead>Источник</TableHead><TableHead>Тип общения</TableHead><TableHead>Игрок</TableHead><TableHead>Сервер / раунд</TableHead><TableHead>Отсек</TableHead><TableHead>Сообщение</TableHead><TableHead>Получатели</TableHead></TableRow></TableHeader><TableBody>
            {isLoading && <TableRow><TableCell colSpan={8} className="py-12 text-center">Загрузка…</TableCell></TableRow>}
            {!isLoading && !(data?.events.length) && <TableRow><TableCell colSpan={8} className="py-12 text-center text-muted-foreground">Сообщений не найдено</TableCell></TableRow>}
            {data?.events.map((event) => { const text = event.source === "voice" ? event.transcript_text : event.original_text; return <TableRow key={event.id} className="cursor-pointer align-top" data-selected={selectedId === event.id || undefined} onClick={() => selectCommunication(selectedId === event.id ? null : event.id)}>
              <TableCell className="whitespace-nowrap font-mono text-[11px] text-muted-foreground">{formatBrowserDate(event.occurred_at)}</TableCell><TableCell><Badge variant="outline" className="flex w-fit items-center gap-1"><SourceIcon source={event.source} />{sourceLabel(event.source)}</Badge></TableCell><TableCell><Badge variant="secondary">{communicationChannelLabels[event.channel] ?? event.channel}</Badge></TableCell><TableCell className="font-medium"><PlayerName playerId={event.player_id} playerName={event.player_name} stopPropagation /></TableCell><TableCell><RoundContext event={event} /></TableCell><TableCell className="text-xs">{event.hull_name ?? "-"}</TableCell><TableCell className="max-w-[360px] whitespace-normal break-words text-xs">{text ?? <AsrStatus event={event} />}</TableCell><TableCell className="text-xs">{event.recipient_count}</TableCell>
            </TableRow> })}
          </TableBody></Table></div>
          <div className="flex items-center justify-between border-t px-4 py-3 text-xs text-muted-foreground"><span>Страница {data?.page ?? page} из {data?.total_pages ?? 1}</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1 || isFetching} onClick={() => { const next = page - 1; setPage(next); syncUrl(applied, next) }}>Назад</Button><Button variant="outline" size="sm" disabled={!data?.has_next || isFetching} onClick={() => { const next = page + 1; setPage(next); syncUrl(applied, next) }}>Дальше</Button></div></div>
        </CardContent>
      </Card>
      <CommunicationDrawer event={detail.data} communicationId={selectedId} loading={detail.isLoading} onClose={() => selectCommunication(null)} />
    </div>
  )
}
