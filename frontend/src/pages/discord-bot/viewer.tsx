import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Checkbox } from "@/components/ui/checkbox"
import { Hash, Volume2, GitBranch, Paperclip, Send, Download, Search, Loader2, X, ShieldCheck, Reply } from "lucide-react"
import { api, ApiError } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"

// ── Типы ─────────────────────────────────────────────────────────────────

interface TreeChannel { id: string; name: string; position: number; can_send: boolean; category_id: string | null; parent_id?: string | null; type: "text" | "thread" | "voice" | "stage" }
interface TreeCategory { id: string; name: string; position: number }
interface ChannelTree {
  guild: { id: string; name: string; icon_url: string | null }
  categories: TreeCategory[]
  channels: TreeChannel[]
  can_send: boolean
  can_manage_roles: boolean
}

interface Author { id: string; username: string; display_name: string; avatar_url: string | null; bot: boolean }
interface Attachment { id: string; filename: string; url: string; content_type: string | null; size: number }
interface ReplyPreview {
  id: string; unavailable: boolean; author?: Author; content?: string; created_at?: string
}
interface DiscordMessage {
  id: string; content: string; author: Author
  created_at: string; edited_at: string | null
  attachments: Attachment[]; embeds: Record<string, unknown>[]; reply_to_id: string | null; reply_to: ReplyPreview | null
}

interface MemberRow {
  discord_id: string; username: string | null; display_name: string | null; avatar_url: string | null
  joined_at: string | null; role_ids: string[]
  steam: { steamid: string | null; player_name: string | null; avatar_url: string | null; confirmed: boolean } | null
}
interface MemberDetail extends MemberRow { last_synced_at: string | null; can_manage_roles: boolean }
interface RoleRow { id: string; name: string; color: string | null; position: number; managed: boolean; mentionable: boolean }

