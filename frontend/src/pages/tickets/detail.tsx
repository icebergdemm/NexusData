import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, ArrowDown, ArrowLeft, Inbox, PanelRight, RefreshCw, Search, Ticket, UsersRound } from "lucide-react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import type { TicketDetailResponse, TicketMessagePollResponse, TicketsListResponse } from "./types"
import { TicketComposer } from "./components/ticket-composer"
import { ArchiveLegend, TicketConversation } from "./components/ticket-conversation"
import { TicketContextPanel } from "./components/ticket-context"
import { TicketInboxList, TicketInboxSkeleton } from "./components/ticket-inbox"
import { ArchiveStatusBadge, TicketStatusBadge } from "./components/ticket-shared"
import { filterTickets } from "./components/ticket-utils"
import { TicketActionsMenu } from "./TicketActionsMenu"
import { TicketMemberDialog } from "./TicketMemberDialog"
import { TicketMemberRemoveDialog } from "./TicketMemberRemoveDialog"

const DETAIL_REFRESH_MS = 4_000
const INBOX_REFRESH_MS = 10_000

function TicketInboxPane({
  data,
  activeId,
  loading,
  error,
  refreshing,
  onRefresh,
  onNavigate,
}: {
  data?: TicketsListResponse
  activeId: number
  loading: boolean
  error: Error | null
  refreshing: boolean
  onRefresh: () => void
  onNavigate?: () => void
}) {
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("open")
  const tickets = data?.tickets
  const filteredTickets = useMemo(() => filterTickets(tickets ?? [], search, status), [tickets, search, status])
  const openCount = tickets?.filter((ticket) => ticket.status !== "closed").length ?? 0
  const closedCount = (tickets?.length ?? 0) - openCount

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <div className="border-b px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">Входящие</p>
            <h2 className="mt-1 text-sm font-semibold">Тикеты поддержки</h2>
          </div>
          <Button type="button" variant="ghost" size="icon-sm" onClick={onRefresh} disabled={refreshing} title="Обновить список" aria-label="Обновить список тикетов">
            <RefreshCw className={refreshing ? "animate-spin" : undefined} />
          </Button>
        </div>
        <div className="relative mt-3">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Номер, автор или тема" className="h-8 pl-8 text-xs" aria-label="Поиск тикетов" />
        </div>
        <Tabs value={status} onValueChange={setStatus} className="mt-2">
          <TabsList variant="line" className="grid h-8 w-full grid-cols-3">
            <TabsTrigger value="open" className="text-xs">Открытые <span className="font-mono text-[9px]">{openCount}</span></TabsTrigger>
            <TabsTrigger value="closed" className="text-xs">Архив <span className="font-mono text-[9px]">{closedCount}</span></TabsTrigger>
            <TabsTrigger value="all" className="text-xs">Все <span className="font-mono text-[9px]">{tickets?.length ?? 0}</span></TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? <TicketInboxSkeleton compact /> : error ? (
          <div className="flex min-h-52 flex-col items-center justify-center gap-3 px-5 text-center">
            <p className="text-sm font-medium">Не удалось загрузить тикеты</p>
            <p className="text-xs text-muted-foreground">{error.message}</p>
            <Button type="button" variant="outline" size="sm" onClick={onRefresh}>Повторить</Button>
          </div>
        ) : <TicketInboxList tickets={filteredTickets} ticketTypes={data?.ticket_config.types ?? {}} activeId={activeId} compact onNavigate={onNavigate} />}
      </div>
      <div className="border-t p-2">
        <Button asChild variant="ghost" size="sm" className="w-full justify-start">
          <Link to="/tickets" onClick={onNavigate}><Inbox data-icon="inline-start" />Открыть полный список</Link>
        </Button>
      </div>
    </div>
  )
}

