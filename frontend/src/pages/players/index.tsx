import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { Users, ChevronRight } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { api } from "@/lib/api"
import type { PlayersListResponse } from "./types"

function formatPlayTime(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}

export function PlayersListPage() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState("")
  const [search, setSearch] = useState<string | undefined>(undefined)
  const [labelId, setLabelId] = useState<string>("all")

  const { data, isLoading } = useQuery({
    queryKey: ["players", page, search, labelId],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), page_size: "10" })
      if (search) params.set("search", search)
      if (labelId !== "all") params.set("label_id", labelId)
      return api.get<PlayersListResponse>(`/api/v1/admin/players?${params.toString()}`)
    },
  })

  const submitSearch = () => {
    const trimmed = searchInput.trim()
    setSearch(trimmed.length >= 2 ? trimmed : undefined)
    setPage(1)
  }

  return (
    <div>
      <PageHeader title="Игроки" description="Все зарегистрированные игроки сервера" icon={Users} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitSearch()}
          placeholder="Поиск по нику, SteamID, IP..."
          className="max-w-xs"
        />
        <Button variant="secondary" onClick={submitSearch}>Найти</Button>
        <Select value={labelId} onValueChange={(v) => { setLabelId(v); setPage(1) }}>
          <SelectTrigger className="w-56">
            <SelectValue placeholder="Все метки" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все метки</SelectItem>
            {data?.label_categories.map((c) => (
              <SelectItem key={c.id} value={String(c.id)}>{c.icon} {c.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {data && <Badge variant="secondary" className="ml-auto font-normal">{data.total} всего</Badge>}
      </div>

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9" />)}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Игрок</TableHead>
                  <TableHead>SteamID</TableHead>
                  <TableHead>Ранг</TableHead>
                  <TableHead>Набор</TableHead>
                  <TableHead>Наиграно</TableHead>
                  <TableHead>Последний вход</TableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.players.map((p) => (
                  <TableRow
                    key={p.player_id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/players/${p.player_id}`)}
                  >
                    <TableCell className="font-medium">{p.player_last_time_name}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{p.player_steamid}</TableCell>
                    <TableCell><Badge variant="secondary">{p.rank_name || "-"}</Badge></TableCell>
                    <TableCell>{p.kit_name || "-"}</TableCell>
                    <TableCell>{formatPlayTime(p.play_time_hours)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {p.last_login_time ? new Date(p.last_login_time).toLocaleString("ru-RU") : "-"}
                    </TableCell>
                    <TableCell><ChevronRight className="h-4 w-4 text-muted-foreground" /></TableCell>
                  </TableRow>
                ))}
                {!data?.players.length && (
                  <TableRow>
                    <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                      Ничего не найдено
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}

          {data && data.total_pages > 1 && (
            <div className="flex items-center justify-center gap-2 border-t p-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Назад
              </Button>
              <span className="text-sm text-muted-foreground">
                Страница {data.page} из {data.total_pages}
              </span>
              <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>
                Вперёд
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