function fmtSize(n: number): string {
  if (n < 1024) return `${n} Б`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} КБ`
  return `${(n / 1024 / 1024).toFixed(1)} МБ`
}

function initials(name: string): string {
  return name.trim().slice(0, 2).toUpperCase()
}

// ── Лента сообщений ─────────────────────────────────────────────────────

function MessageRow({ msg, onReply }: { msg: DiscordMessage; onReply?: (msg: DiscordMessage) => void }) {
  const time = formatBrowserDate(msg.created_at)
  return (
    <div data-message-id={msg.id} className="group flex gap-2.5 rounded px-2 py-1.5 hover:bg-[#2e3035]">
      <Avatar className="mt-0.5 size-9 shrink-0">
        <AvatarImage src={msg.author.avatar_url ?? undefined} />
        <AvatarFallback>{initials(msg.author.display_name)}</AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-[#f2f3f5]">{msg.author.display_name}</span>
          {msg.author.bot && <Badge variant="secondary" className="h-4 px-1 text-[10px]">БОТ</Badge>}
          <span className="text-[11px] text-[#949ba4]">{time}{msg.edited_at && " (изменено)"}</span>
          {onReply && (
            <button
              type="button"
              title="Ответить"
              aria-label={`Ответить на сообщение ${msg.author.display_name}`}
              className="ml-auto rounded p-1 text-[#b5bac1] opacity-0 hover:bg-[#404249] hover:text-[#f2f3f5] group-hover:opacity-100"
              onClick={() => onReply(msg)}
            >
              <Reply className="size-3.5" />
            </button>
          )}
        </div>
        {msg.reply_to && (
          <div className="mb-1 mt-0.5 border-l-2 border-[#5865f2] pl-2 text-xs text-[#949ba4]">
            <div className="font-medium text-[#b5bac1]">Ответ на {msg.reply_to.author?.display_name ?? "сообщение"}</div>
            <div className="truncate">{msg.reply_to.unavailable ? "Сообщение недоступно" : (msg.reply_to.content || "Вложение")}</div>
          </div>
        )}
        {msg.content && <div className="whitespace-pre-wrap break-words text-sm text-[#dbdee1]">{msg.content}</div>}
        {msg.attachments.map((a) => (
          <div key={a.id} className="mt-1">
            {(a.content_type || "").startsWith("image/") ? (
              <img src={a.url} alt={a.filename} className="max-h-72 max-w-sm rounded border border-[#3b3d43]" />
            ) : (
              <a href={a.url} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded border border-[#3b3d43] bg-[#2b2d31] px-3 py-2 text-xs text-[#00a8fc] hover:underline">
                <Paperclip className="size-3.5" /> {a.filename} <span className="text-[#949ba4]">({fmtSize(a.size)})</span>
              </a>
            )}
          </div>
        ))}
        {msg.embeds.map((e, i) => (
          <div key={i} className="mt-1 max-w-md rounded border-l-4 border-[#4e5058] bg-[#2b2d31] p-2 text-xs text-[#dbdee1]">
            {typeof e.title === "string" && <div className="font-semibold">{e.title}</div>}
            {typeof e.description === "string" && <div className="mt-0.5 text-[#949ba4]">{e.description}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

function ChatPanel({ channel, canSend }: { channel: TreeChannel; canSend: boolean }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [replyTarget, setReplyTarget] = useState<DiscordMessage | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["discord-viewer-messages", channel.id],
    queryFn: () => api.get<{ messages: DiscordMessage[] }>(`/api/v1/admin/discord-bot/viewer/channels/${channel.id}/messages?limit=50`),
  })

  useEffect(() => {
    let stopped = false
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempts = 0

    const mergeMessage = (previous: DiscordMessage | undefined, incoming: Partial<DiscordMessage> & { id: string }): DiscordMessage | undefined => {
      if (!previous && !incoming.author) return undefined
      const incomingReply = incoming.reply_to
      const reply_to = incomingReply?.unavailable && previous?.reply_to && !previous.reply_to.unavailable
        ? previous.reply_to
        : incomingReply ?? previous?.reply_to ?? null
      return {
        ...(previous ?? incoming as DiscordMessage),
        ...incoming,
        reply_to,
      }
    }

    const updateCache = (event: { type: string; message?: Partial<DiscordMessage> & { id: string }; message_id?: string }) => {
      if (event.type === "resync") {
        queryClient.invalidateQueries({ queryKey: ["discord-viewer-messages", channel.id] })
        return
      }
      queryClient.setQueryData<{ messages: DiscordMessage[] }>(["discord-viewer-messages", channel.id], (old) => {
        if (!old) return old
        if (event.type === "message_delete") {
          return { messages: old.messages.filter((message) => message.id !== event.message_id) }
        }
        if (!event.message) return old
        const index = old.messages.findIndex((message) => message.id === event.message!.id)
        if (event.type === "message_create" && index < 0) {
          return { messages: [event.message as DiscordMessage, ...old.messages] }
        }
        if (index < 0) return old
        const merged = mergeMessage(old.messages[index], event.message)
        if (!merged) return old
        const messages = [...old.messages]
        messages[index] = merged
        return { messages }
      })
    }

    const connect = () => {
      if (stopped || document.visibilityState === "hidden") return
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
      socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/admin/discord-bot/viewer/events?channel_id=${encodeURIComponent(channel.id)}`)
      socket.onopen = () => { attempts = 0 }
      socket.onmessage = (message) => {
        try { updateCache(JSON.parse(message.data) as { type: string; message?: Partial<DiscordMessage> & { id: string }; message_id?: string }) } catch { /* malformed event: next resync will repair the list */ }
      }
      socket.onclose = () => {
        socket = null
        if (stopped || document.visibilityState === "hidden") return
        const delay = Math.min(30000, 1000 * (2 ** Math.min(attempts++, 5))) + Math.floor(Math.random() * 500)
        reconnectTimer = setTimeout(connect, delay)
      }
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible" && !socket && !reconnectTimer) connect()
    }
    document.addEventListener("visibilitychange", onVisibilityChange)
    connect()
    return () => {
      stopped = true
      document.removeEventListener("visibilitychange", onVisibilityChange)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [channel.id, queryClient])

  const [older, setOlder] = useState<DiscordMessage[]>([])
  useEffect(() => { setOlder([]) }, [channel.id])

  const loadOlder = useMutation({
    mutationFn: () => {
      const earliest = (older.length ? older : data?.messages ?? [])[((older.length ? older : data?.messages) ?? []).length - 1]
      if (!earliest) return Promise.resolve<{ messages: DiscordMessage[] }>({ messages: [] })
      return api.get<{ messages: DiscordMessage[] }>(`/api/v1/admin/discord-bot/viewer/channels/${channel.id}/messages?limit=50&before=${earliest.id}`)
    },
    onSuccess: (res) => setOlder((prev) => [...prev, ...res.messages]),
    onError: (e: ApiError) => toast.error(e.message),
  })

  // messages приходят от новых к старым — для чтения переворачиваем.
  const ordered = useMemo(() => [...(older.length ? [...(data?.messages ?? []), ...older] : data?.messages ?? [])].reverse(), [data, older])

  const send = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.set("content", draft)
      if (file) form.set("file", file)
      if (replyTarget) form.set("reply_to_id", replyTarget.id)
      return api.postForm<DiscordMessage>(`/api/v1/admin/discord-bot/viewer/channels/${channel.id}/messages`, form)
    },
    onSuccess: (sent) => {
      const sentWithPreview = replyTarget
        ? { ...sent, reply_to: { id: replyTarget.id, unavailable: false, author: replyTarget.author, content: replyTarget.content, created_at: replyTarget.created_at } }
        : sent
      queryClient.setQueryData<{ messages: DiscordMessage[] }>(["discord-viewer-messages", channel.id], (old) => ({
        messages: [sentWithPreview, ...(old?.messages ?? []).filter((message) => message.id !== sent.id)],
      }))
      setDraft(""); setFile(null)
      setReplyTarget(null)
      if (fileInputRef.current) fileInputRef.current.value = ""
      setOlder([])
    },
    onError: (e: ApiError) => toast.error(e.message),
  })

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [data])

  return (
    <div className="flex h-[75vh] flex-col rounded-lg border border-[#3b3d43] bg-[#313338]">
      <div className="flex items-center justify-between border-b border-[#3b3d43] px-4 py-2.5">
        <div className="flex items-center gap-1.5 text-[#f2f3f5]">{channel.type === "text" ? <Hash className="size-4 text-[#949ba4]" /> : channel.type === "thread" ? <GitBranch className="size-4 text-[#949ba4]" /> : <Volume2 className="size-4 text-[#949ba4]" />}<span className="font-semibold">{channel.name}</span></div>
        <a href={`/api/v1/admin/discord-bot/viewer/channels/${channel.id}/export`} download>
          <Button size="sm" variant="outline" className="h-7 gap-1 text-xs"><Download className="size-3.5" /> Экспорт в JSON</Button>
        </a>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-2 py-2">
        {(data?.messages.length ?? 0) >= 50 && (
          <div className="flex justify-center pb-2">
            <Button size="sm" variant="ghost" className="h-6 text-xs text-[#949ba4]" disabled={loadOlder.isPending} onClick={() => loadOlder.mutate()}>
              {loadOlder.isPending ? <Loader2 className="size-3 animate-spin" /> : "Загрузить более старые"}
            </Button>
          </div>
        )}
        {isLoading && <div className="p-4 text-center text-sm text-[#949ba4]">Загрузка…</div>}
        {!isLoading && !ordered.length && <div className="p-4 text-center text-sm text-[#949ba4]">Сообщений нет</div>}
        {ordered.map((m) => <MessageRow key={m.id} msg={m} onReply={canSend && channel.can_send ? setReplyTarget : undefined} />)}
      </div>
      {canSend && channel.can_send && (
        <div className="border-t border-[#3b3d43] p-3">
          {replyTarget && (
            <div className="mb-2 flex items-start gap-2 rounded border-l-2 border-[#5865f2] bg-[#2b2d31] px-2 py-1.5 text-xs text-[#b5bac1]">
              <div className="min-w-0 flex-1">
                <div className="font-medium text-[#f2f3f5]">Ответ на {replyTarget.author.display_name}</div>
                <div className="truncate">{replyTarget.content || "Вложение"}</div>
              </div>
              <button type="button" title="Отменить реплай" aria-label="Отменить реплай" onClick={() => setReplyTarget(null)}>
                <X className="size-3.5" />
              </button>
            </div>
          )}
          {file && (
            <div className="mb-1.5 flex items-center gap-2 text-xs text-[#949ba4]">
              <Paperclip className="size-3.5" /> {file.name}
              <button onClick={() => { setFile(null); if (fileInputRef.current) fileInputRef.current.value = "" }}><X className="size-3.5" /></button>
            </div>
          )}
          <div className="flex items-end gap-2">
            <input ref={fileInputRef} type="file" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <Button size="icon" variant="ghost" className="size-9 shrink-0 text-[#949ba4]" onClick={() => fileInputRef.current?.click()}>
              <Paperclip className="size-4" />
            </Button>
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={`Сообщение в #${channel.name}`}
              className="min-h-9 flex-1 resize-none border-[#3b3d43] bg-[#383a40] text-sm text-[#dbdee1]"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (draft.trim() || file) send.mutate() } }}
            />
            <Button size="icon" className="size-9 shrink-0" disabled={send.isPending || (!draft.trim() && !file)} onClick={() => send.mutate()}>
              {send.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            </Button>
          </div>
        </div>
      )}
      {!(canSend && channel.can_send) && (
        <div className="border-t border-[#3b3d43] p-3 text-center text-xs text-[#949ba4]">
          {channel.can_send ? "Нет права на отправку сообщений (broadcast)" : "Боту не разрешено писать в этот канал"}
        </div>
      )}
    </div>
  )
}

