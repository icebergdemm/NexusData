import { useEffect, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import type { TicketDetailResponse } from "./types"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { Ticket } from "lucide-react"

function fmtDate(iso: string | null): string {
  return formatBrowserDate(iso)
}

export function TicketDetailPage() {
  const { ticketId } = useParams<{ ticketId: string }>()
  const [text, setText] = useState("")
  const queryClient = useQueryClient()
  const scrollRef = useRef<HTMLDivElement>(null)
  const queryKey = ["ticket-detail", ticketId]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<TicketDetailResponse>(`/api/v1/admin/tickets/${ticketId}`),
    refetchInterval: 4000,
  })

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [data?.messages.length])

  const sendMutation = useMutation({
    mutationFn: (content: string) => api.post(`/api/v1/admin/tickets/${ticketId}/send`, { content }),
    onSuccess: () => { setText(""); queryClient.invalidateQueries({ queryKey }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const closeMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/tickets/${ticketId}/close`),
    onSuccess: () => { toast.success("Тикет закрыт"); queryClient.invalidateQueries({ queryKey }) },
  })

  if (isLoading || !data) {
    return <div className="space-y-2"><Skeleton className="h-20" /><Skeleton className="h-96" /></div>
  }

  const { ticket } = data
  const meta = data.ticket_config.types[ticket.ticket_type]
  const isClosed = ticket.status === "closed"

  return (
    <div className="mx-auto flex h-[calc(100vh-8rem)] max-w-3xl flex-col gap-3">
      <Card>
        <CardContent className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 font-semibold">
              <PlatformIcon value={meta?.emoji ?? "🎫"} fallback={Ticket} /> {ticket.title}
              <Badge variant={isClosed ? "secondary" : "default"}>{ticket.status}</Badge>
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {meta?.name ?? ticket.ticket_type} · {ticket.creator_display_name || ticket.creator_username}
              {data.player && (
                <span title={data.player.forced_display_name ? `Настоящий ник: ${data.player.player_last_time_name}` : undefined}>
                  {` · игрок: ${data.player.forced_display_name || data.player.player_last_time_name}`}
                </span>
              )}
              {isClosed && ticket.closed_by && (
                <span> · закрыл(а) {ticket.closed_by}{ticket.closed_at ? `, ${fmtDate(ticket.closed_at)}` : ""}</span>
              )}
            </div>
          </div>
          {data.can_edit && !isClosed && (
            <Button variant="destructive" size="sm" onClick={() => closeMutation.mutate()}>Закрыть тикет</Button>
          )}
        </CardContent>
      </Card>

      <Card className="flex flex-1 flex-col overflow-hidden p-0">
        <div ref={scrollRef} className="flex flex-1 flex-col gap-2 overflow-y-auto p-4">
          {data.messages.map((m) => (
            <div key={m.id} className={`rounded-md p-3 text-sm ${m.is_bot ? "bg-primary/5" : "bg-muted"}`}>
              <div className="mb-1 flex items-center justify-between">
                <span className="font-semibold">{m.author_display_name || m.author_username || "?"}</span>
                <span className="text-xs text-muted-foreground">{fmtDate(m.created_at)}</span>
              </div>
              <div className="whitespace-pre-wrap">{m.content}</div>
            </div>
          ))}
          {!data.messages.length && <div className="py-8 text-center text-muted-foreground">Сообщений нет</div>}
        </div>

        {data.can_edit && !isClosed && (
          <div className="flex gap-2 border-t p-3">
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && text.trim() && sendMutation.mutate(text)}
              placeholder="Ответить в тикет..."
            />
            <Button onClick={() => sendMutation.mutate(text)} disabled={!text.trim() || sendMutation.isPending}>Отправить</Button>
          </div>
        )}
      </Card>
    </div>
  )
}
