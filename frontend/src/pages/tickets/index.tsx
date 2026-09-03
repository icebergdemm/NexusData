import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Ticket, ChevronRight, RotateCcw, Trash2, UserMinus, UserPlus } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { AppContextMenu } from "@/components/layout/AppContextMenu"
import type { ContextMenuAction } from "@/components/layout/AppContextMenu"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import type { TicketsListResponse } from "./types"
import type { TicketRow } from "./types"
import { TicketMemberDialog } from "./TicketMemberDialog"
import { TicketMemberRemoveDialog } from "./TicketMemberRemoveDialog"

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

export function TicketsListPage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState("open")
  const [deleteTicket, setDeleteTicket] = useState<TicketRow | null>(null)
  const [restoreTicket, setRestoreTicket] = useState<TicketRow | null>(null)
  const [memberTicketId, setMemberTicketId] = useState<number | null>(null)
  const [removeMemberTicketId, setRemoveMemberTicketId] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ["tickets", status],
    queryFn: () => api.get<TicketsListResponse>(`/api/v1/admin/tickets?status=${status}`),
  })

  const deleteMutation = useMutation({
    mutationFn: (ticket: TicketRow) => api.post(`/api/v1/admin/tickets/${ticket.id}/delete`),
    onSuccess: () => {
      toast.success("Тикет перемещён в удалённые")
      setDeleteTicket(null)
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const restoreMutation = useMutation({
    mutationFn: (ticket: TicketRow) => api.post(`/api/v1/admin/tickets/${ticket.id}/restore`),
    onSuccess: () => {
      toast.success("Тикет восстановлен")
      setRestoreTicket(null)
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  return (
    <div>
      <PageHeader title="Тикеты" description="Обращения игроков через систему поддержки" icon={Ticket} />

      <Tabs value={status} onValueChange={setStatus} className="mb-4">
        <TabsList>
          <TabsTrigger value="open">Открытые</TabsTrigger>
          <TabsTrigger value="closed">Закрытые</TabsTrigger>
          <TabsTrigger value="all">Все</TabsTrigger>
          <TabsTrigger value="deleted">Удалённые</TabsTrigger>
        </TabsList>
      </Tabs>

      {isLoading ? (
        <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : (
        <div className="space-y-2">
          {data?.tickets.map((t) => {
            const meta = data.ticket_config.types[t.ticket_type]
            const actions: ContextMenuAction[] = []
            if (!t.deleted_at && t.status !== "closed" && data.can_assign) {
              actions.push({ label: "Добавить участника", icon: UserPlus, onSelect: () => setMemberTicketId(t.id) })
              actions.push({ label: "Удалить участника", icon: UserMinus, destructive: true, onSelect: () => setRemoveMemberTicketId(t.id) })
            }
            if (!t.deleted_at && t.status === "closed" && data.can_delete) {
              actions.push({ label: "Удалить тикет", icon: Trash2, destructive: true, separatorBefore: true, onSelect: () => setDeleteTicket(t) })
            }
            if (t.deleted_at && data.can_delete) {
              actions.push({ label: "Восстановить тикет", icon: RotateCcw, onSelect: () => setRestoreTicket(t) })
            }
            return (
              <AppContextMenu key={t.id} actions={actions}>
                <Card className="cursor-pointer overflow-hidden py-0 transition-all hover:border-primary/50" onClick={() => navigate(`/tickets/${t.id}`)}>
                  <CardContent className="flex items-center gap-3 py-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-xl"><PlatformIcon value={meta?.emoji ?? "🎫"} fallback={Ticket} className="size-5" /></div>
                    <div className="flex-1">
                      <div className="flex items-center gap-1.5 font-medium"><span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">#{t.id}</span> {t.title}</div>
                      <div className="text-xs text-muted-foreground">
                        {meta?.name ?? t.ticket_type} · {t.creator_display_name || t.creator_username} · {fmtDate(t.created_at)}
                        {t.status === "closed" && t.closed_by && <> · закрыл(а) {t.closed_by}</>}
                        {t.deleted_at && <> · удалил(а) {t.deleted_by || "администратор"}, {fmtDate(t.deleted_at)}</>}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {t.awaiting_admin && t.status !== "closed" && !t.deleted_at && <Badge variant="destructive">Ожидает ответа</Badge>}
                      <Badge variant={t.deleted_at ? "destructive" : t.status === "closed" ? "secondary" : "default"}>{t.deleted_at ? "Удалён" : t.status}</Badge>
                      <ChevronRight className="h-4 w-4 text-muted-foreground" />
                    </div>
                  </CardContent>
                </Card>
              </AppContextMenu>
            )
          })}
          {!data?.tickets.length && (
            <div className="py-12 text-center text-muted-foreground">Тикетов нет</div>
          )}
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTicket}
        onOpenChange={(open) => !deleteMutation.isPending && !open && setDeleteTicket(null)}
        title={`Удалить тикет #${deleteTicket?.id ?? ""}?`}
        description={deleteTicket ? `«${deleteTicket.title}» будет убран из обычных списков. История сохранится на 30 дней, после чего тикет и сообщения будут удалены окончательно.` : undefined}
        confirmLabel={deleteMutation.isPending ? "Удаление..." : "Удалить тикет"}
        confirmDisabled={deleteMutation.isPending}
        onConfirm={() => deleteTicket && deleteMutation.mutate(deleteTicket)}
      />

      <ConfirmDialog
        open={!!restoreTicket}
        onOpenChange={(open) => !restoreMutation.isPending && !open && setRestoreTicket(null)}
        title={`Восстановить тикет #${restoreTicket?.id ?? ""}?`}
        description={restoreTicket ? `«${restoreTicket.title}» снова появится в списке закрытых тикетов. Discord-канал не восстанавливается.` : undefined}
        confirmLabel={restoreMutation.isPending ? "Восстановление..." : "Восстановить тикет"}
        confirmDisabled={restoreMutation.isPending}
        onConfirm={() => restoreTicket && restoreMutation.mutate(restoreTicket)}
      />

      <TicketMemberDialog
        open={memberTicketId !== null}
        ticketId={memberTicketId}
        onOpenChange={(open) => !open && setMemberTicketId(null)}
      />

      <TicketMemberRemoveDialog
        open={removeMemberTicketId !== null}
        ticketId={removeMemberTicketId}
        onOpenChange={(open) => !open && setRemoveMemberTicketId(null)}
      />
    </div>
  )
}
