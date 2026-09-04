import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { AlertCircle, Inbox, RefreshCw, Search, Ticket } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import type { TicketsListResponse } from "./types"
import { TicketInboxList, TicketInboxSkeleton } from "./components/ticket-inbox"
import { filterTickets } from "./components/ticket-utils"

export function TicketsListPage() {
  const [status, setStatus] = useState("open")
  const [search, setSearch] = useState("")
  const query = useQuery({
    queryKey: ["tickets", status],
    queryFn: () => api.get<TicketsListResponse>(`/api/v1/admin/tickets?status=${status}`),
    placeholderData: (previous) => previous,
    refetchInterval: 10_000,
  })

  const tickets = query.data?.tickets
  const filteredTickets = useMemo(() => filterTickets(tickets ?? [], search, "all"), [tickets, search])
  const waitingCount = tickets?.filter((ticket) => ticket.awaiting_admin && ticket.status !== "closed").length ?? 0

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
            <Tabs value={status} onValueChange={setStatus}>
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
          <TicketInboxList tickets={filteredTickets} ticketTypes={query.data?.ticket_config.types ?? {}} />
        )}
      </section>
    </div>
  )
}
