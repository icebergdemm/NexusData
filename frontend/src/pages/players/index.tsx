import { useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useInfiniteScroll } from "@/hooks/useInfiniteScroll"
import { toast } from "sonner"
import { Users, ChevronRight, ExternalLink, Copy, IdCard, Palette, RefreshCw } from "lucide-react"
import { PlayerAvatar } from "@/components/layout/PlayerAvatar"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { AppContextMenu } from "@/components/layout/AppContextMenu"
import { PlayerFiltersToolbar, type PlayerFilterSortOption } from "@/components/layout/PlayerFiltersSheet"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { formatBrowserDate } from "@/lib/format-date"
import type { PlayersListResponse } from "./types"
import { resetPlayerFilters, type PlayerSort } from "@/lib/player-filters"
import { clearPlayerFilterState, loadPlayerFilterState, loadPlayerSort, savePlayerFilterState, savePlayerSort } from "@/lib/player-filter-storage"

/** Superuser-only popover — lets them tweak the background color shown
 * behind each rank badge in the table below. Persisted server-side
 * (AppSetting "rank_colors", routes/admin.py) so it applies for everyone,
 * not just the editing admin. */
function RankColorEditor({ ranks, rankColors }: { ranks: Record<string, string>; rankColors: Record<string, string> }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Record<string, string>>(rankColors)

  useEffect(() => setDraft(rankColors), [rankColors])

  const saveMutation = useMutation({
    mutationFn: (colors: Record<string, string>) => api.patch("/api/v1/admin/players/rank-colors", { colors }),
    onSuccess: () => {
      toast.success("Цвета званий сохранены")
      queryClient.invalidateQueries({ queryKey: ["players"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="icon" title="Настроить цвета званий">
          <Palette className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72">
        <div className="mb-2 text-sm font-medium">Цвета званий</div>
        <div className="space-y-2">
          {Object.entries(ranks).map(([id, name]) => (
            <div key={id} className="flex items-center justify-between gap-2">
              <span className="text-sm">{name}</span>
              <input
                type="color"
                value={draft[id] ?? "#888888"}
                onChange={(e) => setDraft((cur) => ({ ...cur, [id]: e.target.value }))}
                className="h-7 w-12 cursor-pointer rounded border bg-transparent"
              />
            </div>
          ))}
        </div>
        <Button
          className="mt-3 w-full"
          size="sm"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate(draft)}
        >
          Сохранить
        </Button>
      </PopoverContent>
    </Popover>
  )
}

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

const PLAYER_SORT_OPTIONS: readonly PlayerFilterSortOption[] = [
  { key: "none", label: "По умолчанию" },
  { key: "play_time", label: "Наиграно" },
  { key: "last_login", label: "Последний вход" },
  { key: "last_activity", label: "Последняя активность" },
  { key: "active_sanctions", label: "Действующие взыскания" },
  { key: "open_anomalies", label: "Открытые сигналы" },
  { key: "discord_link", label: "Привязка Discord" },
]

const DEFAULT_PLAYER_SORT: PlayerSort = { key: "none", direction: "desc" }

function playerSortToApi(sort: PlayerSort): string | undefined {
  if (sort.key === "play_time") return `playtime_${sort.direction}`
  if (sort.key === "last_login") return `last_login_${sort.direction}`
  if (sort.key === "last_activity") return `last_activity_${sort.direction}`
  if (sort.key === "active_sanctions") return `active_sanctions_${sort.direction}`
  if (sort.key === "open_anomalies") return `open_anomalies_${sort.direction}`
  if (sort.key === "discord_link") return `discord_link_${sort.direction}`
  return undefined
}

export function PlayersListPage() {
  const navigate = useNavigate()
  const { data: admin } = useCurrentAdmin()
  const queryClient = useQueryClient()
  const [searchInput, setSearchInput] = useState("")
  const [search, setSearch] = useState<string | undefined>(undefined)
  const [filters, setFilters] = useState(loadPlayerFilterState)
  const [sort, setSort] = useState(() => loadPlayerSort("players", DEFAULT_PLAYER_SORT, PLAYER_SORT_OPTIONS.map((option) => option.key)))
  const [debouncedInput, setDebouncedInput] = useState("")
  const [suggestOpen, setSuggestOpen] = useState(false)
  const blurTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  const {
    data: infiniteData,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["players", search, filters, playerSortToApi(sort)],
    queryFn: ({ pageParam = 1 }) => {
      const params = new URLSearchParams({ page: String(pageParam as number), page_size: "50" })
      if (search) params.set("search", search)
      if (filters.labelIds.length > 0) params.set("label_ids", filters.labelIds.join(","))
      if (filters.labelMode !== "all" && filters.labelIds.length === 0) params.set("label_mode", filters.labelMode)
      if (filters.rankId !== "all") params.set("rank_id", filters.rankId)
      if (filters.discordFilter !== "all") params.set("discord_link", filters.discordFilter)
      if (filters.notesMode !== "all") params.set("notes", filters.notesMode)
      if (filters.sanctionsMode !== "all") params.set("sanctions", filters.sanctionsMode)
      if (filters.anomaliesMode !== "all") params.set("anomalies", filters.anomaliesMode)
      if (filters.nameHistoryMode !== "all") params.set("name_history", filters.nameHistoryMode)
      if (filters.activityMode !== "all") params.set("activity", filters.activityMode)
      const apiSort = playerSortToApi(sort)
      if (apiSort) params.set("sort", apiSort)
      return api.get<PlayersListResponse>(`/api/v1/admin/players?${params.toString()}`)
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) => (lastPage.page < lastPage.total_pages ? lastPage.page + 1 : undefined),
  })
  const data = infiniteData?.pages[0]
  const allPlayers = infiniteData?.pages.flatMap((p) => p.players) ?? []
  const sentinelRef = useInfiniteScroll(() => { if (hasNextPage) fetchNextPage() }, !!hasNextPage, isFetchingNextPage)

  useEffect(() => {
    const t = setTimeout(() => setDebouncedInput(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  useEffect(() => savePlayerFilterState(filters), [filters])
  useEffect(() => savePlayerSort("players", sort), [sort])

  const { data: suggestData } = useQuery({
    queryKey: ["player-suggestions", debouncedInput],
    queryFn: () => api.get<{ players: PlayerSuggestion[] }>(`/api/v1/admin/players/search-suggestions?q=${encodeURIComponent(debouncedInput)}`),
    enabled: debouncedInput.length >= 2,
  })
  const suggestions = debouncedInput.length >= 2 ? (suggestData?.players ?? []) : []

  const submitSearch = () => {
    const trimmed = searchInput.trim()
    setSearch(trimmed.length >= 2 ? trimmed : undefined)
    setSuggestOpen(false)
  }

  const syncPlayerMutation = useMutation({
    mutationFn: (playerId: number) => api.post(`/api/v1/admin/players/${playerId}/sync`, {}),
    onSuccess: () => toast.success("Синхронизация запрошена — сервер обновит кэш при следующем опросе очереди"),
    onError: (e: Error) => toast.error(e.message),
  })

  const syncAllMutation = useMutation({
    mutationFn: () => api.post("/api/v1/admin/players/sync-all", {}),
    onSuccess: () => {
      toast.success("Принудительная синхронизация всех игроков поставлена в очередь")
      queryClient.invalidateQueries({ queryKey: ["players"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div>
      <PageHeader title="Игроки" description="Все зарегистрированные игроки сервера" icon={Users} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1">
          <SearchInput
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitSearch()}
            onFocus={() => setSuggestOpen(true)}
            onBlur={() => { blurTimeout.current = setTimeout(() => setSuggestOpen(false), 150) }}
            placeholder="Поиск по нику, SteamID, Discord ID, IP..."
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
                    title={p.forced_display_name ? `Закреплённое: ${p.forced_display_name}` : undefined}
                  >
                    {p.player_last_time_name}
                    {p.forced_display_name && <span className="ml-1 text-muted-foreground">🔗 {p.forced_display_name}</span>}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{p.player_steamid}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <PlayerFiltersToolbar
          tabLabel="Игроки"
          filters={filters}
          labelOptions={[
            ...(data?.label_categories.map((category) => ({ value: String(category.id), label: category.name, icon: category.icon })) ?? []),
          ]}
          rankOptions={[
            { value: "all", label: "Все звания" },
            ...Object.entries(data?.ranks ?? {}).map(([id, name]) => ({ value: id, label: name })),
          ]}
          sort={sort}
          defaultSort={DEFAULT_PLAYER_SORT}
          sortOptions={PLAYER_SORT_OPTIONS}
          onFiltersChange={setFilters}
          onSortChange={setSort}
          onReset={() => { clearPlayerFilterState(); setFilters(resetPlayerFilters()); setSort(DEFAULT_PLAYER_SORT) }}
        />
        <Button variant="secondary" onClick={submitSearch}>Найти</Button>
        <div className="ml-auto flex items-center gap-2">
          {admin?.is_superuser && (
            <Button
              variant="outline"
              size="sm"
              disabled={syncAllMutation.isPending}
              onClick={() => syncAllMutation.mutate()}
              title="Принудительно обновить кэш игроков на сервере, минуя проверку изменений"
            >
              <RefreshCw className="size-3.5" /> Синхронизировать всех
            </Button>
          )}
          {admin?.is_superuser && data && <RankColorEditor ranks={data.ranks} rankColors={data.rank_colors} />}
          {data && <Badge variant="secondary" className="font-normal">{data.total} всего</Badge>}
        </div>
      </div>

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9" />)}
            </div>
          ) : (
            <div className="px-10 py-8">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Игрок</TableHead>
                  <TableHead>SteamID</TableHead>
                  <TableHead>Discord ID</TableHead>
                  <TableHead>Ранг</TableHead>
                  <TableHead>Набор</TableHead>
                  <TableHead>Наиграно</TableHead>
                  <TableHead>Последний вход</TableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {allPlayers.map((p) => {
                  const rankColor = data?.rank_colors[String(p.rank_id)]
                  return (
                  <AppContextMenu
                    key={p.player_id}
                    actions={[
                      { label: "Открыть в новой вкладке", icon: ExternalLink, onSelect: () => window.open(`/players/${p.player_id}`, "_blank") },
                      { label: "Скопировать SteamID", icon: IdCard, onSelect: () => copyToClipboard(p.player_steamid, "SteamID") },
                      { label: "Скопировать имя", icon: Copy, onSelect: () => copyToClipboard(playerDisplayName(p), "Имя") },
                      { label: "Синхронизировать с игрой", icon: RefreshCw, onSelect: () => syncPlayerMutation.mutate(p.player_id) },
                    ]}
                  >
                    <TableRow
                      className="cursor-pointer"
                      onClick={() => navigate(`/players/${p.player_id}`)}
                      onAuxClick={(e) => { if (e.button === 1) window.open(`/players/${p.player_id}`, "_blank") }}
                    >
                      <TableCell className="font-medium">
                        <span>{p.player_last_time_name}</span>
                        {p.forced_display_name && (
                          <span className="ml-1 text-[11px] text-muted-foreground" title={`Закреплённое имя: ${p.forced_display_name}`}>🔗</span>
                        )}
                        {p.forced_display_name && p.forced_display_name !== p.player_last_time_name && (
                          <div className="text-xs font-normal text-muted-foreground">🔗 {p.forced_display_name}</div>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{p.player_steamid}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{p.discord_id ?? "-"}</TableCell>
                      <TableCell>
                        <Badge
                          variant="secondary"
                          className="border-transparent text-white"
                          style={rankColor ? { backgroundColor: rankColor } : undefined}
                        >
                          {p.rank_name || "-"}
                        </Badge>
                      </TableCell>
                      <TableCell>{p.kit_name || "-"}</TableCell>
                      <TableCell>{formatPlayTime(p.play_time_hours)}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {p.last_login_time ? formatBrowserDate(p.last_login_time) : "-"}
                      </TableCell>
                      <TableCell><ChevronRight className="h-4 w-4 text-muted-foreground" /></TableCell>
                    </TableRow>
                  </AppContextMenu>
                  )
                })}
                {!allPlayers.length && !isLoading && (
                  <TableRow>
                    <TableCell colSpan={8} className="py-10 text-center text-muted-foreground">
                      Ничего не найдено
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            {/* динамическая подгрузка при скролле */}
            <div ref={sentinelRef as never} className="h-1" />
            {isFetchingNextPage && (
              <div className="flex justify-center gap-2 p-3 text-sm text-muted-foreground">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" /> Загрузка…
              </div>
            )}
            {!hasNextPage && allPlayers.length > 0 && (
              <div className="border-t p-3 text-center text-xs text-muted-foreground">Загружено {allPlayers.length} из {data?.total ?? "?"} · конец списка</div>
            )}
            </div>
          )}

        </CardContent>
      </Card>
    </div>
  )
}
