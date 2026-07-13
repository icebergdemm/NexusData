import { Bell } from "lucide-react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Separator } from "@/components/ui/separator"

interface NotificationItem {
  id: number
  type: "info" | "success" | "warning" | "error"
  title: string
  body?: string | null
  link?: string | null
  icon?: string | null
  is_read: boolean
  created_at: string
}

const TYPE_DOT: Record<NotificationItem["type"], string> = {
  success: "bg-success",
  error: "bg-destructive",
  warning: "bg-warning",
  info: "bg-info",
}

function timeAgo(iso: string): string {
  const diffSec = (Date.now() - new Date(iso).getTime()) / 1000
  if (diffSec < 60) return "только что"
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} мин назад`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} ч назад`
  return `${Math.floor(diffSec / 86400)} дн. назад`
}

export function NotificationBell() {
  const queryClient = useQueryClient()

  const { data: poll } = useQuery({
    queryKey: ["notifications", "poll"],
    queryFn: () => api.get<{ unread: number }>("/api/v1/notifications/poll"),
    refetchInterval: 15000,
  })

  const { data: list, refetch } = useQuery({
    queryKey: ["notifications", "all"],
    queryFn: () => api.get<{ items: NotificationItem[] }>("/api/v1/notifications/all?page_size=30"),
    enabled: false,
  })

  const markAllRead = async () => {
    await api.post("/api/v1/notifications/read", { all: true })
    queryClient.invalidateQueries({ queryKey: ["notifications"] })
  }

  const clearRead = async () => {
    await api.delete("/api/v1/notifications/clear")
    refetch()
  }

  const unread = poll?.unread ?? 0

  return (
    <DropdownMenu onOpenChange={(open) => open && refetch()}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="relative">
          <Bell className="h-4 w-4" />
          {unread > 0 && (
            <Badge variant="destructive" className="absolute -top-1 -right-1 h-4 min-w-4 px-1 text-[10px]">
              {unread > 99 ? "99+" : unread}
            </Badge>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-90 p-0">
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-sm font-semibold">Уведомления</span>
          <div className="flex gap-1">
            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={markAllRead}>
              Прочитать все
            </Button>
            <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={clearRead}>
              Очистить
            </Button>
          </div>
        </div>
        <Separator />
        <div className="max-h-90 overflow-y-auto">
          {!list?.items?.length && (
            <div className="p-6 text-center text-sm text-muted-foreground">Уведомлений нет</div>
          )}
          {list?.items?.map((n) => (
            <a
              key={n.id}
              href={n.link ?? undefined}
              className={`flex gap-2.5 px-3 py-2.5 border-b last:border-b-0 hover:bg-accent ${!n.is_read ? "bg-primary/5" : ""}`}
            >
              <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${TYPE_DOT[n.type]}`} />
              <div className="min-w-0 flex-1">
                <div className={`text-xs ${n.is_read ? "text-muted-foreground" : "font-semibold"}`}>{n.title}</div>
                {n.body && <div className="truncate text-xs text-muted-foreground">{n.body}</div>}
                <div className="text-[10px] text-muted-foreground">{timeAgo(n.created_at)}</div>
              </div>
            </a>
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
