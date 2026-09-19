import { useEffect, useMemo, useRef, useState } from "react"
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import { AlertCircle, Inbox, RefreshCw, Search, Settings2, Ticket } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PageHeader } from "@/components/layout/PageHeader"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { useInfiniteScroll } from "@/hooks/useInfiniteScroll"
import { api } from "@/lib/api"
import type { TicketRow, TicketsListResponse } from "./types"
import { TicketInboxList, TicketInboxSkeleton } from "./components/ticket-inbox"
import { normalizeTicketListStatus } from "./components/ticket-utils"
import { TicketActionsMenu } from "./TicketActionsMenu"
import { TicketMemberDialog } from "./TicketMemberDialog"
import { TicketMemberRemoveDialog } from "./TicketMemberRemoveDialog"
import { TicketSettings } from "./TicketSettings"

export function TicketsListPage() {
  const [showSettings, setShowSettings] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const status = normalizeTicketListStatus(searchParams.get("status"))
  const [search, setSearch] = useState("")
  const debouncedSearch = useDebouncedValue(search.trim(), 300)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [deleteTicket, setDeleteTicket] = useState<TicketRow | null>(null)
  const [restoreTicket, setRestoreTicket] = useState<TicketRow | null>(null)
  const [memberTicketId, setMemberTicketId] = useState<number | null>(null)
  const [removeMemberTicketId, setRemoveMemberTicketId] = useState<number | null>(null)
  const queryClient = useQueryClient()
  const query = useInfiniteQuery({
    queryKey: ["tickets", status, debouncedSearch],
    queryFn: ({ pageParam = null as string | null }) => {
      const params = new URLSearchParams({ status, limit: "30" })
      if (pageParam) params.set("cursor", pageParam as string)
      if (debouncedSearch) params.set("search", debouncedSearch)
      return api.get<TicketsListResponse>(`/api/v1/admin/tickets?${params}`)
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    // Closed/deleted tickets are immutable in normal operation. Polling the
    // archive every 10 seconds kept rerunning its heaviest DB aggregates even
    // while the user was only reading old tickets.
    refetchInterval: (activeQuery) => {
      if (status !== "open") return false
      const pageCount = (activeQuery.state.data as { pages?: unknown[] } | undefined)?.pages?.length ?? 0
      // Refetching an infinite query refreshes every loaded page. Keep the
      // live 10-second refresh while only the first page is visible, then let
      // manual refresh handle deeper browsing instead of multiplying traffic.
      return pageCount <= 1 ? 10_000 : false
    },
    staleTime: status === "open" ? 5_000 : 5 * 60_000,
  })

  const firstPage = query.data?.pages[0]
  const tickets = useMemo(
    () => query.data?.pages.flatMap((page) => page.tickets) ?? [],
    [query.data?.pages],
  )
  const waitingCount = tickets.filter((ticket) => ticket.awaiting_admin && ticket.status !== "closed").length
  const canAssign = firstPage?.permissions?.can_assign ?? firstPage?.can_assign ?? false
  const canClose = firstPage?.permissions?.can_close ?? firstPage?.can_edit ?? false
  const canDelete = firstPage?.permissions?.can_delete ?? firstPage?.can_delete ?? false
  const hasTicketActions = canAssign || canClose || canDelete
  const sentinelRef = useInfiniteScroll(
    () => { if (query.hasNextPage) void query.fetchNextPage() },
    Boolean(query.hasNextPage),
    query.isFetchingNextPage,
  )

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [status, debouncedSearch])

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
    <div className="-m-3 flex h-[calc(100dvh-3.5rem)] min-w-0 flex-col overflow-hidden sm:-m-6">
      <div className="shrink-0 px-3 pt-3 sm:px-6 sm:pt-6">
        <PageHeader
          title="Тикеты"
          description="Обращения игроков, переписка и сохранённые материалы"
          icon={Ticket}
          actions={
            <>
              <Button type="button" variant={showSettings ? "default" : "outline"} size="sm" onClick={() => setShowSettings((value) => !value)}>
                <Settings2 data-icon="inline-start" />
                {showSettings ? "Очередь тикетов" : "Настройка Discord"}
              </Button>
              {!showSettings && <Button type="button" variant="outline" size="sm" onClick={() => query.refetch()} disabled={query.isFetching}>
                <RefreshCw data-icon="inline-start" className={query.isFetching ? "animate-spin" : undefined} />
                Обновить
              </Button>}
            </>
          }
        />
      </div>

      {showSettings ? <div className="mx-3 mb-3 min-h-0 flex-1 overflow-y-auto sm:mx-6 sm:mb-6"><TicketSettings /></div> : <section aria-label="Список тикетов" className="mx-3 mb-3 flex min-h-0 flex-1 flex-col overflow-hidden border bg-background sm:mx-6 sm:mb-6">
        <div className="flex shrink-0 flex-col gap-3 border-b p-3 sm:p-4">
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
            <span className="inline-flex items-center gap-1.5"><Inbox className="size-3.5" />Загружено: <strong className="font-mono text-foreground">{tickets.length}{query.hasNextPage ? "+" : ""}</strong></span>
            {waitingCount ? <Badge variant="destructive"><AlertCircle />Ожидают ответа: {waitingCount}</Badge> : null}
            {query.isFetching && !query.isFetchingNextPage && !query.isLoading ? <span className="font-mono text-[10px]">Синхронизация…</span> : null}
          </div>
        </div>

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
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
              tickets={tickets}
              ticketTypes={firstPage?.ticket_config.types ?? {}}
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
          <div ref={sentinelRef} className="flex min-h-12 items-center justify-center px-4 py-3 text-xs text-muted-foreground" aria-live="polite">
            {query.isFetchingNextPage ? <><RefreshCw className="mr-2 size-3.5 animate-spin" />Загрузка следующей страницы…</> : query.hasNextPage ? "Прокрутите ниже, чтобы загрузить ещё" : tickets.length ? "Все тикеты загружены" : null}
          </div>
        </div>
      </section>}

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