// ── Участники ────────────────────────────────────────────────────────────

function MemberDetailDialog({ discordId, roles, onClose }: { discordId: string; roles: RoleRow[]; onClose: () => void }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ["discord-viewer-member", discordId],
    queryFn: () => api.get<MemberDetail>(`/api/v1/admin/discord-bot/viewer/members/${discordId}`),
  })
  const [pending, setPending] = useState<Set<string>>(new Set())

  const updateRoles = useMutation({
    mutationFn: (body: { add: string[]; remove: string[] }) => api.put(`/api/v1/admin/discord-bot/viewer/members/${discordId}/roles`, body),
    onSuccess: () => {
      toast.success("Роли обновлены")
      queryClient.invalidateQueries({ queryKey: ["discord-viewer-member", discordId] })
      queryClient.invalidateQueries({ queryKey: ["discord-viewer-members"] })
      setPending(new Set())
    },
    onError: (e: ApiError) => toast.error(e.message),
  })

  const toggleRole = (roleId: string, currentlyHeld: boolean) => {
    updateRoles.mutate(currentlyHeld ? { add: [], remove: [roleId] } : { add: [roleId], remove: [] })
    setPending((p) => new Set(p).add(roleId))
  }

  if (!data) return null
  const heldRoles = new Set(data.role_ids)

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle className="flex items-center gap-2">
          <Avatar className="size-8"><AvatarImage src={data.avatar_url ?? undefined} /><AvatarFallback>{initials(data.display_name ?? data.username ?? "?")}</AvatarFallback></Avatar>
          {data.display_name ?? data.username}
        </DialogTitle></DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="text-xs text-muted-foreground">
            Discord ID: <span className="font-mono">{data.discord_id}</span>
            {data.joined_at && <> · на сервере с {new Date(data.joined_at).toLocaleDateString("ru-RU")}</>}
          </div>

          {data.steam ? (
            <div className="flex items-center gap-2 rounded-md border p-2">
              {data.steam.avatar_url && <Avatar className="size-8"><AvatarImage src={data.steam.avatar_url} /></Avatar>}
              <div>
                <div className="flex items-center gap-1 font-medium">
                  {data.steam.player_name ?? "Игрок"}
                  {data.steam.confirmed && <span title="Привязка подтверждена"><ShieldCheck className="size-3.5 text-emerald-500" /></span>}
                </div>
                <a className="font-mono text-xs text-[#00a8fc] hover:underline" target="_blank" rel="noreferrer"
                   href={`https://steamcommunity.com/profiles/${data.steam.steamid}`}>{data.steam.steamid}</a>
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">Steam-профиль не привязан</p>
          )}

          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">Роли</div>
            {!roles.length && <p className="text-xs text-muted-foreground">Список ролей сервера недоступен</p>}
            <div className="max-h-56 space-y-1 overflow-y-auto">
              {roles.map((r) => (
                <label key={r.id} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-muted">
                  <Checkbox
                    checked={heldRoles.has(r.id)}
                    disabled={!data.can_manage_roles || r.managed || (updateRoles.isPending && pending.has(r.id))}
                    onCheckedChange={() => toggleRole(r.id, heldRoles.has(r.id))}
                  />
                  <span className="size-2.5 rounded-full" style={{ background: r.color ?? "#99aab5" }} />
                  <span>{r.name}</span>
                  {r.managed && <span className="text-[10px] text-muted-foreground">(управляется интеграцией)</span>}
                </label>
              ))}
            </div>
            {!data.can_manage_roles && <p className="mt-1 text-xs text-muted-foreground">Нет права manage_config — роли только для просмотра.</p>}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function MembersPanel() {
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["discord-viewer-members", search],
    queryFn: () => api.get<{ total: number; members: MemberRow[] }>(`/api/v1/admin/discord-bot/viewer/members?search=${encodeURIComponent(search)}&limit=200`),
  })
  const { data: rolesData } = useQuery({
    queryKey: ["discord-viewer-roles"],
    queryFn: () => api.get<{ roles: RoleRow[] }>("/api/v1/admin/discord-bot/viewer/roles"),
  })

  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="relative max-w-xs">
          <Search className="absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" />
          <Input className="pl-8" placeholder="Поиск по имени или ID…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {isLoading && <p className="text-sm text-muted-foreground">Загрузка…</p>}
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
          {data?.members.map((mem) => (
            <button
              key={mem.discord_id}
              onClick={() => setSelected(mem.discord_id)}
              className="flex items-center gap-2 rounded-md border p-2 text-left hover:border-primary"
            >
              <Avatar className="size-9"><AvatarImage src={mem.avatar_url ?? undefined} /><AvatarFallback>{initials(mem.display_name ?? mem.username ?? "?")}</AvatarFallback></Avatar>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{mem.display_name ?? mem.username}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {mem.steam ? (mem.steam.player_name ?? "Steam привязан") : "Steam не привязан"}
                </div>
              </div>
            </button>
          ))}
        </div>
        {data && !data.members.length && <p className="text-sm text-muted-foreground">Никого не найдено</p>}
        {data && data.total > data.members.length && (
          <p className="text-xs text-muted-foreground">Показано {data.members.length} из {data.total} — уточните поиск</p>
        )}
      </CardContent>
      {selected && <MemberDetailDialog discordId={selected} roles={rolesData?.roles ?? []} onClose={() => setSelected(null)} />}
    </Card>
  )
}

