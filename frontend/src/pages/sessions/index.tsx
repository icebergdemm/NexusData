import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Clock } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface SessionRow {
  id: number; admin_id: number; admin_name: string | null; token_preview: string
  created_at: string | null; expires_at: string | null; revoked: boolean
}
interface SessionsResponse { sessions: SessionRow[]; can_edit: boolean }

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "Бессрочно"
}

export function SessionsPage() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ["admin-sessions"],
    queryFn: () => api.get<SessionsResponse>("/api/v1/admin/sessions"),
  })

  const revoke = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/sessions/${id}/revoke`),
    onSuccess: () => { toast.success("Сессия отозвана"); queryClient.invalidateQueries({ queryKey: ["admin-sessions"] }) },
  })

  return (
    <div>
      <PageHeader title="Сессии" description="Активные и отозванные сессии администраторов" icon={Clock} />
      <Card className="overflow-hidden py-0">
      <CardContent className="p-0">
        {isLoading ? <div className="p-4"><Skeleton className="h-64" /></div> : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>Админ</TableHead><TableHead>Токен</TableHead><TableHead>Создана</TableHead><TableHead>Истекает</TableHead><TableHead>Статус</TableHead>{data?.can_edit && <TableHead />}</TableRow>
            </TableHeader>
            <TableBody>
              {data?.sessions.map((s) => (
                <TableRow key={s.id}>
                  <TableCell>{s.admin_name ?? `#${s.admin_id}`}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{s.token_preview}</TableCell>
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
              {!data?.sessions.length && <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">Сессий нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        )}
        </CardContent>
      </Card>
    </div>
  )
}
