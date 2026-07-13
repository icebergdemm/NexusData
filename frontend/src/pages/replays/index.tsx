import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { PlayCircle } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface ReplayRow {
  id: number; server_name: string | null; submarine_name: string | null; level_name: string | null
  file_size: number | null; has_voice: boolean; recorded_at: string | null; uploaded_at: string | null
}
interface ReplaysResponse {
  replays: ReplayRow[]; total: number; page: number; total_pages: number
  retention_days: number; can_delete: boolean; is_superuser: boolean
}

function fmtSize(n: number | null): string {
  if (!n) return "0 B"
  const units = ["B", "KB", "MB", "GB"]
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

export function ReplaysListPage() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const queryClient = useQueryClient()
  const queryKey = ["replays", page]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<ReplaysResponse>(`/api/v1/admin/replays?page=${page}`),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/v1/admin/replays/${id}`),
    onSuccess: () => { toast.success("Реплей удалён"); queryClient.invalidateQueries({ queryKey: ["replays"] }) },
  })

  return (
    <div>
      <PageHeader
        title="Реплеи раундов"
        description="Записи прошедших раундов для просмотра"
        icon={PlayCircle}
        actions={data ? <Badge variant="secondary">{data.total} всего</Badge> : undefined}
      />
      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? <Skeleton className="m-4 h-64" /> : (
            <Table>
              <TableHeader>
                <TableRow><TableHead>Корабль</TableHead><TableHead>Сервер</TableHead><TableHead>Размер</TableHead><TableHead>Голос</TableHead><TableHead>Загружен</TableHead>{data?.can_delete && <TableHead />}</TableRow>
              </TableHeader>
              <TableBody>
                {data?.replays.map((r) => (
                  <TableRow key={r.id} className="cursor-pointer" onClick={() => navigate(`/replays/${r.id}`)}>
                    <TableCell className="font-medium">{r.submarine_name ?? "Раунд"} #{r.id}{r.level_name && <span className="text-xs text-muted-foreground"> · {r.level_name}</span>}</TableCell>
                    <TableCell><Badge variant="secondary">{r.server_name ?? "—"}</Badge></TableCell>
                    <TableCell>{fmtSize(r.file_size)}</TableCell>
                    <TableCell>{r.has_voice ? "🔊" : "—"}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{r.uploaded_at ? new Date(r.uploaded_at).toLocaleString("ru-RU") : "—"}</TableCell>
                    {data?.can_delete && (
                      <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                        <Button size="sm" variant="destructive" onClick={() => deleteMutation.mutate(r.id)}>Удалить</Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
                {!data?.replays.length && <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">Реплеев нет</TableCell></TableRow>}
              </TableBody>
            </Table>
          )}
          {data && data.total_pages > 1 && (
            <div className="flex items-center justify-center gap-2 border-t bg-muted/30 py-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Назад</Button>
              <span className="text-sm text-muted-foreground">Страница {data.page} из {data.total_pages}</span>
              <Button variant="outline" size="sm" disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>Вперёд</Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
