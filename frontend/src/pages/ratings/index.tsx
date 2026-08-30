import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Star, Gift, RotateCw, Camera, Medal } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Skeleton } from "@/components/ui/skeleton"
import { Link } from "react-router-dom"
import { PageHeader } from "@/components/layout/PageHeader"
import { SearchInput } from "@/components/layout/SearchInput"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { api } from "@/lib/api"
import { RatingConfigSection } from "./config-section"

const ROLE_LABELS: Record<string, string> = {
  captain: "Капитан", mechanic: "Механик", medic: "Врач", security: "СБ", engineer: "Инженер",
}

interface LeaderboardEntry {
  rank: number; steam_id: string; player_name: string; player_id: number | null
  rating: number; cumulative_points: number; last_activity: string | null
}
interface Snapshot { filename: string; title: string; created_at: string; total_players: number }
interface RatingsResponse {
  leaderboard: LeaderboardEntry[]
  selected_role: string
  roles: string[]
  search_result: { player_name: string; player_steamid: string; ratings: Record<string, { rating: number; points: number }> } | null
  snapshots: Snapshot[]
  can_edit: boolean
  is_superuser: boolean
}

export function RatingsPage() {
  const [role, setRole] = useState("captain")
  const [searchInput, setSearchInput] = useState("")
  const [search, setSearch] = useState("")
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ["ratings", role, search],
    queryFn: () => api.get<RatingsResponse>(`/api/v1/admin/ratings?${new URLSearchParams({ role, ...(search ? { search } : {}) })}`),
  })

  const generateSnapshot = useMutation({
    mutationFn: () => api.post("/api/v1/admin/ratings/snapshot/generate", { title: "" }),
    onSuccess: () => { toast.success("Снапшот создан"); queryClient.invalidateQueries({ queryKey: ["ratings"] }) },
  })

  const deleteSnapshot = useMutation({
    mutationFn: (filename: string) => api.delete(`/api/v1/admin/ratings/snapshot/${filename}`),
    onSuccess: () => { toast.success("Снапшот удалён"); queryClient.invalidateQueries({ queryKey: ["ratings"] }) },
  })

  const restoreSnapshot = useMutation({
    mutationFn: (filename: string) => api.post<{ restored: number }>(`/api/v1/admin/ratings/snapshot/${filename}/restore`),
    onSuccess: (r) => { toast.success(`Восстановлено ${r.restored} записей`); queryClient.invalidateQueries({ queryKey: ["ratings"] }) },
  })

  const recalcAll = useMutation({
    mutationFn: () => api.post("/api/v1/admin/ratings/recalculate-all"),
    onSuccess: () => toast.success("Пересчёт запущен в фоне"),
  })

  return (
    <div>
      <PageHeader
        title="Рейтинг"
        description="Лидерборды по ролям и система наград"
        icon={Star}
        actions={<>
          <Button variant="outline" asChild><Link to="/ratings/rewards"><Gift className="h-4 w-4" /> Награды</Link></Button>
          {data?.can_edit && (
            <>
              <Button variant="outline" onClick={() => recalcAll.mutate()}><RotateCw className="h-4 w-4" /> Пересчитать всё</Button>
              <Button onClick={() => generateSnapshot.mutate()}><Camera className="h-4 w-4" /> Создать снапшот</Button>
            </>
          )}
        </>}
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Tabs value={role} onValueChange={setRole}>
          <TabsList>
            {(data?.roles ?? ["captain", "mechanic", "medic", "security", "engineer"]).map((r) => (
              <TabsTrigger key={r} value={r}>{ROLE_LABELS[r] ?? r}</TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <SearchInput
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && setSearch(searchInput.trim())}
          placeholder="Поиск игрока..."
          className="max-w-xs"
        />
        <Button variant="secondary" onClick={() => setSearch(searchInput.trim())}>Найти</Button>
      </div>

      {data?.is_superuser && <RatingConfigSection />}

      {data?.search_result && (
        <Card className="mb-4">
          <CardHeader><CardTitle className="text-sm">{data.search_result.player_name} - все роли</CardTitle></CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {Object.entries(data.search_result.ratings).map(([r, v]) => (
              <Badge key={r} variant="secondary">{ROLE_LABELS[r] ?? r}: {v.rating} ({v.points} pts)</Badge>
            ))}
          </CardContent>
        </Card>
      )}

      <Card className="mb-4 overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Лидерборд - {ROLE_LABELS[role] ?? role}</CardTitle></CardHeader>
        <CardContent className="p-0">
          {isLoading ? <div className="p-4"><Skeleton className="h-64" /></div> : (
            <Table>
              <TableHeader>
                <TableRow><TableHead className="w-12">#</TableHead><TableHead>Игрок</TableHead><TableHead>Рейтинг</TableHead><TableHead>Очки</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {data?.leaderboard.map((e) => (
                  <TableRow key={e.steam_id} className={e.rank <= 3 ? "bg-warning/5" : ""}>
                    <TableCell className="text-base">{e.rank <= 3 ? <PlatformIcon value={["🥇", "🥈", "🥉"][e.rank - 1]} fallback={Medal} /> : <span className="text-muted-foreground">{e.rank}</span>}</TableCell>
                    <TableCell className="font-medium">{e.player_name}</TableCell>
                    <TableCell className="font-semibold text-primary">{e.rating}</TableCell>
                    <TableCell className="text-muted-foreground">{e.cumulative_points}</TableCell>
                  </TableRow>
                ))}
                {!data?.leaderboard.length && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">Нет данных</TableCell></TableRow>}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card className="overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Снапшоты</CardTitle></CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow><TableHead>Название</TableHead><TableHead>Игроков</TableHead><TableHead>Дата</TableHead>{data?.can_edit && <TableHead className="text-right">Действия</TableHead>}</TableRow>
            </TableHeader>
            <TableBody>
              {data?.snapshots.map((s) => (
                <TableRow key={s.filename}>
                  <TableCell><Link to={`/ratings/snapshots/${s.filename}`} className="font-medium text-primary underline-offset-2 hover:underline">{s.title}</Link></TableCell>
                  <TableCell>{s.total_players}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{s.created_at ? new Date(s.created_at).toLocaleString("ru-RU") : "-"}</TableCell>
                  {data.can_edit && (
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1.5">
                        <Button size="sm" variant="outline" onClick={() => setRestoreTarget(s.filename)}>Восстановить</Button>
                        <Button size="sm" variant="destructive" onClick={() => setDeleteTarget(s.filename)}>Удалить</Button>
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
              {!data?.snapshots.length && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">Снапшотов нет</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={restoreTarget != null}
        onOpenChange={(o) => !o && setRestoreTarget(null)}
        title="Восстановить рейтинг из снапшота?"
        description="Текущие рейтинги игроков будут перезаписаны данными из этого снапшота."
        confirmLabel="Восстановить"
        onConfirm={() => { if (restoreTarget) restoreSnapshot.mutate(restoreTarget); setRestoreTarget(null) }}
      />
      <ConfirmDialog
        open={deleteTarget != null}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="Удалить снапшот?"
        description="Это действие необратимо."
        onConfirm={() => { if (deleteTarget) deleteSnapshot.mutate(deleteTarget); setDeleteTarget(null) }}
      />
    </div>
  )
}
