import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Radio, MoreVertical, Send, Truck, CornerUpLeft, PackagePlus, Zap,
  MapPin, Ship, Target, Users2, Warehouse as WarehouseIcon,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { canEdit, useCurrentAdmin } from "@/lib/auth"
import { cn } from "@/lib/utils"
import type { ModerationOverview } from "@/pages/moderation/types"
import { Warehouse } from "./Warehouse"

interface DispatchMessage {
  id: number
  steamid: string | null
  player_name: string | null
  real_name: string | null
  text: string
  sender_type: "player" | "dispatcher"
  server_name: string | null
  created_at: string | null
}

const SERVERS = [
  { key: "ASPER1", label: "ASPER1" },
  { key: "ASPER2", label: "ASPER2" },
  { key: "", label: "Все" },
]

const STUB_ACTIONS = [
  { key: "shuttle_send", label: "Отправить челнок снабжения", icon: Truck },
  { key: "shuttle_recall", label: "Отозвать челнок снабжения", icon: CornerUpLeft },
  { key: "cargo_queue", label: "Создать груз в очередь на спавн", icon: PackagePlus },
  { key: "torpedo_strike", label: "Сделать торпедный удар", icon: Zap },
]

function formatTime(iso: string | null): string {
  if (!iso) return ""
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
}

function RoundInfoCard({ serverName, state }: { serverName: string; state: ModerationOverview["round_states"][string] }) {
  return (
    <Card className="overflow-hidden py-0">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 border-b bg-muted/30 py-2.5">
        <CardTitle className="text-sm font-semibold">{serverName}</CardTitle>
        <Badge variant={state.active ? "default" : "secondary"} className={state.active ? "bg-success text-white" : ""}>
          {state.active ? "Идёт раунд" : "Раунд не идёт"}
        </Badge>
      </CardHeader>
      {state.active ? (
        <CardContent className="space-y-1.5 py-3 text-xs">
          <div className="flex items-center gap-1.5 text-muted-foreground"><MapPin className="size-3.5 shrink-0" /> {state.location ?? "-"}</div>
          <div className="flex items-center gap-1.5 text-muted-foreground"><Ship className="size-3.5 shrink-0" /> {state.submarine_name ?? "-"}</div>
          <div className="flex items-center gap-1.5 text-muted-foreground"><Target className="size-3.5 shrink-0" /> {state.mission ?? "-"}</div>
          <div className="flex items-center gap-1.5 text-muted-foreground"><Users2 className="size-3.5 shrink-0" /> {state.players.length} игроков</div>
        </CardContent>
      ) : (
        <CardContent className="py-3 text-xs text-muted-foreground">Нет активного раунда</CardContent>
      )}
    </Card>
  )
}

