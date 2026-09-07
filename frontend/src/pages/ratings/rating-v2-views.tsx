import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

type UnknownRecord = Record<string, unknown>

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : {}
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—"
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value)
  return JSON.stringify(value)
}

function DataRows({ data }: { data: unknown }) {
  const entries = Object.entries(asRecord(data))
  if (!entries.length) return <p className="text-sm text-muted-foreground">Нет данных</p>
  return <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[minmax(10rem,auto)_1fr]">
    {entries.map(([key, value]) => <div key={key} className="contents">
      <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
      <dd className="break-words">{displayValue(value)}</dd>
    </div>)}
  </dl>
}

function eventRows(value: unknown): UnknownRecord[] {
  if (Array.isArray(value)) return value.map(asRecord).filter((event) => Object.keys(event).length > 0)
  const response = asRecord(value)
  const events = Array.isArray(response.events)
    ? response.events
    : Array.isArray(response.history) ? response.history
      : Array.isArray(response.items) ? response.items : []
  return events.map(asRecord).filter((event) => Object.keys(event).length > 0)
}

export function RatingV2Investigation({ player, explanation, events, history, delivery }: { player: unknown; explanation: unknown; events: unknown; history?: unknown; delivery?: unknown }) {
  const rows = eventRows(events)
  const historyRows = eventRows(history)
  const deliveryRows = eventRows(delivery)
  return <div className="grid gap-4 xl:grid-cols-3">
    <Card>
      <CardHeader><CardTitle className="text-sm">Данные игрока</CardTitle></CardHeader>
      <CardContent><DataRows data={player} /></CardContent>
    </Card>
    <Card>
      <CardHeader><CardTitle className="text-sm">Объяснение расчёта</CardTitle></CardHeader>
      <CardContent><DataRows data={explanation} /></CardContent>
    </Card>
    <Card>
      <CardHeader><CardTitle className="text-sm">История позиции</CardTitle></CardHeader>
      <CardContent>{historyRows.length ? <div className="overflow-x-auto"><Table>
        <TableHeader><TableRow><TableHead>Время</TableHead><TableHead>Место</TableHead><TableHead>Рейтинг</TableHead></TableRow></TableHeader>
        <TableBody>{historyRows.map((entry, index) => <TableRow key={String(entry.id ?? entry.captured_at ?? index)}>
          <TableCell>{displayValue(entry.captured_at)}</TableCell><TableCell>{displayValue(entry.rank)}</TableCell><TableCell>{displayValue(entry.rating)}</TableCell>
        </TableRow>)}</TableBody>
      </Table></div> : <p className="text-sm text-muted-foreground">История появится после первого периодического снимка</p>}</CardContent>
    </Card>
    <Card className="xl:col-span-3">
      <CardHeader><CardTitle className="text-sm">Журнал рейтинга</CardTitle></CardHeader>
      <CardContent>
        {rows.length ? <div className="overflow-x-auto"><Table>
          <TableHeader><TableRow><TableHead>Время</TableHead><TableHead>Событие</TableHead><TableHead>Изменение</TableHead><TableHead>Источник</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map((event, index) => <TableRow key={String(event.id ?? event.event_id ?? index)}>
            <TableCell>{displayValue(event.created_at ?? event.timestamp ?? event.occurred_at)}</TableCell>
            <TableCell>{displayValue(event.event_type ?? event.type ?? event.kind)}</TableCell>
            <TableCell>{displayValue(event.points_delta ?? event.delta ?? event.rating_delta)}</TableCell>
            <TableCell>{displayValue(event.source_server ?? event.source ?? event.session_trace_id)}</TableCell>
          </TableRow>)}</TableBody>
        </Table></div> : <p className="text-sm text-muted-foreground">Нет данных</p>}
      </CardContent>
    </Card>
    <Card className="xl:col-span-3">
      <CardHeader><CardTitle className="text-sm">Доставка</CardTitle></CardHeader>
      <CardContent>{deliveryRows.length ? <div className="overflow-x-auto"><Table>
        <TableHeader><TableRow><TableHead>Событие</TableHead><TableHead>Статус</TableHead><TableHead>Причина</TableHead><TableHead>Время</TableHead></TableRow></TableHeader>
        <TableBody>{deliveryRows.map((entry, index) => <TableRow key={String(entry.id ?? entry.event_id ?? index)}>
          <TableCell>{displayValue(entry.event_id)}</TableCell><TableCell>{displayValue(entry.status)}</TableCell><TableCell>{displayValue(entry.reason)}</TableCell><TableCell>{displayValue(entry.received_at)}</TableCell>
        </TableRow>)}</TableBody>
      </Table></div> : <p className="text-sm text-muted-foreground">Нет данных о доставке</p>}</CardContent>
    </Card>
  </div>
}