function DetailLoading() {
  return (
    <div className="-m-4 grid h-[calc(100dvh-3.5rem)] min-h-[38rem] grid-cols-1 overflow-hidden border-t bg-background sm:-m-5 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[18rem_minmax(0,1fr)_20rem]">
      <div className="hidden border-r p-3 lg:flex lg:flex-col lg:gap-3">
        <Skeleton className="h-16 w-full" />
        {Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-20 w-full" />)}
      </div>
      <div className="flex min-w-0 flex-col">
        <div className="flex h-20 items-center gap-3 border-b px-4"><Skeleton className="size-9" /><div className="flex flex-1 flex-col gap-2"><Skeleton className="h-4 w-1/2" /><Skeleton className="h-3 w-1/3" /></div></div>
        <div className="flex flex-1 flex-col gap-5 p-5">{Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-24 w-full" />)}</div>
      </div>
      <div className="hidden border-l p-4 xl:flex xl:flex-col xl:gap-4"><Skeleton className="h-16 w-full" /><Skeleton className="h-40 w-full" /><Skeleton className="h-52 w-full" /></div>
    </div>
  )
}

export function TicketDetailPage() {
  const { ticketId } = useParams<{ ticketId: string }>()
  const numericTicketId = Number(ticketId)
  const queryClient = useQueryClient()
  const scrollRef = useRef<HTMLDivElement>(null)
  const previousMessageCountRef = useRef(0)
  const latestMessageIdRef = useRef(0)
  const stickToBottomRef = useRef(true)
  const [text, setText] = useState("")
  const [files, setFiles] = useState<File[]>([])
  const [showNewMessages, setShowNewMessages] = useState(false)
  const [inboxOpen, setInboxOpen] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [restoreOpen, setRestoreOpen] = useState(false)
  const [memberDialogOpen, setMemberDialogOpen] = useState(false)
  const [removeMemberDialogOpen, setRemoveMemberDialogOpen] = useState(false)
  const [pollReady, setPollReady] = useState(false)
  const detailQueryKey = useMemo(() => ["ticket-detail", ticketId] as const, [ticketId])

  const detailQuery = useQuery({
    queryKey: detailQueryKey,
    queryFn: () => api.get<TicketDetailResponse>(`/api/v1/admin/tickets/${ticketId}`),
    enabled: Number.isFinite(numericTicketId),
  })
  const inboxQuery = useQuery({
    queryKey: ["tickets", "all"],
    queryFn: () => api.get<TicketsListResponse>("/api/v1/admin/tickets?status=all"),
    refetchInterval: INBOX_REFRESH_MS,
  })

  const messageCount = detailQuery.data?.messages.length ?? 0
  useEffect(() => {
    previousMessageCountRef.current = 0
    latestMessageIdRef.current = 0
    setPollReady(false)
    stickToBottomRef.current = true
    setShowNewMessages(false)
  }, [ticketId])

  useEffect(() => {
    if (!detailQuery.data) return
    latestMessageIdRef.current = detailQuery.data.messages.reduce(
      (latest, message) => Math.max(latest, message.id),
      0,
    )
    setPollReady(true)
  }, [detailQuery.data])

  const messagePollQuery = useQuery({
    queryKey: ["ticket-message-poll", ticketId],
    queryFn: () => api.get<TicketMessagePollResponse>(
      `/api/v1/admin/tickets/${ticketId}/messages?after_id=${latestMessageIdRef.current}`,
    ),
    enabled: pollReady && Number.isFinite(numericTicketId),
    refetchInterval: DETAIL_REFRESH_MS,
  })

  useEffect(() => {
    const incoming = messagePollQuery.data?.messages ?? []
    const deliveryUpdates = messagePollQuery.data?.delivery_updates ?? []
    const ticketUpdate = messagePollQuery.data?.ticket_update
    if (!incoming.length && !deliveryUpdates.length && !ticketUpdate) return
    const statusChanged = Boolean(
      ticketUpdate
      && detailQuery.data
      && (
        ticketUpdate.status !== detailQuery.data.ticket.status
        || ticketUpdate.archive_state !== detailQuery.data.ticket.archive_state
      )
    )
    const knownBeforeUpdate = new Set(
      detailQuery.data?.messages.map((message) => message.id) ?? [],
    )
    const hasUnseenIncoming = incoming.some(
      (message) => !knownBeforeUpdate.has(message.id),
    )

    queryClient.setQueryData<TicketDetailResponse>(detailQueryKey, (current) => {
      if (!current) return current
      const known = new Set(current.messages.map((message) => message.id))
      const additions = incoming.filter((message) => !known.has(message.id))
      if (additions.length) {
        latestMessageIdRef.current = Math.max(
          latestMessageIdRef.current,
          ...additions.map((message) => message.id),
        )
      }
      const deliveryById = new Map(deliveryUpdates.map((update) => [update.id, update]))
      let deliveryChanged = false
      const messages = current.messages.map((message) => {
        const update = deliveryById.get(message.id)
        if (!update || (
          update.delivery_status === message.delivery_status
          && update.delivery_error === message.delivery_error
        )) return message
        deliveryChanged = true
        return { ...message, ...update }
      })
      const ticketChanged = Boolean(ticketUpdate && (
        ticketUpdate.status !== current.ticket.status
        || ticketUpdate.archive_state !== current.ticket.archive_state
        || ticketUpdate.closed_by !== current.ticket.closed_by
        || ticketUpdate.closed_at !== current.ticket.closed_at
        || ticketUpdate.last_message_at !== current.ticket.last_message_at
        || ticketUpdate.awaiting_admin !== current.ticket.awaiting_admin
      ))
      if (!additions.length && !deliveryChanged && !ticketChanged) return current
      return {
        ...current,
        ticket: ticketUpdate ? { ...current.ticket, ...ticketUpdate } : current.ticket,
        messages: [...messages, ...additions],
      }
    })
    // Participant state and archive totals are more expensive to compute, so
    // refresh the full detail only after the lightweight poll found a change.
    if (hasUnseenIncoming || statusChanged) {
      void queryClient.invalidateQueries({ queryKey: detailQueryKey })
      void queryClient.invalidateQueries({ queryKey: ["tickets"] })
    }
  }, [detailQuery.data, detailQueryKey, messagePollQuery.data, queryClient])

  useEffect(() => {
    if (!detailQuery.data) return
    const previousCount = previousMessageCountRef.current
    const receivedNewMessage = messageCount > previousCount && previousCount > 0
    if (previousCount === 0 || stickToBottomRef.current) {
      requestAnimationFrame(() => {
        const element = scrollRef.current
        if (element) element.scrollTop = element.scrollHeight
      })
      setShowNewMessages(false)
    } else if (receivedNewMessage) {
      setShowNewMessages(true)
    }
    previousMessageCountRef.current = messageCount
  }, [detailQuery.data, messageCount])

  const scrollToBottom = () => {
    stickToBottomRef.current = true
    setShowNewMessages(false)
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }

  const sendMutation = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.append("content", text.trim())
      files.forEach((file) => form.append("files", file))
      return api.postForm(`/api/v1/admin/tickets/${ticketId}/send`, form)
    },
    onSuccess: () => {
      setText("")
      setFiles([])
      stickToBottomRef.current = true
      queryClient.invalidateQueries({ queryKey: detailQueryKey })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => {
      // The API persists evidence before Discord delivery. A transport error
      // therefore still creates a visible failed row which moderators must
      // be able to audit instead of silently retrying into duplicates.
      toast.error(error.message)
      queryClient.invalidateQueries({ queryKey: detailQueryKey })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
  })

  const closeMutation = useMutation({
    mutationFn: () => api.post<{ status: string; detail?: string }>(`/api/v1/admin/tickets/${ticketId}/close`),
    onSuccess: (result) => {
      if (result.status === "queued") {
        toast.info(result.detail || "Закрытие поставлено в очередь; канал пока остаётся открытым")
      } else {
        toast.success("Тикет закрыт и передан в архив")
      }
      queryClient.invalidateQueries({ queryKey: detailQueryKey })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const assignMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/tickets/${ticketId}/assign`),
    onSuccess: () => {
      toast.success("Ответственный обновлён")
      queryClient.invalidateQueries({ queryKey: detailQueryKey })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/tickets/${ticketId}/delete`),
    onSuccess: () => {
      toast.success("Тикет перемещён в удалённые")
      setDeleteOpen(false)
      queryClient.invalidateQueries({ queryKey: detailQueryKey })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const restoreMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/tickets/${ticketId}/restore`),
    onSuccess: () => {
      toast.success("Тикет восстановлен")
      setRestoreOpen(false)
      queryClient.invalidateQueries({ queryKey: detailQueryKey })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (detailQuery.isLoading) return <DetailLoading />

  if (detailQuery.isError || !detailQuery.data) {
    const message = detailQuery.error instanceof Error ? detailQuery.error.message : "Тикет недоступен"
    return (
      <div className="flex min-h-[28rem] flex-col items-center justify-center gap-4 border bg-background px-6 text-center">
        <Ticket className="size-10 text-muted-foreground" aria-hidden="true" />
        <div>
          <h1 className="text-lg font-semibold">Не удалось открыть тикет</h1>
          <p className="mt-1 text-sm text-muted-foreground">{message}</p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline"><Link to="/tickets"><ArrowLeft data-icon="inline-start" />К списку</Link></Button>
          <Button type="button" onClick={() => detailQuery.refetch()}><RefreshCw data-icon="inline-start" />Повторить</Button>
        </div>
      </div>
    )
  }

  const data = detailQuery.data
  const { ticket } = data
  const meta = data.ticket_config.types[ticket.ticket_type]
  const isClosed = ticket.status === "closed"
  const isDeleted = Boolean(ticket.deleted_at)
  const isArchiveIncomplete = ticket.archive_state === "incomplete"
  const canReply = !isDeleted && !isArchiveIncomplete && (data.permissions?.can_reply ?? data.can_edit)
  const canClose = !isDeleted && !isArchiveIncomplete && (data.permissions?.can_close ?? data.can_edit)
  const canAssign = !isDeleted && (data.permissions?.can_assign ?? data.can_assign ?? data.can_edit)
  const canDelete = data.permissions?.can_delete ?? data.can_delete ?? false
  const archiveRisk = isArchiveIncomplete || data.archive?.state === "failed" || data.archive?.state === "partial" || data.archive?.state === "pending" || Boolean(data.archive?.failed_count)
  const creatorName = ticket.creator_display_name || ticket.creator_username || "Неизвестный пользователь"
  const participants = data.participants ?? []

  const inboxPane = (
    <TicketInboxPane
      data={inboxQuery.data}
      activeId={ticket.id}
      loading={inboxQuery.isLoading}
      error={inboxQuery.error instanceof Error ? inboxQuery.error : null}
      refreshing={inboxQuery.isFetching}
      onRefresh={() => { void inboxQuery.refetch() }}
      onNavigate={() => setInboxOpen(false)}
    />
  )

  const contextPane = (
    <TicketContextPanel data={data} canAssign={canAssign} assignPending={assignMutation.isPending} onAssign={() => assignMutation.mutate()} />
  )

  return (
    <div className="-m-4 grid h-[calc(100dvh-3.5rem)] min-h-[38rem] grid-cols-1 overflow-hidden border-t bg-background sm:-m-5 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[18rem_minmax(0,1fr)_20rem]">
      <section className="hidden min-h-0 border-r lg:block">{inboxPane}</section>

      <main className="flex min-h-0 min-w-0 flex-col bg-background">
        <header className="flex min-h-[4.75rem] shrink-0 items-center gap-2 border-b px-3 py-2 sm:px-4">
          <Button asChild variant="ghost" size="icon-sm" title="Назад к списку" aria-label="Назад к списку тикетов">
            <Link to="/tickets"><ArrowLeft /></Link>
          </Button>
          <Sheet open={inboxOpen} onOpenChange={setInboxOpen}>
            <SheetTrigger asChild>
              <Button type="button" variant="ghost" size="icon-sm" className="lg:hidden" title="Открыть тикеты" aria-label="Открыть список тикетов"><Inbox /></Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-[min(92vw,22rem)] gap-0 p-0 sm:max-w-none">
              <SheetHeader className="sr-only"><SheetTitle>Тикеты поддержки</SheetTitle><SheetDescription>Переключение между тикетами</SheetDescription></SheetHeader>
              {inboxPane}
            </SheetContent>
          </Sheet>

          <div className="flex size-9 shrink-0 items-center justify-center border bg-muted text-lg" aria-hidden="true">
            <PlatformIcon value={meta?.emoji ?? "🎫"} fallback={Ticket} className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <h1 className="truncate text-sm font-semibold sm:text-base"><span className="mr-1.5 font-mono text-[11px] text-muted-foreground">#{ticket.id}</span>{ticket.title}</h1>
              <TicketStatusBadge status={ticket.status} compact />
              {isDeleted ? <Badge variant="destructive">Удалён</Badge> : null}
            </div>
            <p className="mt-0.5 truncate text-[10px] text-muted-foreground sm:text-xs">{meta?.name ?? ticket.ticket_type} · {creatorName} · {participants.length || 1} участник(а)</p>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <div className="hidden 2xl:block"><ArchiveStatusBadge archive={data.archive} state={ticket.archive_state} compact /></div>
            <Button type="button" variant="ghost" size="icon-sm" onClick={() => detailQuery.refetch()} disabled={detailQuery.isFetching} title="Обновить историю" aria-label="Обновить историю сообщений">
              <RefreshCw className={detailQuery.isFetching ? "animate-spin" : undefined} />
            </Button>
            <Sheet open={contextOpen} onOpenChange={setContextOpen}>
              <SheetTrigger asChild>
                <Button type="button" variant="ghost" size="icon-sm" className="xl:hidden" title="Участники и архив" aria-label="Открыть участников и сведения об архиве"><PanelRight /></Button>
              </SheetTrigger>
              <SheetContent side="right" className="w-[min(92vw,24rem)] gap-0 p-0 sm:max-w-none">
                <SheetHeader className="sr-only"><SheetTitle>Контекст тикета</SheetTitle><SheetDescription>Участники, ответственный и архив материалов</SheetDescription></SheetHeader>
                {contextPane}
              </SheetContent>
            </Sheet>
            <TicketActionsMenu
              ticket={ticket}
              canAssign={canAssign}
              canEdit={canClose}
              canDelete={canDelete}
              disabled={closeMutation.isPending || deleteMutation.isPending || restoreMutation.isPending}
              onAddMember={() => setMemberDialogOpen(true)}
              onRemoveMember={() => setRemoveMemberDialogOpen(true)}
              onClose={() => closeMutation.mutate()}
              onDelete={() => setDeleteOpen(true)}
              onRestore={() => setRestoreOpen(true)}
            />
            {canClose && !isClosed && !isArchiveIncomplete ? (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button type="button" variant="destructive" size="sm" className="hidden sm:inline-flex">Закрыть</Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Закрыть тикет #{ticket.id}?</AlertDialogTitle>
                    <AlertDialogDescription>
                      {archiveRisk
                        ? "Архив материалов ещё не завершён или содержит ошибки. Сервер может не разрешить закрытие, пока важные вложения не будут сохранены."
                        : "Переписка останется доступна на сайте, а тикет перейдёт в архив."}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Отмена</AlertDialogCancel>
                    <AlertDialogAction variant="destructive" disabled={closeMutation.isPending} onClick={() => closeMutation.mutate()}>
                      {closeMutation.isPending ? "Закрытие…" : archiveRisk ? "Попробовать закрыть" : "Закрыть тикет"}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            ) : null}
          </div>
        </header>

        <div className="relative min-h-0 flex-1">
          <div
            ref={scrollRef}
            onScroll={(event) => {
              const element = event.currentTarget
              const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120
              stickToBottomRef.current = nearBottom
              if (nearBottom) setShowNewMessages(false)
            }}
            className="size-full overflow-y-auto overscroll-contain px-3 pb-5 sm:px-5"
          >
            <TicketConversation messages={data.messages} participants={participants} />
          </div>
          {showNewMessages ? (
            <Button type="button" size="sm" className="absolute bottom-3 left-1/2 -translate-x-1/2" onClick={scrollToBottom}>
              <ArrowDown data-icon="inline-start" />Новые сообщения
            </Button>
          ) : null}
        </div>

        {isDeleted ? (
          <div className="flex shrink-0 items-center gap-3 border-t bg-destructive/10 px-4 py-3">
            <Badge variant="destructive">Удалён</Badge>
            <div className="min-w-0 flex-1 text-xs text-muted-foreground">
              Тикет удалён{ticket.deleted_by ? ` пользователем ${ticket.deleted_by}` : ""}{ticket.deleted_at ? ` · ${formatBrowserDate(ticket.deleted_at)}` : ""}. История хранится 30 дней и доступна только для чтения.
            </div>
          </div>
        ) : isClosed ? (
          <div className="flex shrink-0 items-center gap-3 border-t bg-muted/30 px-4 py-3">
            <Badge variant="secondary">Закрыт</Badge>
            <div className="min-w-0 flex-1 text-xs text-muted-foreground">
              Тикет закрыт{ticket.closed_by ? ` пользователем ${ticket.closed_by}` : ""}{ticket.closed_at ? ` · ${formatBrowserDate(ticket.closed_at)}` : ""}. История доступна только для чтения.
            </div>
            <div className="hidden lg:block"><ArchiveLegend /></div>
          </div>
        ) : isArchiveIncomplete ? (
          <div className="flex shrink-0 items-start gap-3 border-t border-destructive/40 bg-destructive/10 px-4 py-3 text-xs text-destructive">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div><span className="font-semibold">Discord-канал удалён вне NexusData.</span> Архив может быть неполным; переписка оставлена только для проверки и восстановления доказательств.</div>
          </div>
        ) : canReply ? (
          <TicketComposer text={text} files={files} pending={sendMutation.isPending} onTextChange={setText} onFilesChange={setFiles} onSend={() => sendMutation.mutate()} />
        ) : (
          <div className="flex shrink-0 items-center gap-2 border-t bg-muted/30 px-4 py-3 text-xs text-muted-foreground"><UsersRound className="size-4" />У вас нет права отвечать в этом тикете.</div>
        )}

        {canClose && !isClosed && !isArchiveIncomplete ? (
          <div className="border-t p-2 sm:hidden">
            <AlertDialog>
              <AlertDialogTrigger asChild><Button type="button" variant="destructive" size="sm" className="w-full">Закрыть тикет</Button></AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader><AlertDialogTitle>Закрыть тикет #{ticket.id}?</AlertDialogTitle><AlertDialogDescription>{archiveRisk ? "Архив ещё не завершён или содержит ошибки." : "Переписка останется доступна в архиве."}</AlertDialogDescription></AlertDialogHeader>
                <AlertDialogFooter><AlertDialogCancel>Отмена</AlertDialogCancel><AlertDialogAction variant="destructive" disabled={closeMutation.isPending} onClick={() => closeMutation.mutate()}>Закрыть</AlertDialogAction></AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        ) : null}
      </main>

      <section className="hidden min-h-0 border-l xl:block">{contextPane}</section>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={(open) => !deleteMutation.isPending && setDeleteOpen(open)}
        title={`Удалить тикет #${ticket.id}?`}
        description={`«${ticket.title}» будет убран из обычных списков. История сохранится на 30 дней, после чего тикет и сообщения будут удалены окончательно.`}
        confirmLabel={deleteMutation.isPending ? "Удаление..." : "Удалить тикет"}
        confirmDisabled={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
      <ConfirmDialog
        open={restoreOpen}
        onOpenChange={(open) => !restoreMutation.isPending && setRestoreOpen(open)}
        title={`Восстановить тикет #${ticket.id}?`}
        description={`«${ticket.title}» снова появится в списке закрытых тикетов. Discord-канал не восстанавливается.`}
        confirmLabel={restoreMutation.isPending ? "Восстановление..." : "Восстановить тикет"}
        confirmDisabled={restoreMutation.isPending}
        onConfirm={() => restoreMutation.mutate()}
      />
      <TicketMemberDialog open={memberDialogOpen} ticketId={ticket.id} onOpenChange={setMemberDialogOpen} />
      <TicketMemberRemoveDialog open={removeMemberDialogOpen} ticketId={ticket.id} onOpenChange={setRemoveMemberDialogOpen} />
    </div>
  )
}