// ── Корень вкладки ──────────────────────────────────────────────────────

export function DiscordViewerTab() {
  const { data: tree, isLoading, error } = useQuery({
    queryKey: ["discord-viewer-tree"],
    queryFn: () => api.get<ChannelTree>("/api/v1/admin/discord-bot/viewer/tree"),
    retry: false,
  })
  const [activeChannel, setActiveChannel] = useState<string | null>(null)
  const [tab, setTab] = useState<"chat" | "members">("chat")

  useEffect(() => {
    if (tree && !activeChannel && tree.channels.length) setActiveChannel(tree.channels[0].id)
  }, [tree, activeChannel])

  if (isLoading) return <p className="text-sm text-muted-foreground">Загрузка…</p>
  if (error) {
    const msg = error instanceof ApiError ? error.message : "Не удалось связаться с ботом"
    return <p className="text-sm text-destructive">{msg}</p>
  }
  if (!tree) return null

  const channel = tree.channels.find((c) => c.id === activeChannel) ?? null
  const byCategory = new Map<string | null, TreeChannel[]>()
  for (const c of tree.channels) {
    const key = c.category_id
    if (!byCategory.has(key)) byCategory.set(key, [])
    byCategory.get(key)!.push(c)
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-1.5">
        <Button size="sm" variant={tab === "chat" ? "default" : "outline"} onClick={() => setTab("chat")}>Чат</Button>
        <Button size="sm" variant={tab === "members" ? "default" : "outline"} onClick={() => setTab("members")}>Участники</Button>
      </div>

      {tab === "chat" && (
        <div className="grid grid-cols-[220px_1fr] gap-3">
          <div className="h-[75vh] overflow-y-auto rounded-lg border border-[#3b3d43] bg-[#2b2d31] p-2">
            <div className="mb-2 truncate px-1 text-sm font-semibold text-[#f2f3f5]">{tree.guild.name}</div>
            {tree.categories.map((cat) => (
              <div key={cat.id} className="mb-2">
                <div className="px-1 text-[11px] font-semibold uppercase tracking-wide text-[#949ba4]">{cat.name}</div>
                {(byCategory.get(cat.id) ?? []).map((c) => (
                  <button
                    key={c.id}
                    onClick={() => setActiveChannel(c.id)}
                    className={`flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-sm ${c.id === activeChannel ? "bg-[#404249] text-white" : "text-[#949ba4] hover:bg-[#35373c]"}`}
                  >
                    {c.type === "text" ? <Hash className="size-3.5 shrink-0" /> : c.type === "thread" ? <GitBranch className="size-3.5 shrink-0" /> : <Volume2 className="size-3.5 shrink-0" />} <span className="truncate">{c.name}</span>
                  </button>
                ))}
              </div>
            ))}
            {(byCategory.get(null) ?? []).map((c) => (
              <button
                key={c.id}
                onClick={() => setActiveChannel(c.id)}
                className={`flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-sm ${c.id === activeChannel ? "bg-[#404249] text-white" : "text-[#949ba4] hover:bg-[#35373c]"}`}
              >
                {c.type === "text" ? <Hash className="size-3.5 shrink-0" /> : c.type === "thread" ? <GitBranch className="size-3.5 shrink-0" /> : <Volume2 className="size-3.5 shrink-0" />} <span className="truncate">{c.name}</span>
              </button>
            ))}
            {!tree.channels.length && <p className="px-1 text-xs text-muted-foreground">Боту не видно каналов с текстовым чатом</p>}
          </div>
          {channel ? <ChatPanel channel={channel} canSend={tree.can_send} /> : (
            <div className="flex h-[75vh] items-center justify-center rounded-lg border text-sm text-muted-foreground">Выберите канал слева</div>
          )}
        </div>
      )}

      {tab === "members" && <MembersPanel />}
    </div>
  )
}
