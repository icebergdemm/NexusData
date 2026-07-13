import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import { canEdit, useCurrentAdmin } from "@/lib/auth"
import { cn } from "@/lib/utils"

interface DispatchMessage {
  id: number
  steamid: string | null
  player_name: string | null
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

function formatTime(iso: string | null): string {
  if (!iso) return ""
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
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

  return (
    <div className="mx-auto flex h-[calc(100vh-8rem)] max-w-3xl flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold">📡 Диспетчерская</h2>
        <div className="flex gap-1 rounded-lg border bg-card p-1">
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
      </div>

      <Card className="flex flex-1 flex-col overflow-hidden p-0">
        <div ref={scrollRef} className="flex flex-1 flex-col gap-2 overflow-y-auto p-4">
          {!messages?.length && (
            <div className="py-10 text-center text-sm text-muted-foreground">Сообщений пока нет</div>
          )}
          {messages?.map((m) => (
            <div
              key={m.id}
              className={cn(
                "flex gap-2.5 rounded-lg border-l-2 px-3 py-2 text-sm",
                m.sender_type === "dispatcher"
                  ? "border-l-success bg-success/5"
                  : "border-l-primary bg-primary/5"
              )}
            >
              <span className="min-w-13.75 font-mono text-[10px] text-muted-foreground pt-0.5">
                {formatTime(m.created_at)}
              </span>
              <span
                className={cn(
                  "min-w-30 max-w-45 truncate font-bold",
                  m.sender_type === "dispatcher" ? "text-success" : "text-info"
                )}
                title={m.steamid ?? ""}
              >
                {m.player_name || m.steamid || "?"}
              </span>
              <span className="flex-1 break-words">{m.text}</span>
            </div>
          ))}
        </div>

        {canSend && (
          <div className="flex gap-2 border-t p-3">
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Написать как диспетчер..."
              maxLength={500}
              disabled={sending}
              autoComplete="off"
            />
            <Button onClick={send} disabled={sending || !text.trim()}>
              {sending ? "⏳…" : "📤 Отправить"}
            </Button>
          </div>
        )}
      </Card>
    </div>
  )
}