export function DispatchPage() {
  const { data: admin } = useCurrentAdmin()
  const [server, setServer] = useState("ASPER1")
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)
  const queryClient = useQueryClient()
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: messages } = useQuery({
    queryKey: ["dispatch-messages", server],
    queryFn: () =>
      api.get<DispatchMessage[]>(
        `/api/v1/admin/dispatch/messages?${new URLSearchParams({ limit: "100", server }).toString()}`
      ),
    refetchInterval: 3000,
  })

  const { data: overview } = useQuery({
    queryKey: ["dispatch-overview", server],
    queryFn: () => api.get<ModerationOverview>(`/api/v1/admin/moderation/overview${server ? `?server=${server}` : ""}`),
    refetchInterval: 10000,
  })

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages])

  const pollPending = async () => {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const res = await api.get<{ pending: boolean }>("/api/v1/admin/dispatch/pending")
      if (!res.pending) break
      await new Promise((r) => setTimeout(r, 2000))
    }
  }

  const send = async () => {
    const trimmed = text.trim()
    if (!trimmed || sending) return
    setText("")
    setSending(true)
    try {
      await api.post("/api/v1/admin/dispatch/send", { text: trimmed, server_name: server || null })
      await pollPending()
      queryClient.invalidateQueries({ queryKey: ["dispatch-messages"] })
    } finally {
      setSending(false)
    }
  }

  const canSend = canEdit(admin, "dispatch")
  const roundStates = overview ? Object.entries(overview.round_states) : []
  const onlinePlayers = overview?.online_players ?? []

  return (
    <div>
      <PageHeader title="Диспетчерская" description="Радиосвязь с игроками и статус раунда в реальном времени" icon={Radio} />

      <Tabs defaultValue="radio">
        <TabsList className="mb-3">
          <TabsTrigger value="radio" className="gap-1.5"><Radio className="size-3.5" /> Радиосвязь</TabsTrigger>
          <TabsTrigger value="warehouse" className="gap-1.5"><WarehouseIcon className="size-3.5" /> Склад</TabsTrigger>
        </TabsList>

        <TabsContent value="radio">
      <div className="flex h-[calc(100vh-13rem)] gap-4">
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="flex justify-end gap-1 rounded-lg border bg-card p-1 self-end">
            {SERVERS.map((s) => (
              <button
                key={s.key}
                onClick={() => setServer(s.key)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-bold transition-colors",
                  server === s.key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                )}
              >
                {s.label}
              </button>
            ))}
          </div>

          <Card className="flex flex-1 flex-col overflow-hidden p-0">
            <div ref={scrollRef} className="flex flex-1 flex-col gap-2 overflow-y-auto p-4">
              {!messages?.length && (
                <div className="py-10 text-center text-sm text-muted-foreground">Сообщений пока нет</div>
              )}
              {messages?.map((m) => {
                const outgoing = m.sender_type === "dispatcher"
                return (
                  <div key={m.id} className={cn("flex flex-col", outgoing ? "items-end" : "items-start")}>
                    <div
                      className={cn(
                        "max-w-[75%] rounded-2xl px-3.5 py-2 text-sm",
                        outgoing ? "rounded-br-sm bg-primary text-primary-foreground" : "rounded-bl-sm bg-muted"
                      )}
                    >
                      {!outgoing && (
                        <div
                          className="mb-0.5 text-xs font-semibold text-info"
                          title={m.real_name ? `Настоящий ник: ${m.real_name}` : (m.steamid ?? "")}
                        >
                          {m.player_name || m.steamid || "?"}
                        </div>
                      )}
                      <div className="break-words">{m.text}</div>
                    </div>
                    <span className="mt-0.5 px-1 font-mono text-[10px] text-muted-foreground">
                      {outgoing ? "Диспетчер · " : ""}{formatTime(m.created_at)}
                    </span>
                  </div>
                )
              })}
            </div>

            {canSend && (
              <div className="flex items-center gap-2 border-t p-3">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="icon" className="shrink-0 rounded-full">
                      <MoreVertical className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" side="top">
                    {STUB_ACTIONS.map((a) => (
                      <DropdownMenuItem key={a.key} onClick={() => toast.info("Функция в разработке")}>
                        <a.icon className="mr-1 size-4" /> {a.label}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
                <Input
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && send()}
                  placeholder="Диспетчер, введите сообщение..."
                  maxLength={500}
                  disabled={sending}
                  autoComplete="off"
                />
                <Button onClick={send} disabled={sending || !text.trim()} size="icon" className="shrink-0 rounded-full">
                  <Send className="size-4" />
                </Button>
              </div>
            )}
          </Card>
        </div>

        <div className="hidden min-h-0 w-72 shrink-0 flex-col gap-3 lg:flex">
          {roundStates.map(([sn, state]) => <RoundInfoCard key={sn} serverName={sn} state={state} />)}

          <Card className="flex min-h-0 flex-1 flex-col overflow-hidden py-0">
            <CardHeader className="border-b bg-muted/30 py-2.5">
              <CardTitle className="text-sm font-semibold">Игроки онлайн ({onlinePlayers.length})</CardTitle>
            </CardHeader>
            <ScrollArea className="min-h-0 flex-1">
              <div className="divide-y">
                {onlinePlayers.map((p) => (
                  <div key={p.player_steamid} className="flex items-center justify-between px-3 py-2 text-xs">
                    <span
                      className="truncate font-medium"
                      title={p.forced_display_name ? `Настоящий ник: ${p.player_name ?? p.player_steamid}` : undefined}
                    >
                      {p.forced_display_name || p.player_name || p.character_name || "?"}
                    </span>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {server === "" && p.server_name && <Badge variant="outline" className="text-[10px]">{p.server_name}</Badge>}
                      <Badge variant="secondary" className="text-[10px]">{p.job_id ?? "-"}</Badge>
                    </div>
                  </div>
                ))}
                {!onlinePlayers.length && <div className="py-6 text-center text-xs text-muted-foreground">Никого нет онлайн</div>}
              </div>
            </ScrollArea>
          </Card>
        </div>
      </div>
        </TabsContent>

        <TabsContent value="warehouse">
          <Warehouse />
        </TabsContent>
      </Tabs>
    </div>
  )
}
