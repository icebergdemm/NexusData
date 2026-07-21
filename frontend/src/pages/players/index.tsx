import { useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { Users, ChevronRight, ExternalLink, Copy, IdCard, Link2 } from "lucide-react"
import { PlayerAvatar } from "@/components/layout/PlayerAvatar"
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
import { AppContextMenu } from "@/components/layout/AppContextMenu"
import { api } from "@/lib/api"
import type { PlayersListResponse } from "./types"

function copyToClipboard(text: string, label: string) {
  navigator.clipboard.writeText(text).then(() => toast.success(`${label} скопирован`)).catch(() => toast.error("Не удалось скопировать"))
}

function formatPlayTime(hours: number): string {
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}ч ${m}м`
}

interface PlayerSuggestion {
  player_id: number
  player_last_time_name: string
  forced_display_name: string | null
  player_steamid: string
  steam_avatar_url: string | null
}

function playerDisplayName(p: { player_last_time_name: string; forced_display_name?: string | null }): string {
  return p.forced_display_name || p.player_last_time_name
}

export function PlayersListPage() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [searchInput, setSearchInput] = useState("")
  const [search, setSearch] = useState<string | undefined>(undefined)
  const [labelId, setLabelId] = useState<string>("all")
  const [rankId, setRankId] = useState<string>("all")
  const [sort, setSort] = useState<string>("none")
  const [debouncedInput, setDebouncedInput] = useState("")
  const [suggestOpen, setSuggestOpen] = useState(false)
  const blurTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["players", page, search, labelId, rankId, sort],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), page_size: "50" })
      if (search) params.set("search", search)
      if (labelId !== "all") params.set("label_id", labelId)
      if (rankId !== "all") params.set("rank_id", rankId)
      if (sort !== "none") params.set("sort", sort)
      return api.get<PlayersListResponse>(`/api/v1/admin/players?${params.toString()}`)
    },
  })

  useEffect(() => {
    const t = setTimeout(() => setDebouncedInput(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const { data: suggestData } = useQuery({
    queryKey: ["player-suggestions", debouncedInput],
    queryFn: () => api.get<{ players: PlayerSuggestion[] }>(`/api/v1/admin/players/search-suggestions?q=${encodeURIComponent(debouncedInput)}`),
    enabled: debouncedInput.length >= 2,
  })
  const suggestions = debouncedInput.length >= 2 ? (suggestData?.players ?? []) : []

  const submitSearch = () => {
    const trimmed = searchInput.trim()
    setSearch(trimmed.length >= 2 ? trimmed : undefined)
    setPage(1)
    setSuggestOpen(false)
  }

  return (
    <div>
      <PageHeader title="Игроки" description="Все зарегистрированные игроки сервера" icon={Users} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative max-w-xs">
          <SearchInput
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitSearch()}
            onFocus={() => setSuggestOpen(true)}
            onBlur={() => { blurTimeout.current = setTimeout(() => setSuggestOpen(false), 150) }}
            placeholder="Поиск по нику, SteamID, IP..."
          />
          {suggestOpen && suggestions.length > 0 && (
            <div className="absolute top-full left-0 z-20 mt-1 w-full overflow-hidden rounded-md border bg-popover shadow-md">
              {suggestions.map((p) => (
                <button
                  key={p.player_id}
                  className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm hover:bg-accent"
                  onMouseDown={(e) => { e.preventDefault(); if (blurTimeout.current) clearTimeout(blurTimeout.current); navigate(`/players/${p.player_id}`) }}
                >
                  <PlayerAvatar url={p.steam_avatar_url} name={p.player_last_time_name} size={22} />
                  <span
                    className="min-w-0 flex-1 truncate"
                    title={p.forced_display_name ? `Настоящий ник: ${p.player_last_time_name}` : undefined}
                  >
                    {playerDisplayName(p)}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{p.player_steamid}</span>
                </button>
              ))}
            </div>
          )}
        </div>
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
        <Select value={rankId} onValueChange={(v) => { setRankId(v); setPage(1) }}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="Все звания" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все звания</SelectItem>
            {Object.entries(data?.ranks ?? {}).map(([id, name]) => (
              <SelectItem key={id} value={id}>{name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(v) => { setSort(v); setPage(1) }}>
          <SelectTrigger className="w-56">
            <SelectValue placeholder="Сортировка" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">По умолчанию</SelectItem>
            <SelectItem value="playtime_desc">Наиграно: больше - меньше</SelectItem>
            <SelectItem value="playtime_asc">Наиграно: меньше - больше</SelectItem>
            <SelectItem value="last_login_desc">Последний вход: сначала недавние</SelectItem>
            <SelectItem value="last_login_asc">Последний вход: сначала давние</SelectItem>
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
                  <AppContextMenu
                    key={p.player_id}
                    actions={[
                      { label: "Открыть в новой вкладке", icon: ExternalLink, onSelect: () => window.open(`/players/${p.player_id}`, "_blank") },
                      { label: "Скопировать SteamID", icon: IdCard, onSelect: () => copyToClipboard(p.player_steamid, "SteamID") },
                      { label: "Скопировать имя", icon: Copy, onSelect: () => copyToClipboard(playerDisplayName(p), "Имя") },
                    ]}
                  >
                    <TableRow
                      className="cursor-pointer"
                      onClick={() => navigate(`/players/${p.player_id}`)}
                      onAuxClick={(e) => { if (e.button === 1) window.open(`/players/${p.player_id}`, "_blank") }}
                    >
                      <TableCell className="font-medium">
                        {playerDisplayName(p)}
                        {p.forced_display_name && (
                          <Badge
                            variant="outline"
                            className="ml-1.5 border-info/40 text-info"
                            title={`Настоящий ник: ${p.player_last_time_name}`}
                          >
                            <Link2 className="mr-1 size-3" /> принудительное имя
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{p.player_steamid}</TableCell>
                      <TableCell><Badge variant="secondary">{p.rank_name || "-"}</Badge></TableCell>
                      <TableCell>{p.kit_name || "-"}</TableCell>
                      <TableCell>{formatPlayTime(p.play_time_hours)}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {p.last_login_time ? new Date(p.last_login_time).toLocaleString("ru-RU") : "-"}
                      </TableCell>
                      <TableCell><ChevronRight className="h-4 w-4 text-muted-foreground" /></TableCell>
                    </TableRow>
                  </AppContextMenu>
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
