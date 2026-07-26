import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ScrollText, RefreshCw } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"

interface LogEvent {
  timestamp: string
  player_name: string | null
  action_type: string
  details: string | null
  server_name: string | null
  source_file: string | null
}
interface LogsResponse { events: LogEvent[]; total: number; page: number; page_size: number; total_pages: number }
interface LogStatEntry { event_count: number; last_refresh: string | null; oldest_event: string | null; newest_event: string | null }

const ACTION_TYPES = [
  "picked_up", "placed", "removed", "opened", "closed", "equipped", "unequipped", "dropped",
  "reactor", "repair", "fabrication", "fabrication_cancel", "activated", "deactivated", "wire",
  "pump", "settings_change", "console", "death", "chat", "login", "logout", "attack",
  "structure", "kick", "name_change", "connection", "radio", "other",
]

const ACTION_COLOR: Record<string, string> = {
  death: "text-destructive", kick: "text-destructive", attack: "text-destructive",
  login: "text-success", connection: "text-success",
  logout: "text-muted-foreground",
  chat: "text-info", radio: "text-info",
  console: "text-warning", settings_change: "text-warning",
}

export function LogsPage() {
  const { data: admin } = useCurrentAdmin()
  const [server, setServer] = useState("all")
  const [category, setCategory] = useState("all")
  const [actionType, setActionType] = useState("all")
  const [player, setPlayer] = useState("")
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ["logs-events", server, category, actionType, player, search, page],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), page_size: "100" })
      if (server !== "all") params.set("server", server)
      if (category !== "all") params.set("category", category)
      if (actionType !== "all") params.set("action_type", actionType)
      if (player.trim()) params.set("player", player.trim())
      if (search.trim()) params.set("search", search.trim())
      return api.get<LogsResponse>(`/api/v1/admin/logs/events?${params}`)
    },
  })

  const { data: stats } = useQuery({
    queryKey: ["logs-stats"],
    queryFn: () => api.get<Record<string, LogStatEntry>>("/api/v1/admin/logs/stats"),
  })

  const refresh = useMutation({
    mutationFn: () => api.post("/api/v1/admin/logs/refresh"),
    onSuccess: () => { toast.success("Обновление логов запущено в фоне"); setTimeout(() => queryClient.invalidateQueries({ queryKey: ["logs-stats"] }), 5000) },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div>
      <PageHeader
        title="Логи сервера"
        description="События игровых серверов: действия игроков, консоль, подключения"
        icon={ScrollText}
        actions={admin?.is_superuser && (
          <Button variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            <RefreshCw className={`h-4 w-4 ${refresh.isPending ? "animate-spin" : ""}`} /> Обновить из SFTP
          </Button>
        )}
      />

      {stats && Object.keys(stats).length > 0 && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          {Object.entries(stats).map(([sn, s]) => (
            <Card key={sn}>
              <CardContent className="flex items-center justify-between pt-6 text-sm">
                <div>
                  <div className="font-semibold">{sn}</div>
                  <div className="text-xs text-muted-foreground">
                    Обновлено: {s.last_refresh ? new Date(s.last_refresh).toLocaleString("ru-RU") : "-"}
                  </div>
                </div>
                <Badge variant="secondary">{s.event_count.toLocaleString("ru-RU")} событий</Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} placeholder="Поиск по тексту события..." className="max-w-56" />
        <SearchInput value={player} onChange={(e) => { setPlayer(e.target.value); setPage(1) }} placeholder="Игрок..." className="max-w-44" />
        <Select value={server} onValueChange={(v) => { setServer(v); setPage(1) }}>
          <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все серверы</SelectItem>
            <SelectItem value="ASPER1">ASPER1</SelectItem>
            <SelectItem value="ASPER2">ASPER2</SelectItem>
          </SelectContent>
        </Select>
        <Select value={category} onValueChange={(v) => { setCategory(v); setPage(1) }}>
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все категории</SelectItem>
            <SelectItem value="player">Игровые</SelectItem>
            <SelectItem value="technical">Технические</SelectItem>
          </SelectContent>
        </Select>
        <Select value={actionType} onValueChange={(v) => { setActionType(v); setPage(1) }}>
          <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все типы действий</SelectItem>
            {ACTION_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-8" />)}</div> : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-36">Время</TableHead>
                    <TableHead className="w-24">Сервер</TableHead>
                    <TableHead className="w-40">Игрок</TableHead>
                    <TableHead className="w-32">Действие</TableHead>
                    <TableHead>Детали</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.events.map((e, i) => (
                    <TableRow key={i}>
                      <TableCell className="text-xs whitespace-nowrap text-muted-foreground">{new Date(e.timestamp).toLocaleString("ru-RU")}</TableCell>
                      <TableCell><Badge variant="outline">{e.server_name ?? "-"}</Badge></TableCell>
                      <TableCell className="truncate font-medium">{e.player_name ?? "-"}</TableCell>
                      <TableCell>
                        <span className={`font-mono text-xs ${ACTION_COLOR[e.action_type] ?? "text-foreground"}`}>{e.action_type}</span>
                      </TableCell>
                      <TableCell className="max-w-md truncate text-sm text-muted-foreground" title={e.details ?? undefined}>{e.details ?? "-"}</TableCell>
                    </TableRow>
                  ))}
                  {!data?.events.length && <TableRow><TableCell colSpan={5} className="py-10 text-center text-muted-foreground">Событий не найдено</TableCell></TableRow>}
                </TableBody>
              </Table>
              {data && data.total_pages > 1 && (
                <div className="flex items-center justify-center gap-2 border-t p-3">
                  <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
                  <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages} ({data.total.toLocaleString("ru-RU")})</span>
                  <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
