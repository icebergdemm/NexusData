import { useState } from "react"
import { useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"

const ROLE_LABELS: Record<string, string> = {
  captain: "Капитан", mechanic: "Механик", medic: "Врач", security: "СБ", engineer: "Инженер",
}

interface SnapshotViewResponse {
  meta: { title: string; created_at: string; created_by?: string; total_players: number }
  leaderboard: { steam_id: string; player_name: string; rating: number; cumulative_points: number }[]
  selected_role: string
  roles: string[]
}

export function RatingSnapshotViewPage() {
  const { filename } = useParams<{ filename: string }>()
  const [role, setRole] = useState("captain")

  const { data, isLoading } = useQuery({
    queryKey: ["rating-snapshot", filename, role],
    queryFn: () => api.get<SnapshotViewResponse>(`/api/v1/admin/ratings/snapshot/${filename}/view?role=${role}`),
  })

  if (isLoading || !data) return <Skeleton className="h-96" />

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle>{data.meta.title}</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {data.meta.total_players} игроков · {data.meta.created_at ? new Date(data.meta.created_at).toLocaleString("ru-RU") : ""}
          {data.meta.created_by && ` · создал ${data.meta.created_by}`}
        </CardContent>
      </Card>

      <Tabs value={role} onValueChange={setRole}>
        <TabsList>
          {data.roles.map((r) => <TabsTrigger key={r} value={r}>{ROLE_LABELS[r] ?? r}</TabsTrigger>)}
        </TabsList>
      </Tabs>

      <Card>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>#</TableHead><TableHead>Игрок</TableHead><TableHead>Рейтинг</TableHead><TableHead>Очки</TableHead></TableRow></TableHeader>
            <TableBody>
              {data.leaderboard.map((e, i) => (
                <TableRow key={e.steam_id}>
                  <TableCell>{i + 1}</TableCell>
                  <TableCell>{e.player_name}</TableCell>
                  <TableCell className="font-semibold">{e.rating}</TableCell>
                  <TableCell>{e.cumulative_points}</TableCell>
                </TableRow>
              ))}
              {!data.leaderboard.length && <TableRow><TableCell colSpan={4} className="text-center text-muted-foreground">Нет данных</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
