import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { Ticket, ChevronRight } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import type { TicketsListResponse } from "./types"

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}

export function TicketsListPage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState("open")

  const { data, isLoading } = useQuery({
    queryKey: ["tickets", status],
    queryFn: () => api.get<TicketsListResponse>(`/api/v1/admin/tickets?status=${status}`),
  })

  return (
    <div>
      <PageHeader title="Тикеты" description="Обращения игроков через систему поддержки" icon={Ticket} />

      <Tabs value={status} onValueChange={setStatus} className="mb-4">
        <TabsList>
          <TabsTrigger value="open">Открытые</TabsTrigger>
          <TabsTrigger value="closed">Закрытые</TabsTrigger>
          <TabsTrigger value="all">Все</TabsTrigger>
        </TabsList>
      </Tabs>

      {isLoading ? (
        <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : (
        <div className="space-y-2">
          {data?.tickets.map((t) => {
            const meta = data.ticket_config.types[t.ticket_type]
            return (
              <Card key={t.id} className="cursor-pointer overflow-hidden py-0 transition-all hover:border-primary/50" onClick={() => navigate(`/tickets/${t.id}`)}>
                <CardContent className="flex items-center gap-3 py-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-xl">{meta?.emoji ?? "🎫"}</div>
                  <div className="flex-1">
                    <div className="font-medium">{t.title}</div>
                    <div className="text-xs text-muted-foreground">
                      {meta?.name ?? t.ticket_type} · {t.creator_display_name || t.creator_username} · {fmtDate(t.created_at)}
                      {t.status === "closed" && t.closed_by && <> · закрыл(а) {t.closed_by}</>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {t.awaiting_admin && t.status !== "closed" && <Badge variant="destructive">Ожидает ответа</Badge>}
                    <Badge variant={t.status === "closed" ? "secondary" : "default"}>{t.status}</Badge>
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </div>
                </CardContent>
              </Card>
            )
          })}
          {!data?.tickets.length && (
            <div className="py-12 text-center text-muted-foreground">Тикетов нет</div>
          )}
        </div>
      )}
    </div>
  )
}
