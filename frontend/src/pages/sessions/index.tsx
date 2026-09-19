import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { Clock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"

interface SessionRow {
  id: number; admin_id: number; admin_name: string | null
  created_at: string | null; expires_at: string | null; last_seen_at: string | null
  last_seen_ip: string | null; user_agent: string | null; auth_type: string | null
  revoked: boolean; is_current: boolean
}
interface SessionsResponse { sessions: SessionRow[]; can_edit: boolean }

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

export function SessionsPage({ embedded = false }: { embedded?: boolean }) {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const showHistory = searchParams.get("history") === "1"
  const adminId = searchParams.get("admin")
  const { data, isLoading } = useQuery({
    queryKey: ["admin-sessions", showHistory, adminId],
    queryFn: () => {
      const params = new URLSearchParams({ active: String(!showHistory) })
      if (adminId) params.set("admin_id", adminId)
      return api.get<SessionsResponse>(`/api/v1/admin/sessions?${params.toString()}`)
    },
  })

  const toggleHistory = () => {
    const next = new URLSearchParams(searchParams)
    if (showHistory) next.delete("history")
    else next.set("history", "1")
    setSearchParams(next)
  }

  const revoke = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/sessions/${id}/revoke`),
    onSuccess: () => { toast.success("Сессия отозвана"); queryClient.invalidateQueries({ queryKey: ["admin-sessions"] }) },
  })

  return (
    <div>
      {!embedded && <PageHeader title="Сессии" description="Активные и отозванные сессии администраторов" icon={Clock} />}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm text-muted-foreground">{showHistory ? "История сессий" : "Только активные сессии"}</div>
        <Button size="sm" variant={showHistory ? "default" : "outline"} aria-pressed={showHistory} onClick={toggleHistory}>{showHistory ? "Скрыть историю" : "Показать историю"}</Button>
      </div>
      <Card className="overflow-hidden py-0">
      <CardContent className="p-0">
        {isLoading ? <div className="p-4"><Skeleton className="h-64" /></div> : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>Админ</TableHead><TableHead>Вход и устройство</TableHead><TableHead>Создана</TableHead><TableHead>Истекает</TableHead><TableHead>Статус</TableHead>{data?.can_edit && <TableHead />}</TableRow>
            </TableHeader>
            <TableBody>
              {data?.sessions.map((s) => (
                <TableRow key={s.id}>
                  <TableCell>{s.admin_name ?? `#${s.admin_id}`}{s.is_current && <Badge className="ml-2" variant="outline">это вы</Badge>}</TableCell>
                  <TableCell><div className="text-sm">{authLabel(s.auth_type)}</div><div className="max-w-xs truncate text-xs text-muted-foreground" title={s.user_agent ?? undefined}>{s.last_seen_ip ?? "IP скрыт"} · {formatBrowserDate(s.last_seen_at)}</div></TableCell>
                  <TableCell className="text-sm">{fmtDate(s.created_at)}</TableCell>
                  <TableCell className="text-sm">{fmtDate(s.expires_at)}</TableCell>
                  <TableCell><Badge variant={s.revoked ? "secondary" : "default"}>{s.revoked ? "Отозвана" : "Активна"}</Badge></TableCell>
                  {data.can_edit && (
                    <TableCell className="text-right">
                      {!s.revoked && <Button size="sm" variant="destructive" onClick={() => revoke.mutate(s.id)}>Отозвать</Button>}
                    </TableCell>
                  )}
                </TableRow>
              ))}
              {!data?.sessions.length && <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">{showHistory ? "Сессий нет" : "Активных сессий нет"}</TableCell></TableRow>}
            </TableBody>
          </Table>
        )}
        </CardContent>
      </Card>
    </div>
  )
}

function authLabel(value: string | null) {
  if (value === "discord_oauth") return "Discord"
  if (value === "login_key" || value === "slogin") return "slogin"
  return "браузер"
}
