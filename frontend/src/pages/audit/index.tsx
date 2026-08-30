import { useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { useInfiniteScroll } from "@/hooks/useInfiniteScroll"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { ScrollText, Plus, Pencil, Trash2, LogIn, Activity, Search, RotateCcw, CalendarClock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"

interface AuditLog {
  id: number; admin_id: number; admin_name: string | null; action: string
  details: Record<string, unknown> | null; created_at: string | null
}
interface AuditResponse { logs: AuditLog[]; total: number; page: number; page_size: number; total_pages: number }

function describeDetails(details: Record<string, unknown> | null): string {
  if (!details) return ""
  const target = details.target as Record<string, unknown> | undefined
  const parts: string[] = []
  if (target) {
    if (target.name) parts.push(String(target.name))
    if (target.steamid) parts.push(String(target.steamid))
    if (target.username) parts.push(String(target.username))
    if (target.player_id != null) parts.push(`player #${target.player_id}`)
    if (target.admin_id != null) parts.push(`admin #${target.admin_id}`)
    if (target.session_id != null) parts.push(`session #${target.session_id}`)
    if (target.kit_id != null) parts.push(`kit #${target.kit_id}`)
  }
  if (Array.isArray(details.changes)) {
    for (const c of details.changes as { field: string; from: unknown; to: unknown }[]) {
      parts.push(`${c.field}: ${c.from} → ${c.to}`)
    }
  }
  if (!parts.length) {
    if (details.reason) parts.push(String(details.reason))
    else if (details.steamid) parts.push(String(details.steamid))
  }
  return parts.join(" · ") || JSON.stringify(details).slice(0, 80)
}

type ActionCategory = "create" | "update" | "delete" | "auth" | "other"

function actionCategory(action: string): ActionCategory {
  const a = action.toLowerCase()
  if (/(delete|revoke|remove|void|kick|ban)/.test(a)) return "delete"
  if (/(create|add|assign|generate|grant|create_admin)/.test(a)) return "create"
  if (/(update|edit|save|toggle|change)/.test(a)) return "update"
  if (/(login|logout|auth)/.test(a)) return "auth"
  return "other"
}

const CATEGORY_META: Record<ActionCategory, { icon: typeof Plus; classes: string }> = {
  create: { icon: Plus, classes: "bg-success/15 text-success" },
  update: { icon: Pencil, classes: "bg-info/15 text-info" },
  delete: { icon: Trash2, classes: "bg-destructive/15 text-destructive" },
  auth: { icon: LogIn, classes: "bg-primary/15 text-primary" },
  other: { icon: Activity, classes: "bg-muted text-muted-foreground" },
}

function initials(name: string | null, id: number): string {
  if (!name) return `#${id}`.slice(0, 2)
  return name.slice(0, 2).toUpperCase()
}

export function AuditPage() {
  const [source, setSource] = useState<"" | "discord" | "all">("")
  const [searchInput, setSearchInput] = useState("")
  const debouncedSearch = useDebouncedValue(searchInput.trim(), 400)
  const [category, setCategory] = useState("all")
  const [actionFilter, setActionFilter] = useState("")
  const debouncedAction = useDebouncedValue(actionFilter.trim(), 400)
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const hasActiveFilters = !!debouncedSearch || category !== "all" || !!debouncedAction || !!dateFrom || !!dateTo || source !== ""

  const { data: infiniteData, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage, isFetching } = useInfiniteQuery({
    queryKey: ["audit-logs", source, debouncedSearch, category, debouncedAction, dateFrom, dateTo],
    queryFn: ({ pageParam = 1 }) => {
      const params = new URLSearchParams({ page: String(pageParam as number), page_size: "50" })
      if (source === "discord" || source === "") params.set("source", source)
      // source === "all" — не отправляем параметр, бэкенд вернёт все
      if (debouncedSearch) params.set("q", debouncedSearch)
      if (category !== "all") params.set("category", category)
      if (debouncedAction) params.set("action", debouncedAction)
      if (dateFrom) params.set("date_from", new Date(dateFrom).toISOString())
      if (dateTo) params.set("date_to", new Date(dateTo).toISOString())
      return api.get<AuditResponse>(`/api/v1/admin/audit/logs?${params.toString()}`)
    },
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page < last.total_pages ? last.page + 1 : undefined),
  })
  const allLogs = infiniteData?.pages.flatMap((p) => p.logs) ?? []
  const total = infiniteData?.pages[0]?.total ?? 0
  const sentinelRef = useInfiniteScroll(() => { if (hasNextPage) fetchNextPage() }, !!hasNextPage, isFetchingNextPage)

  const filtered = allLogs

  return (
    <div>
      <PageHeader
        title="Аудит"
        description="Журнал всех действий администраторов в панели"
        icon={ScrollText}
        actions={<Badge variant="secondary" className="text-sm font-normal">{total} записей всего</Badge>}
      />

      <Card className="mb-4 py-0">
        <CardContent className="space-y-3 p-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-64 flex-1">
              <Label className="mb-1.5 block text-xs">Поиск</Label>
              <SearchInput
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Действие, админ, детали, SteamID, reason… (мощный поиск по всем полям)"
              />
            </div>
            <div className="w-44">
              <Label className="mb-1.5 block text-xs">Категория</Label>
              <Select value={category} onValueChange={setCategory}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Все категории</SelectItem>
                  <SelectItem value="create">Создание</SelectItem>
                  <SelectItem value="update">Изменение</SelectItem>
                  <SelectItem value="delete">Удаление</SelectItem>
                  <SelectItem value="auth">Авторизация</SelectItem>
                  <SelectItem value="other">Прочее</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="w-48">
              <Label className="mb-1.5 block text-xs">Действие (подстрока)</Label>
              <div className="relative">
                <Search className="absolute left-2 top-2.5 size-3.5 text-muted-foreground" />
                <Input value={actionFilter} onChange={(e) => setActionFilter(e.target.value)} placeholder="напр. ban, create" className="pl-7" />
              </div>
            </div>
            {(hasActiveFilters || isFetching) && (
              <div className="flex items-center gap-2">
                {isFetching && <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />}
                <Badge variant="secondary">{total} найдено</Badge>
                {hasActiveFilters && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setSearchInput("")
                      setCategory("all")
                      setActionFilter("")
                      setDateFrom("")
                      setDateTo("")
                      setSource("")
                    }}
                  >
                    <RotateCcw className="size-3.5" /> Сбросить
                  </Button>
                )}
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex gap-2">
              <Button size="sm" variant={source === "" ? "default" : "outline"} onClick={() => setSource("")}>Панель</Button>
              <Button size="sm" variant={source === "discord" ? "default" : "outline"} onClick={() => setSource("discord")}>Discord</Button>
              <Button size="sm" variant={source === "all" ? "default" : "outline"} onClick={() => setSource("all")}>Все</Button>
            </div>
            <div className="flex items-end gap-2">
              <div>
                <Label className="mb-1.5 block text-xs flex items-center gap-1"><CalendarClock className="size-3" /> С даты</Label>
                <Input type="datetime-local" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-44" />
              </div>
              <div>
                <Label className="mb-1.5 block text-xs">По дату</Label>
                <Input type="datetime-local" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-44" />
              </div>
            </div>
            <span className="ml-auto text-[11px] text-muted-foreground">Мощный поиск: ищет по action, имени админа и JSON-деталям (CAST → TEXT) на сервере, а не только на загруженной странице</span>
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? <div className="space-y-2 p-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div> : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Админ</TableHead>
                    <TableHead>Действие</TableHead>
                    <TableHead>Детали</TableHead>
                    <TableHead className="text-right">Время</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.map((l) => {
                    const cat = actionCategory(l.action)
                    const meta = CATEGORY_META[cat]
                    return (
                      <TableRow key={l.id}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-secondary text-[10px] font-semibold">
                              {initials(l.admin_name, l.admin_id)}
                            </div>
                            <span className="font-medium">{l.admin_name ?? `#${l.admin_id}`}</span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-mono text-xs ${meta.classes}`}>
                            <meta.icon className="h-3 w-3" />
                            {l.action}
                          </span>
                        </TableCell>
                        <TableCell className="max-w-md truncate text-sm text-muted-foreground" title={describeDetails(l.details)}>
                          {describeDetails(l.details) || "-"}
                        </TableCell>
                        <TableCell className="text-right text-sm whitespace-nowrap text-muted-foreground">
                          {formatBrowserDate(l.created_at)}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                  {!filtered.length && <TableRow><TableCell colSpan={4} className="py-10 text-center text-muted-foreground">Записей не найдено</TableCell></TableRow>}
                </TableBody>
              </Table>
              <div ref={sentinelRef as never} className="h-1" />
              {isFetchingNextPage && (
                <div className="flex justify-center gap-2 p-3 text-sm text-muted-foreground">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" /> Загрузка…
                </div>
              )}
              {!hasNextPage && allLogs.length > 0 && (
                <div className="border-t p-3 text-center text-xs text-muted-foreground">Загружено {allLogs.length} из {total} · конец списка</div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
