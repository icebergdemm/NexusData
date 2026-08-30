import { useMemo, useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { useInfiniteScroll } from "@/hooks/useInfiniteScroll"
import { ScrollText, Plus, Pencil, Trash2, LogIn, Activity } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
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
  const [search, setSearch] = useState("")
  const [source, setSource] = useState<"" | "discord">("")
  const { data: infiniteData, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["audit-logs", source],
    queryFn: ({ pageParam = 1 }) => api.get<AuditResponse>(`/api/v1/admin/audit/logs?page=${pageParam as number}&page_size=50&source=${source}`),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page < last.total_pages ? last.page + 1 : undefined),
  })
  const allLogs = infiniteData?.pages.flatMap((p) => p.logs) ?? []
  const total = infiniteData?.pages[0]?.total ?? 0
  const sentinelRef = useInfiniteScroll(() => { if (hasNextPage) fetchNextPage() }, !!hasNextPage, isFetchingNextPage)

  const filtered = useMemo(() => {
    if (!search.trim()) return allLogs
    const q = search.trim().toLowerCase()
    return allLogs.filter((l) =>
      l.action.toLowerCase().includes(q) ||
      (l.admin_name ?? "").toLowerCase().includes(q) ||
      describeDetails(l.details).toLowerCase().includes(q)
    )
  }, [allLogs, search])

  return (
    <div>
      <PageHeader
        title="Аудит"
        description="Журнал всех действий администраторов в панели"
        icon={ScrollText}
        actions={<Badge variant="secondary" className="text-sm font-normal">{total} записей всего</Badge>}
      />

      <div className="mb-4">
        <div className="mb-2 flex gap-2">
          <Button size="sm" variant={!source ? "default" : "outline"} onClick={() => setSource("")}>Действия панели</Button>
          <Button size="sm" variant={source === "discord" ? "default" : "outline"} onClick={() => setSource("discord")}>Discord-логи</Button>
        </div>
        <SearchInput
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Фильтр по админу, действию или деталям (на этой странице)..."
          className="max-w-sm"
        />
      </div>

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
