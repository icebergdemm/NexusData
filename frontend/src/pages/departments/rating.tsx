import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Star, Plus, Trash2, Trophy } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { departmentsApi, type DepartmentDetail, type RatingEntry } from "./api"

export function AddRatingEntryDialog({ open, onOpenChange, department, presetMember }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  department: DepartmentDetail
  /** When set (e.g. opened from a specific Roster row), skips the member
   * picker and targets this admin directly. */
  presetMember?: { admin_id: number; admin_name: string | null }
}) {
  const queryClient = useQueryClient()
  const [adminId, setAdminId] = useState("")
  const [delta, setDelta] = useState("")
  const [reason, setReason] = useState("")
  const [category, setCategory] = useState("")

  useEffect(() => {
    if (open && presetMember) setAdminId(String(presetMember.admin_id))
  }, [open, presetMember])

  const { data: membersData } = useQuery({
    queryKey: ["department-members", department.id],
    queryFn: () => departmentsApi.members.list(department.id),
    enabled: open && !presetMember,
  })

  const addMutation = useMutation({
    mutationFn: () => departmentsApi.rating.addEntry(department.id, {
      admin_id: Number(adminId), delta: Number(delta), reason: reason.trim() || undefined, category: category.trim() || undefined,
    }),
    onSuccess: () => {
      toast.success("Запись добавлена")
      queryClient.invalidateQueries({ queryKey: ["department-rating", department.id] })
      queryClient.invalidateQueries({ queryKey: ["department-rating-entries", department.id] })
      onOpenChange(false)
      setAdminId(""); setDelta(""); setReason(""); setCategory("")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader><DialogTitle>Новая запись рейтинга</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">Сотрудник</Label>
            {presetMember ? (
              <div className="rounded-md border px-3 py-2 text-sm">{presetMember.admin_name ?? `#${presetMember.admin_id}`}</div>
            ) : (
              <Select value={adminId} onValueChange={setAdminId}>
                <SelectTrigger><SelectValue placeholder="Выберите сотрудника" /></SelectTrigger>
                <SelectContent>
                  {membersData?.members.map((m) => (
                    <SelectItem key={m.admin_id} value={String(m.admin_id)}>{m.forced_display_name ?? m.admin_name ?? `#${m.admin_id}`}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div>
            <Label className="mb-1.5 block">Баллы (можно отрицательные)</Label>
            <Input type="number" value={delta} onChange={(e) => setDelta(e.target.value)} placeholder="10" />
          </div>
          <div>
            <Label className="mb-1.5 block">Категория</Label>
            <Input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="Необязательно" />
          </div>
          <div>
            <Label className="mb-1.5 block">Причина</Label>
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="За что начислено/списано" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button
            disabled={!adminId || !delta || Number(delta) === 0 || addMutation.isPending}
            onClick={() => addMutation.mutate()}
          >
            Добавить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function RatingTab({ department, canEdit }: { department: DepartmentDetail; canEdit: boolean }) {
  const queryClient = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<RatingEntry | null>(null)
  const canView = department.my_access.rating?.view

  const { data: leaderboard, isLoading: leaderboardLoading } = useQuery({
    queryKey: ["department-rating", department.id],
    queryFn: () => departmentsApi.rating.leaderboard(department.id),
    enabled: canView,
  })

  const { data: entriesData, isLoading: entriesLoading } = useQuery({
    queryKey: ["department-rating-entries", department.id],
    queryFn: () => departmentsApi.rating.entries(department.id),
    enabled: canView,
  })

  const deleteMutation = useMutation({
    mutationFn: (entryId: number) => departmentsApi.rating.deleteEntry(department.id, entryId),
    onSuccess: () => {
      toast.success("Запись удалена")
      queryClient.invalidateQueries({ queryKey: ["department-rating", department.id] })
      queryClient.invalidateQueries({ queryKey: ["department-rating-entries", department.id] })
      setDeleteTarget(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!canView) {
    return <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">Недостаточно прав для просмотра рейтинга отдела.</CardContent></Card>
  }

  return (
    <div className="space-y-4">
      {canEdit && (
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setAddOpen(true)}><Plus className="size-4" /> Начислить баллы</Button>
        </div>
      )}

      <Card>
        <CardContent className="space-y-2 p-4">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground"><Trophy className="size-3.5" /> Таблица лидеров</div>
          {leaderboardLoading ? (
            <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-9" />)}</div>
          ) : !leaderboard?.leaderboard.length ? (
            <p className="text-sm text-muted-foreground">В отделе пока никого нет.</p>
          ) : (
            <div className="flex flex-col divide-y">
              {leaderboard.leaderboard.map((r, i) => (
                <div key={r.admin_id} className="flex items-center justify-between py-2 text-sm">
                  <span className="flex items-center gap-2">
                    <Badge variant="outline" className="w-6 justify-center text-[10px]">{i + 1}</Badge>
                    {r.forced_display_name ?? r.admin_name ?? `#${r.admin_id}`}
                  </span>
                  <span className={`font-semibold ${r.total > 0 ? "text-success" : r.total < 0 ? "text-destructive" : "text-muted-foreground"}`}>
                    {r.total > 0 ? "+" : ""}{r.total}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-2 p-4">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground"><Star className="size-3.5" /> История начислений</div>
          {entriesLoading ? (
            <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10" />)}</div>
          ) : !entriesData?.entries.length ? (
            <p className="text-sm text-muted-foreground">Записей пока нет.</p>
          ) : (
            <div className="flex flex-col divide-y">
              {entriesData.entries.map((e) => (
                <div key={e.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{e.forced_display_name ?? e.admin_name ?? `#${e.admin_id}`}</span>
                      <span className={e.delta > 0 ? "text-success" : "text-destructive"}>{e.delta > 0 ? "+" : ""}{e.delta}</span>
                      {e.category && <Badge variant="secondary" className="text-[10px]">{e.category}</Badge>}
                    </div>
                    {e.reason && <div className="truncate text-xs text-muted-foreground">{e.reason}</div>}
                  </div>
                  {canEdit && (
                    <Button size="icon-xs" variant="ghost" className="shrink-0 text-destructive" onClick={() => setDeleteTarget(e)}>
                      <Trash2 className="size-3.5" />
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <AddRatingEntryDialog open={addOpen} onOpenChange={setAddOpen} department={department} />
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="Удалить запись рейтинга?"
        description={deleteTarget?.reason ?? undefined}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
      />
    </div>
  )
}
