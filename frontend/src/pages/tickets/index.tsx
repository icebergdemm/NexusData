import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { AlertCircle, Inbox, RefreshCw, Search, Ticket } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import type { TicketRow, TicketsListResponse } from "./types"
import { TicketInboxList, TicketInboxSkeleton } from "./components/ticket-inbox"
import { filterTickets, normalizeTicketListStatus } from "./components/ticket-utils"
import { TicketActionsMenu } from "./TicketActionsMenu"
import { TicketMemberDialog } from "./TicketMemberDialog"
import { TicketMemberRemoveDialog } from "./TicketMemberRemoveDialog"

export function TicketsListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const status = normalizeTicketListStatus(searchParams.get("status"))
  const [search, setSearch] = useState("")
  const [deleteTicket, setDeleteTicket] = useState<TicketRow | null>(null)
  const [restoreTicket, setRestoreTicket] = useState<TicketRow | null>(null)
  const [memberTicketId, setMemberTicketId] = useState<number | null>(null)
  const [removeMemberTicketId, setRemoveMemberTicketId] = useState<number | null>(null)
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ["tickets", status],
    queryFn: () => api.get<TicketsListResponse>(`/api/v1/admin/tickets?status=${status}`),
    placeholderData: (previous) => previous,
    // Closed/deleted tickets are immutable in normal operation. Polling the
    // archive every 10 seconds kept rerunning its heaviest DB aggregates even
    // while the user was only reading old tickets.
    refetchInterval: status === "open" ? 10_000 : false,
    staleTime: status === "open" ? 5_000 : 5 * 60_000,
  })

  const tickets = query.data?.tickets
  const filteredTickets = useMemo(() => filterTickets(tickets ?? [], search, "all"), [tickets, search])
  const waitingCount = tickets?.filter((ticket) => ticket.awaiting_admin && ticket.status !== "closed").length ?? 0
  const canAssign = query.data?.permissions?.can_assign ?? query.data?.can_assign ?? false
  const canClose = query.data?.permissions?.can_close ?? query.data?.can_edit ?? false
  const canDelete = query.data?.permissions?.can_delete ?? query.data?.can_delete ?? false
  const hasTicketActions = canAssign || canClose || canDelete

  const closeMutation = useMutation({
    mutationFn: (ticket: TicketRow) => api.post(`/api/v1/admin/tickets/${ticket.id}/close`),
    onSuccess: () => {
      toast.success("Тикет закрыт")
      void queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: (ticket: TicketRow) => api.post(`/api/v1/admin/tickets/${ticket.id}/delete`),
    onSuccess: () => {
      toast.success("Тикет перемещён в удалённые")
      setDeleteTicket(null)
      void queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const restoreMutation = useMutation({
    mutationFn: (ticket: TicketRow) => api.post(`/api/v1/admin/tickets/${ticket.id}/restore`),
    onSuccess: () => {
      toast.success("Тикет восстановлен")
      setRestoreTicket(null)
      void queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const handleStatusChange = (value: string) => {
    const nextStatus = normalizeTicketListStatus(value)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set("status", nextStatus)
      return next
    })
  }

  return (
    <div className="min-w-0">
      <PageHeader
        title="Тикеты"
        description="Обращения игроков, переписка и сохранённые материалы"
        icon={Ticket}
        actions={
          <Button type="button" variant="outline" size="sm" onClick={() => query.refetch()} disabled={query.isFetching}>
            <RefreshCw data-icon="inline-start" className={query.isFetching ? "animate-spin" : undefined} />
            Обновить
          </Button>
        }
      />

      <section aria-label="Список тикетов" className="overflow-hidden border bg-background">
        <div className="flex flex-col gap-3 border-b p-3 sm:p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <Tabs value={status} onValueChange={handleStatusChange}>
              <TabsList variant="line" className="grid w-full grid-cols-4 sm:w-auto">
                <TabsTrigger value="open">Открытые</TabsTrigger>
                <TabsTrigger value="closed">Архив</TabsTrigger>
                <TabsTrigger value="deleted">Удалённые</TabsTrigger>
                <TabsTrigger value="all">Все</TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="relative w-full lg:max-w-sm">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск по номеру, теме или участнику" className="pl-9" aria-label="Поиск тикетов" />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5"><Inbox className="size-3.5" />Показано: <strong className="font-mono text-foreground">{filteredTickets.length}</strong></span>
            {waitingCount ? <Badge variant="destructive"><AlertCircle />Ожидают ответа: {waitingCount}</Badge> : null}
            {query.isFetching && !query.isLoading ? <span className="font-mono text-[10px]">Синхронизация…</span> : null}
          </div>
        </div>

        {query.isLoading ? <TicketInboxSkeleton /> : query.isError ? (
          <div className="flex min-h-72 flex-col items-center justify-center gap-3 px-6 text-center">
            <AlertCircle className="size-8 text-destructive" aria-hidden="true" />
            <div>
              <h2 className="text-sm font-semibold">Не удалось загрузить тикеты</h2>
              <p className="mt-1 text-xs text-muted-foreground">{query.error instanceof Error ? query.error.message : "Попробуйте ещё раз."}</p>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={() => query.refetch()}>Повторить</Button>
          </div>
        ) : (
          <TicketInboxList
            tickets={filteredTickets}
            ticketTypes={query.data?.ticket_config.types ?? {}}
            returnStatus={status}
            renderActions={hasTicketActions ? (ticket) => {
              const isDeleted = ticket.deleted_at !== null
              const isOpen = !isDeleted && ticket.status !== "closed"
              const hasActions = (isOpen && (canAssign || canClose)) || (!isOpen && canDelete)
              if (!hasActions) return null
              return (
                <TicketActionsMenu
                  ticket={ticket}
                  canAssign={canAssign}
                  canEdit={canClose}
                  canDelete={canDelete}
                  disabled={closeMutation.isPending || deleteMutation.isPending || restoreMutation.isPending}
                  onAddMember={() => setMemberTicketId(ticket.id)}
                  onRemoveMember={() => setRemoveMemberTicketId(ticket.id)}
                  onClose={() => closeMutation.mutate(ticket)}
                  onDelete={() => setDeleteTicket(ticket)}
                  onRestore={() => setRestoreTicket(ticket)}
                />
              )
            } : undefined}
          />
        )}
      </section>

      <ConfirmDialog
        open={deleteTicket !== null}
        onOpenChange={(open) => !deleteMutation.isPending && !open && setDeleteTicket(null)}
        title={`Удалить тикет #${deleteTicket?.id ?? ""}?`}
        description={deleteTicket ? `«${deleteTicket.title}» будет убран из обычных списков. История сохранится на 30 дней, после чего тикет и сообщения будут удалены окончательно.` : undefined}
        confirmLabel={deleteMutation.isPending ? "Удаление..." : "Удалить тикет"}
        confirmDisabled={deleteMutation.isPending}
        onConfirm={() => deleteTicket && deleteMutation.mutate(deleteTicket)}
      />

      <ConfirmDialog
        open={restoreTicket !== null}
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
