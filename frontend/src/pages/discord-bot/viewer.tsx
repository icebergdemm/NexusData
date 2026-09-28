import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Checkbox } from "@/components/ui/checkbox"
import { Hash, Volume2, GitBranch, Paperclip, Send, Download, Search, Loader2, X, ShieldCheck, Reply, Headphones, ChevronDown, Users, MoreHorizontal, Copy, Link2, Trash2, Radio } from "lucide-react"
import { api, ApiError } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { applyVoiceState, discordSearchSnowflake, groupMembersByRole, groupThreadsByParent, mergeAuditEntries, mergeViewerChannels, parseDiscordSearch } from "./viewer-utils"

// ── Типы ─────────────────────────────────────────────────────────────────

interface VoiceMember { id: string; display_name: string; avatar_url: string | null; self_mute: boolean; self_deaf: boolean; mute: boolean; deaf: boolean; video: boolean; streaming: boolean }
interface TreeChannel { id: string; name: string; position: number; can_send: boolean; category_id: string | null; parent_id?: string | null; archived?: boolean; voice_members?: VoiceMember[]; type: "text" | "thread" | "voice" | "stage" | "forum" | "media" }
interface TreeCategory { id: string; name: string; position: number }
interface ArchivePageState { before: string | null; hasMore: boolean; loaded: boolean; loading: boolean }
interface ChannelTree {
  guild: { id: string; name: string; icon_url: string | null }
  categories: TreeCategory[]
  channels: TreeChannel[]
  can_send: boolean
  can_manage_roles: boolean
  can_manage_config?: boolean
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
  channel_id?: string; reactions?: { emoji: string; count: number; me: boolean }[]; pinned?: boolean
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

function MessageRow({ msg, guildId, channelId, onReply, onReaction, onReactionUsers, onPin, onDelete, onThread, canManage }: { msg: DiscordMessage; guildId: string; channelId: string; onReply?: (msg: DiscordMessage) => void; onReaction?: (msg: DiscordMessage, emoji: string) => void; onReactionUsers?: (msg: DiscordMessage, emoji: string) => void; onPin?: (msg: DiscordMessage) => void; onDelete?: (msg: DiscordMessage) => void; onThread?: (msg: DiscordMessage) => void; canManage?: boolean }) {
  const time = formatBrowserDate(msg.created_at)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (menuOpen) menuRef.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus()
  }, [menuOpen])
  return (
    <div data-message-id={msg.id} className="group relative flex gap-2.5 rounded px-2 py-1.5 hover:bg-[#2e3035]" onContextMenu={(event) => { event.preventDefault(); setMenuOpen(true) }}>
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
        {!!msg.reactions?.length && <div className="mt-1 flex flex-wrap gap-1">{msg.reactions.map((reaction) => <span key={reaction.emoji} className={`inline-flex items-center rounded-md border text-xs ${reaction.me ? "border-[#5865f2] bg-[#404249]" : "border-[#3b3d43] bg-[#2b2d31]"}`}><button type="button" title={`Переключить реакцию ${reaction.emoji}`} onClick={() => onReaction?.(msg, reaction.emoji)} className="rounded-l px-1.5 py-0.5">{reaction.emoji}</button><button type="button" title={`Показать ${reaction.count} участников реакции`} onClick={() => onReactionUsers?.(msg, reaction.emoji)} className="rounded-r px-1 py-0.5 text-[#b5bac1] hover:bg-[#404249]">{reaction.count}</button></span>)}</div>}
        <button type="button" aria-label="Действия сообщения" aria-haspopup="menu" onClick={() => setMenuOpen((open) => !open)} className="absolute right-2 top-1 hidden rounded bg-[#1e1f22] p-1 text-[#b5bac1] group-hover:block focus:block"><MoreHorizontal className="size-4" /></button>
        {menuOpen && <div ref={menuRef} role="menu" tabIndex={-1} className="absolute right-2 top-7 z-20 min-w-48 rounded-md border border-[#3b3d43] bg-[#111214] p-1 shadow-xl" onKeyDown={(event) => { if (event.key === "Escape") setMenuOpen(false) }} onMouseLeave={() => setMenuOpen(false)}>
          <div className="flex gap-1 px-1 py-1">{["✅", "👍", "❤️", "🔥"].map((emoji) => <button key={emoji} role="menuitem" title={`Добавить реакцию ${emoji}`} onClick={() => { onReaction?.(msg, emoji); setMenuOpen(false) }} className="rounded px-1.5 py-1 hover:bg-[#35373c]">{emoji}</button>)}</div>
          {onReply && <button role="menuitem" onClick={() => { onReply(msg); setMenuOpen(false) }} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-[#35373c]"><Reply className="size-3.5" />Ответить</button>}
          <button role="menuitem" onClick={() => { void navigator.clipboard.writeText(msg.content); setMenuOpen(false) }} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-[#35373c]"><Copy className="size-3.5" />Копировать текст</button>
          <button role="menuitem" onClick={() => { void navigator.clipboard.writeText(msg.id); setMenuOpen(false) }} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-[#35373c]"><Link2 className="size-3.5" />Копировать ID</button>
          <button role="menuitem" onClick={() => { void navigator.clipboard.writeText(`https://discord.com/channels/${guildId}/${channelId}/${msg.id}`); setMenuOpen(false) }} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-[#35373c]">Копировать ссылку</button>
          {canManage && onPin && <button role="menuitem" onClick={() => { onPin(msg); setMenuOpen(false) }} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-[#35373c]">{msg.pinned ? "Открепить" : "Закрепить"}</button>}
          {canManage && onThread && <button role="menuitem" onClick={() => { onThread(msg); setMenuOpen(false) }} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-[#35373c]"><GitBranch className="size-3.5" />Создать ветку</button>}
          {canManage && onDelete && <button role="menuitem" onClick={() => { onDelete(msg); setMenuOpen(false) }} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#ed4245] hover:bg-[#35373c]"><Trash2 className="size-3.5" />Удалить…</button>}
        </div>}
      </div>
    </div>
  )
}

function ChatPanel({ channel, guildId, canSend, canManage, jumpTo }: { channel: TreeChannel; guildId: string; canSend: boolean; canManage: boolean; jumpTo?: string | null }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [replyTarget, setReplyTarget] = useState<DiscordMessage | null>(null)
  const [reactionUsers, setReactionUsers] = useState<{ emoji: string; users: Author[] } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["discord-viewer-messages", channel.id, jumpTo],
    queryFn: () => api.get<{ messages: DiscordMessage[] }>(`/api/v1/admin/discord-bot/viewer/channels/${channel.id}/messages?limit=50${jumpTo ? `&around=${jumpTo}` : ""}`),
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

    const updateCache = (event: { type: string; channel_id?: string; message?: Partial<DiscordMessage> & { id: string }; message_id?: string; voice?: { member_id: string; connected: boolean; member?: VoiceMember } }) => {
      if (event.type === "resync") {
      queryClient.invalidateQueries({ queryKey: ["discord-viewer-messages", channel.id] })
        queryClient.invalidateQueries({ queryKey: ["discord-viewer-tree"] })
        return
      }
      if (event.type === "reaction_update") {
        queryClient.invalidateQueries({ queryKey: ["discord-viewer-messages", channel.id] })
        return
      }
      if (event.type === "voice_state" && event.voice) {
        queryClient.setQueryData<ChannelTree>(["discord-viewer-tree"], (old) => old ? {
          ...old, channels: applyVoiceState(old.channels, { channel_id: channel.id, voice: event.voice! }) as TreeChannel[],
        } : old)
        return
      }
        queryClient.setQueryData<{ messages: DiscordMessage[] }>(["discord-viewer-messages", channel.id, jumpTo], (old) => {
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
  }, [channel.id, queryClient, jumpTo])

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
      queryClient.setQueryData<{ messages: DiscordMessage[] }>(["discord-viewer-messages", channel.id, jumpTo], (old) => ({
        messages: [sentWithPreview, ...(old?.messages ?? []).filter((message) => message.id !== sent.id)],
      }))
      setDraft(""); setFile(null)
      setReplyTarget(null)
      if (fileInputRef.current) fileInputRef.current.value = ""
      setOlder([])
    },
    onError: (e: ApiError) => toast.error(e.message),
  })

  const toggleReaction = useMutation({
    mutationFn: ({ msg, emoji }: { msg: DiscordMessage; emoji: string }) => api.post(`/api/v1/admin/discord-bot/viewer/channels/${msg.channel_id ?? channel.id}/messages/${msg.id}/reactions/toggle`, { emoji }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["discord-viewer-messages", channel.id] }),
    onError: (e: ApiError) => toast.error(e.message),
  })
  const showReactionUsers = async (msg: DiscordMessage, emoji: string) => {
    try {
      const result = await api.get<{ users: Author[] }>(`/api/v1/admin/discord-bot/viewer/channels/${msg.channel_id ?? channel.id}/messages/${msg.id}/reactions/${encodeURIComponent(emoji)}/users?limit=100`)
      setReactionUsers({ emoji, users: result.users })
    } catch (e) { toast.error((e as ApiError).message) }
  }
  const pinMessage = async (msg: DiscordMessage) => {
    try { await api.put(`/api/v1/admin/discord-bot/viewer/channels/${msg.channel_id ?? channel.id}/messages/${msg.id}/pin`, { pinned: !msg.pinned }); toast.success(msg.pinned ? "Сообщение откреплено" : "Сообщение закреплено"); await queryClient.invalidateQueries({ queryKey: ["discord-viewer-messages", channel.id] }) }
    catch (e) { toast.error((e as ApiError).message) }
  }
  const deleteMessage = async (msg: DiscordMessage) => {
    if (!window.confirm("Удалить сообщение? Это действие нельзя отменить.")) return
    try { await api.delete(`/api/v1/admin/discord-bot/viewer/channels/${msg.channel_id ?? channel.id}/messages/${msg.id}`, { reason: "Удалено администратором NexusData" }); toast.success("Сообщение удалено"); await queryClient.invalidateQueries({ queryKey: ["discord-viewer-messages", channel.id] }) }
    catch (e) { toast.error((e as ApiError).message) }
  }
  const createThread = async (msg: DiscordMessage) => {
    const name = window.prompt("Название новой ветки")
    if (!name?.trim()) return
    try { await api.post(`/api/v1/admin/discord-bot/viewer/channels/${msg.channel_id ?? channel.id}/messages/${msg.id}/thread`, { name }); toast.success("Ветка создана"); await queryClient.invalidateQueries({ queryKey: ["discord-viewer-tree"] }) }
    catch (e) { toast.error((e as ApiError).message) }
  }

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [data])
  useEffect(() => {
    if (!jumpTo) return
    document.querySelector(`[data-message-id="${CSS.escape(jumpTo)}"]`)?.scrollIntoView({ block: "center" })
  }, [jumpTo, ordered])

  return (
    <div className="discord-chat-panel flex min-h-0 flex-1 flex-col bg-[#313338]">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-[#1e1f22] px-4">
        <div className="flex items-center gap-1.5 text-[#f2f3f5]">{channel.type === "text" ? <Hash className="size-4 text-[#949ba4]" /> : channel.type === "thread" ? <GitBranch className="size-4 text-[#949ba4]" /> : <Volume2 className="size-4 text-[#949ba4]" />}<span className="font-semibold">{channel.name}</span></div>
        <a href={`/api/v1/admin/discord-bot/viewer/channels/${channel.id}/export`} download>
          <Button size="icon" variant="ghost" title="Экспорт истории канала" aria-label="Экспорт истории канала" className="size-8 text-[#b5bac1]"><Download className="size-4" /></Button>
        </a>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {(data?.messages.length ?? 0) >= 50 && (
          <div className="flex justify-center pb-2">
            <Button size="sm" variant="ghost" className="h-6 text-xs text-[#949ba4]" disabled={loadOlder.isPending} onClick={() => loadOlder.mutate()}>
              {loadOlder.isPending ? <Loader2 className="size-3 animate-spin" /> : "Загрузить более старые"}
            </Button>
          </div>
        )}
        {isLoading && <div className="p-4 text-center text-sm text-[#949ba4]">Загрузка…</div>}
        {!isLoading && !ordered.length && <div className="p-4 text-center text-sm text-[#949ba4]">Сообщений нет</div>}
        {ordered.map((m) => <MessageRow key={m.id} msg={m} guildId={guildId} channelId={channel.id} canManage={canManage} onReaction={canManage ? (msg, emoji) => toggleReaction.mutate({ msg, emoji }) : undefined} onReactionUsers={showReactionUsers} onPin={pinMessage} onDelete={deleteMessage} onThread={createThread} onReply={canSend && channel.can_send ? setReplyTarget : undefined} />)}
      </div>
      {canSend && channel.can_send && (
        <div className="shrink-0 border-t border-[#1e1f22] p-3">
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
        <div className="shrink-0 border-t border-[#1e1f22] p-3 text-center text-xs text-[#949ba4]">
          {channel.can_send ? "Нет права на отправку сообщений (broadcast)" : "Боту не разрешено писать в этот канал"}
        </div>
      )}
      {reactionUsers && <Dialog open onOpenChange={(open) => !open && setReactionUsers(null)}><DialogContent className="max-w-sm"><DialogHeader><DialogTitle>Реакция {reactionUsers.emoji}</DialogTitle></DialogHeader><div className="max-h-72 space-y-1 overflow-y-auto">{reactionUsers.users.map((user) => <div key={user.id} className="flex items-center gap-2 rounded p-1"><Avatar className="size-7"><AvatarImage src={user.avatar_url ?? undefined} /><AvatarFallback>{initials(user.display_name)}</AvatarFallback></Avatar><span className="text-sm">{user.display_name}</span></div>)}{!reactionUsers.users.length && <p className="text-xs text-muted-foreground">Участников не найдено.</p>}</div></DialogContent></Dialog>}
    </div>
  )
}

// ── Участники ────────────────────────────────────────────────────────────

function MemberDetailDialog({ discordId, roles, voiceTargets, onVoiceMove, onClose }: { discordId: string; roles: RoleRow[]; voiceTargets: TreeChannel[]; onVoiceMove: (memberId: string, channelId: string) => void; onClose: () => void }) {
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
          {!!voiceTargets.length && <div className="flex gap-2 border-t pt-3"><select aria-label="Голосовой канал назначения" id="voice-target" className="min-w-0 flex-1 rounded-md border bg-background px-2 text-xs"><option value="">Переместить в голосовой канал…</option>{voiceTargets.map((target) => <option key={target.id} value={target.id}>{target.name}</option>)}</select><Button size="sm" onClick={() => { const target = document.getElementById("voice-target") as HTMLSelectElement; if (target.value) onVoiceMove(discordId, target.value) }}>Переместить</Button></div>}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function MembersPanel({ onSelect, draggingMember, setDraggingMember, voiceMemberIds }: { onSelect: (id: string) => void; draggingMember: string | null; setDraggingMember: (id: string | null) => void; voiceMemberIds: Set<string> }) {
  const [search, setSearch] = useState("")
  const { data, isLoading } = useQuery({
    queryKey: ["discord-viewer-members", search],
    queryFn: () => api.get<{ total: number; members: MemberRow[] }>(`/api/v1/admin/discord-bot/viewer/members?search=${encodeURIComponent(search)}&limit=200`),
  })
  const { data: rolesData } = useQuery({
    queryKey: ["discord-viewer-roles"],
    queryFn: () => api.get<{ roles: RoleRow[] }>("/api/v1/admin/discord-bot/viewer/roles"),
  })
  const groups = useMemo(() => groupMembersByRole(data?.members ?? [], rolesData?.roles ?? []), [data, rolesData])

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-[#1e1f22] bg-[#2b2d31]">
      <div className="shrink-0 border-b border-[#1e1f22] p-2"><div className="relative"><Search className="absolute left-2.5 top-2.5 size-3.5 text-[#949ba4]" /><Input aria-label="Поиск участников" className="h-8 border-0 bg-[#1e1f22] pl-8 text-xs" placeholder="Найти участника" value={search} onChange={(e) => setSearch(e.target.value)} /></div></div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2 text-[#dbdee1]">
        {isLoading && <p className="p-2 text-xs text-[#949ba4]">Загрузка…</p>}
        {groups.map(({ role, members }) => <section key={role?.id ?? "none"} className="mb-3">
          <div className="mb-1 flex items-center gap-1 px-1 text-[10px] font-bold uppercase tracking-wide text-[#949ba4]"><ChevronDown className="size-3" />{role && <span className="size-2 rounded-full" style={{ background: role.color ?? "#99aab5" }} />}{role?.name ?? "Без роли"} — {members.length}</div>
          {members.map((mem) => <button key={mem.discord_id} draggable={voiceMemberIds.has(mem.discord_id)} onDragStart={() => setDraggingMember(mem.discord_id)} onDragEnd={() => setDraggingMember(null)} onClick={() => onSelect(mem.discord_id)} className={`flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-[#35373c] ${draggingMember === mem.discord_id ? "opacity-40" : ""}`}>
            <Avatar className="size-7"><AvatarImage src={mem.avatar_url ?? undefined} /><AvatarFallback className="text-[10px]">{initials(mem.display_name ?? mem.username ?? "?")}</AvatarFallback></Avatar>
            <span className="min-w-0 flex-1 truncate text-xs">{mem.display_name ?? mem.username}</span>
          </button>)}
        </section>)}
        {!isLoading && !data?.members.length && <p className="p-2 text-xs text-[#949ba4]">Участников не найдено</p>}
      </div>
      {data?.total && data.total > data.members.length ? <div className="border-t border-[#3b3d43] p-2 text-[10px] text-[#949ba4]">Показано {data.members.length} из {data.total}</div> : null}
    </aside>
  )
}

function VoiceEventsBridge({ channelId }: { channelId: string }) {
  const queryClient = useQueryClient()
  useEffect(() => {
    let stopped = false
    let socket: WebSocket | null = null
    let timer: number | undefined
    const connect = () => {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
      socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/admin/discord-bot/viewer/events?channel_id=${encodeURIComponent(channelId)}`)
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as { type: string; voice?: { member_id: string; connected: boolean; member?: VoiceMember } }
          if (event.type === "resync") void queryClient.invalidateQueries({ queryKey: ["discord-viewer-tree"] })
          if (event.type === "voice_state" && event.voice) queryClient.setQueryData<ChannelTree>(["discord-viewer-tree"], (old) => old ? { ...old, channels: applyVoiceState(old.channels, { channel_id: channelId, voice: event.voice! }) } : old)
        } catch { /* malformed Gateway payload is repaired by tree refresh */ }
      }
      socket.onclose = () => { if (!stopped) timer = window.setTimeout(connect, 2000) }
    }
    connect()
    return () => { stopped = true; if (timer) window.clearTimeout(timer); socket?.close() }
  }, [channelId, queryClient])
  return null
}

interface AuditEntry { id: string; action_type: number; action: string; actor: { id: string; name: string | null } | null; target: { id: string; name: string | null } | null; created_at: string; reason: string | null; changes: { key: string; old_value: unknown; new_value: unknown }[] }
function AuditLogPanel({ enabled }: { enabled: boolean }) {
  const [open, setOpen] = useState(true)
  const [actorFilter, setActorFilter] = useState("")
  const [actionFilter, setActionFilter] = useState("")
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [before, setBefore] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState("")
  const load = useMutation({
    mutationFn: (cursor: string | null) => { const params = new URLSearchParams({ limit: "50" }); if (cursor) params.set("before", cursor); if (actorFilter) params.set("user_id", actorFilter); if (actionFilter) params.set("action_type", actionFilter); return api.get<{ entries: AuditEntry[]; before: string | null; has_more: boolean }>(`/api/v1/admin/discord-bot/viewer/audit-log?${params}`) },
    onSuccess: (page, cursor) => { setEntries((old) => cursor ? mergeAuditEntries(old, page.entries) : mergeAuditEntries([], page.entries)); setBefore(page.before); setHasMore(page.has_more); setError("") },
    onError: (e: ApiError) => setError(e.message),
  })
  const loadAudit = load.mutate
  useEffect(() => {
    if (!open || !enabled) return
    loadAudit(null)
    const timer = window.setInterval(() => loadAudit(null), 60000)
    return () => window.clearInterval(timer)
  }, [open, enabled, loadAudit, actorFilter, actionFilter])
  return <section className={`flex shrink-0 flex-col border-t border-[#1e1f22] bg-[#313338] text-[#dbdee1] ${open ? "h-[210px]" : "h-9"}`}>
    <button type="button" onClick={() => setOpen((value) => !value)} className="flex h-9 w-full shrink-0 items-center gap-2 px-3 text-left text-sm font-semibold"><Radio className="size-4 text-[#5865f2]" />Журнал аудита Discord <span className="ml-auto text-xs font-normal text-[#949ba4]">{open ? "Свернуть" : "Развернуть"}</span></button>
    {open && <div className="flex min-h-0 flex-1 flex-col border-t border-[#1e1f22] p-2">
      {!enabled && <p className="p-3 text-xs text-[#949ba4]">Нужны manage_config сайта и VIEW_AUDIT_LOG у бота.</p>}
      {enabled && <div className="mb-2 flex shrink-0 gap-2"><Input aria-label="Фильтр Audit Log по actor ID" placeholder="Actor ID" value={actorFilter} onChange={(event) => setActorFilter(event.target.value)} className="h-7 max-w-48 bg-[#2b2d31] text-xs" /><Input aria-label="Фильтр Audit Log по типу действия" placeholder="Тип действия ID" value={actionFilter} onChange={(event) => setActionFilter(event.target.value)} className="h-7 max-w-36 bg-[#2b2d31] text-xs" /><Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => load.mutate(null)}>Фильтр</Button></div>}
      {enabled && error && <p className="p-3 text-xs text-[#ed4245]">{error}</p>}
      {enabled && load.isPending && !entries.length && <p className="p-3 text-xs text-[#949ba4]">Загрузка Audit Log…</p>}
      {enabled && !load.isPending && !error && !entries.length && <p className="p-3 text-xs text-[#949ba4]">Записей нет или Discord Audit Log недоступен.</p>}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {entries.map((entry) => <article key={entry.id} className="flex gap-2 border-b border-[#1e1f22] px-2 py-2 text-xs last:border-0"><Avatar className="size-7"><AvatarFallback>{initials(entry.actor?.name ?? "?")}</AvatarFallback></Avatar><div className="min-w-0 flex-1"><div><strong className="text-[#f2f3f5]">{entry.actor?.name ?? "Неизвестный участник"}</strong> · {entry.action.replaceAll("_", " ")} · {entry.target?.name ?? entry.target?.id ?? "цель удалена"}<time className="ml-2 text-[10px] text-[#949ba4]">{formatBrowserDate(entry.created_at)}</time></div>{entry.reason && <div className="mt-0.5 text-[#949ba4]">Причина: {entry.reason}</div>}{!!entry.changes.length && <details className="mt-1 text-[#949ba4]"><summary className="cursor-pointer">Изменения ({entry.changes.length})</summary>{entry.changes.map((change) => <div key={change.key}>{change.key}: {String(change.old_value ?? "—")} → {String(change.new_value ?? "—")}</div>)}</details>}</div></article>)}
        {enabled && hasMore && <Button size="sm" variant="ghost" className="mt-2 h-7 text-xs" disabled={load.isPending} onClick={() => load.mutate(before)}>{load.isPending ? "Загрузка…" : "Загрузить старые записи"}</Button>}
      </div>
    </div>}
  </section>
}

// ── Корень вкладки ──────────────────────────────────────────────────────

export function DiscordViewerTab() {
  const { data: tree, isLoading, error } = useQuery({
    queryKey: ["discord-viewer-tree"],
    queryFn: () => api.get<ChannelTree>("/api/v1/admin/discord-bot/viewer/tree"),
    retry: false,
  })
  const queryClient = useQueryClient()
  const { data: rolesData } = useQuery({ queryKey: ["discord-viewer-roles"], queryFn: () => api.get<{ roles: RoleRow[] }>("/api/v1/admin/discord-bot/viewer/roles") })
  const [activeChannel, setActiveChannel] = useState<string | null>(null)
  const [selectedMember, setSelectedMember] = useState<string | null>(null)
  const [draggingMember, setDraggingMember] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [searchPage, setSearchPage] = useState(0)
  const [searchResults, setSearchResults] = useState<{ messages: DiscordMessage[]; indexing?: boolean; retry_after?: number | null; has_more?: boolean } | null>(null)
  const [searchError, setSearchError] = useState("")
  const [jumpTo, setJumpTo] = useState<string | null>(null)
  const [channelMenu, setChannelMenu] = useState<{ id: string; x: number; y: number } | null>(null)
  const [memberAccess, setMemberAccess] = useState<string | null>(null)
  const [archivePages, setArchivePages] = useState<Record<string, ArchivePageState>>({})
  const channelMenuRef = useRef<HTMLDivElement>(null)
  const threadsByParent = useMemo(() => groupThreadsByParent(tree?.channels ?? []), [tree?.channels])

  useEffect(() => {
    if (tree && !activeChannel && tree.channels.length) setActiveChannel(tree.channels[0].id)
  }, [tree, activeChannel])
  useEffect(() => {
    if (channelMenu) channelMenuRef.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus()
  }, [channelMenu])

  if (isLoading) return <p className="text-sm text-muted-foreground">Загрузка…</p>
  if (error) {
    const msg = error instanceof ApiError ? error.message : "Не удалось связаться с ботом"
    return <p className="text-sm text-destructive">{msg}</p>
  }
  if (!tree) return null

  const channel = tree.channels.find((c) => c.id === activeChannel) ?? null
  const byCategory = new Map<string | null, TreeChannel[]>()
  for (const c of tree.channels.filter((item) => item.type !== "thread")) {
    const key = c.category_id
    if (!byCategory.has(key)) byCategory.set(key, [])
    byCategory.get(key)!.push(c)
  }

  const selectChannel = (item: TreeChannel) => { setActiveChannel(item.id); setJumpTo(null); setSearchResults(null); setChannelMenu(null) }
  const moveMember = async (memberId: string, targetId: string) => {
    try { await api.post(`/api/v1/admin/discord-bot/viewer/members/${memberId}/voice`, { target_channel_id: targetId }); toast.success("Участник перемещён"); queryClient.invalidateQueries({ queryKey: ["discord-viewer-tree"] }) }
    catch (e) { toast.error((e as ApiError).message) }
    finally { setDraggingMember(null) }
  }
  const loadArchivedThreads = async (parent: TreeChannel) => {
    const sources = parent.type === "text" ? ["public", "joined_private"] : ["public"]
    const pending = sources.filter((source) => {
      const state = archivePages[`${parent.id}:${source}`]
      return !state?.loaded || state.hasMore
    })
    if (!pending.length) return
    setArchivePages((current) => Object.fromEntries([
      ...Object.entries(current),
      ...pending.map((source) => {
        const key = `${parent.id}:${source}`
        return [key, { ...(current[key] ?? { before: null, hasMore: false, loaded: false }), loading: true }]
      }),
    ]))
    await Promise.all(pending.map(async (source) => {
      const key = `${parent.id}:${source}`
      const params = new URLSearchParams({ source })
      const before = archivePages[key]?.before
      if (before) params.set("before", before)
      try {
        const page = await api.get<{ threads: TreeChannel[]; before: string | null; has_more: boolean }>(`/api/v1/admin/discord-bot/viewer/channels/${parent.id}/archived-threads?${params}`)
        queryClient.setQueryData<ChannelTree>(["discord-viewer-tree"], (current) => current ? { ...current, channels: mergeViewerChannels(current.channels, page.threads) } : current)
        setArchivePages((current) => ({ ...current, [key]: { before: page.before, hasMore: page.has_more, loaded: true, loading: false } }))
      } catch (error) {
        setArchivePages((current) => ({ ...current, [key]: { ...(current[key] ?? { before: null, hasMore: false, loaded: false }), loading: false } }))
        toast.error((error as ApiError).message)
      }
    }))
  }
  const searchMessages = async (offset = 0) => {
    const parsed = parseDiscordSearch(search)
    const params = new URLSearchParams({ query: parsed.query, offset: String(offset), limit: "25" })
    const needsMembers = ["from", "mentions", "replied_to_user_id"].some((key) => parsed.filters[key]?.length)
    const members = needsMembers
      ? await queryClient.fetchQuery({ queryKey: ["discord-viewer-members", ""], queryFn: () => api.get<{ total: number; members: MemberRow[] }>("/api/v1/admin/discord-bot/viewer/members?limit=500") }).then((res) => res.members).catch(() => [] as MemberRow[])
      : []
    const resolveIds = (key: string, target: string, values: string[]) => values.forEach((raw) => {
      const value = raw.replace(/^[@#]/, "")
      const name = value.toLowerCase()
      if (key === "in") {
        const channel = tree.channels.find((item) => item.name.toLowerCase() === name)
        if (channel) params.append(target, channel.id)
        else if (/^\d+$/.test(value)) params.append(target, value)
        return
      }
      if (key === "mentions" && name === "everyone") { params.set("mention_everyone", "true"); return }
      const member = members.find((item) => (item.display_name ?? item.username ?? "").toLowerCase() === name || item.discord_id === value)
      const role = rolesData?.roles.find((item) => item.name.toLowerCase() === name || item.id === value)
      if (key === "mentions" && !member && role) params.append("mentions_role_id", role.id)
      else if (member) params.append(target, member.discord_id)
      else if (/^\d+$/.test(value)) params.append(target, value)
    })
    resolveIds("in", "channel_id", parsed.filters.in ?? [])
    resolveIds("from", "author_id", parsed.filters.from ?? [])
    resolveIds("mentions", "mentions", parsed.filters.mentions ?? [])
    resolveIds("mentions_role_id", "mentions_role_id", parsed.filters.mentions_role_id ?? [])
    resolveIds("replied_to_user_id", "replied_to_user_id", parsed.filters.replied_to_user_id ?? [])
    resolveIds("replied_to_message_id", "replied_to_message_id", parsed.filters.replied_to_message_id ?? [])
    const everyone = parsed.filters.mention_everyone?.[0]
    if (everyone === "true" || everyone === "false") params.set("mention_everyone", everyone)
    for (const value of parsed.filters.has ?? []) params.append("has", value === "file" ? "attachment" : value)
    for (const value of parsed.filters.author_type ?? []) params.append("author_type", value)
    for (const value of parsed.filters.pinned ?? []) params.set("pinned", String(value.toLowerCase() !== "false"))
    for (const key of ["embed_type", "embed_provider", "link_hostname", "attachment_filename", "attachment_extension"])
      for (const value of parsed.filters[key] ?? []) params.append(key, value)
    for (const key of ["sort_by", "sort_order", "slop", "include_nsfw"])
      for (const value of parsed.filters[key] ?? []) params.set(key, value)
    const before = parsed.filters.before?.[0]
    const after = parsed.filters.after?.[0]
    const snowflake = (value: string) => /^\d{17,20}$/.test(value) ? value : discordSearchSnowflake(value)
    if (before && snowflake(before)) params.set("max_id", snowflake(before)!)
    if (after && snowflake(after)) params.set("min_id", snowflake(after)!)
    const during = parsed.filters.during?.[0]
    if (during) {
      const start = new Date(`${during}T00:00:00Z`)
      const end = new Date(start.getTime() + 86400000)
      if (discordSearchSnowflake(start.toISOString()) && discordSearchSnowflake(end.toISOString())) {
        params.set("min_id", discordSearchSnowflake(start.toISOString())!)
        params.set("max_id", discordSearchSnowflake(end.toISOString())!)
      }
    }
    if (parsed.filters.sort?.length) { const sort = parsed.filters.sort[0]; params.set("sort_by", sort === "relevance" ? "relevance" : "timestamp"); params.set("sort_order", sort === "oldest" ? "asc" : "desc") }
    try {
      const result = await api.get<typeof searchResults & { messages: DiscordMessage[] }>(`/api/v1/admin/discord-bot/viewer/search?${params}`)
      setSearchResults(result); setSearchPage(offset); setSearchError("")
      if (result.indexing && result.retry_after) setSearchError(`Discord индексирует сообщения. Повторите поиск через ${Math.ceil(result.retry_after)} с.`)
    } catch (e) { setSearchError((e as ApiError).message); setSearchResults(null) }
  }
  const channelButton = (item: TreeChannel, nested = false) => <div key={item.id} className="group">
    <button type="button" onClick={() => selectChannel(item)} onContextMenu={(event) => { event.preventDefault(); setChannelMenu({ id: item.id, x: event.clientX, y: event.clientY }) }} onDragOver={(event) => item.type === "voice" || item.type === "stage" ? event.preventDefault() : undefined} onDrop={(event) => { event.preventDefault(); const memberId = event.dataTransfer.getData("text/plain") || draggingMember; if (memberId && (item.type === "voice" || item.type === "stage")) void moveMember(memberId, item.id) }} className={`flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-sm ${nested ? "pl-6" : ""} ${item.id === activeChannel ? "bg-[#404249] text-white" : "text-[#949ba4] hover:bg-[#35373c]"}`}>
      {item.type === "thread" ? <GitBranch className="size-3.5 shrink-0" /> : item.type === "voice" || item.type === "stage" ? <Volume2 className="size-3.5 shrink-0" /> : item.type === "forum" || item.type === "media" ? <Users className="size-3.5 shrink-0" /> : <Hash className="size-3.5 shrink-0" />}
      <span className="min-w-0 flex-1 truncate">{item.name}{item.archived && <span className="ml-1 text-[9px]">архив</span>}</span>
      <span className="hidden group-hover:inline" title="Контекстные действия"><MoreHorizontal className="size-3.5" /></span>
    </button>
    <button type="button" aria-label={`Действия канала ${item.name}`} onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); setChannelMenu({ id: item.id, x: rect.right, y: rect.top }) }} className="sr-only focus:not-sr-only focus:rounded focus:bg-[#404249] focus:px-2 focus:text-xs">Меню канала</button>
    {(item.type === "voice" || item.type === "stage") && item.voice_members?.map((member) => <button key={member.id} type="button" draggable onDragStart={(event) => { event.dataTransfer.setData("text/plain", member.id); setDraggingMember(member.id) }} onDragEnd={() => setDraggingMember(null)} onClick={() => setSelectedMember(member.id)} className="ml-7 flex w-[calc(100%-1.75rem)] items-center gap-1.5 rounded px-1 py-1 text-left text-xs text-[#949ba4] hover:bg-[#35373c]">
      <Avatar className="size-5"><AvatarImage src={member.avatar_url ?? undefined} /><AvatarFallback className="text-[8px]">{initials(member.display_name)}</AvatarFallback></Avatar><span className="min-w-0 flex-1 truncate">{member.display_name}</span>{member.streaming && <Radio className="size-3 text-[#23a55a]" />}{member.self_mute || member.mute ? <span title="Микрофон выключен">мьют</span> : null}
    </button>)}
    {threadsByParent.get(item.id)?.map((thread) => channelButton(thread, true))}
    {!nested && (item.type === "text" || item.type === "forum" || item.type === "media") && (() => {
      const sources = item.type === "text" ? ["public", "joined_private"] : ["public"]
      const states = sources.map((source) => archivePages[`${item.id}:${source}`])
      const loading = states.some((state) => state?.loading)
      const canLoad = states.some((state) => !state?.loaded || state.hasMore)
      const loaded = states.some((state) => state?.loaded)
      return <button type="button" disabled={loading || !canLoad} onClick={() => void loadArchivedThreads(item)} className="ml-7 rounded px-1 py-0.5 text-left text-[10px] text-[#949ba4] hover:bg-[#35373c] disabled:opacity-50">{loading ? "Загрузка архивных веток…" : canLoad ? (loaded ? "Загрузить ещё архивные ветки" : "Показать архивные ветки") : "Архивные ветки загружены"}</button>
    })()}
  </div>

  return <div data-discord-viewer className="discord-client flex h-[calc(100dvh-44px)] min-h-[420px] flex-col overflow-hidden">
    <div className="grid min-h-0 flex-1 grid-cols-[232px_minmax(0,1fr)_250px] overflow-hidden bg-[#313338] text-[#dbdee1] max-[900px]:grid-cols-[180px_minmax(0,1fr)] max-[900px]:[&>aside]:hidden">
      <nav aria-label="Discord channels" className="flex min-h-0 flex-col overflow-y-auto border-r border-[#1e1f22] bg-[#2b2d31] px-2 py-0">
        <div className="-mx-2 mb-2 flex h-12 shrink-0 items-center gap-2 truncate border-b border-[#1e1f22] px-3 text-sm font-semibold text-[#f2f3f5]"><Avatar className="size-7"><AvatarImage src={tree.guild.icon_url ?? undefined} /><AvatarFallback>{initials(tree.guild.name)}</AvatarFallback></Avatar>{tree.guild.name}</div>
        {tree.categories.map((cat) => <section key={cat.id} className="mb-3"><div className="mb-1 flex items-center gap-1 px-1 text-[10px] font-bold uppercase tracking-wide text-[#949ba4]"><ChevronDown className="size-3" />{cat.name}</div>{(byCategory.get(cat.id) ?? []).map((item) => channelButton(item))}</section>)}
        {(byCategory.get(null) ?? []).map((item) => channelButton(item))}
        {!tree.channels.length && <p className="px-1 text-xs text-[#949ba4]">Нет видимых каналов.</p>}
      </nav>
      <main className="flex min-h-0 min-w-0 flex-col bg-[#313338]">
        <form onSubmit={(event) => { event.preventDefault(); void searchMessages(0) }} className="flex h-12 shrink-0 items-center gap-2 border-b border-[#1e1f22] px-3"><Search className="size-4 shrink-0 text-[#949ba4]" /><Input aria-label="Поиск сообщений Discord" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Искать в Discord: in:#канал from:участник has:file" className="h-8 border-0 bg-[#2b2d31] text-xs" /><Button size="sm" type="submit" disabled={!search.trim()} className="bg-[#5865f2] text-white hover:bg-[#4752c4]">Поиск</Button></form>
        {searchError && <p role="status" className="shrink-0 border-b border-[#1e1f22] bg-[#2b2d31] px-3 py-2 text-xs text-[#f0b232]">{searchError}</p>}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {searchResults ? <section className="min-h-0 flex-1 overflow-y-auto px-2 py-2"><div className="mb-2 flex items-center justify-between text-xs text-[#949ba4]"><span>Результаты Discord Search</span>{searchResults.has_more && <Button size="sm" variant="ghost" onClick={() => void searchMessages(searchPage + 25)}>Следующие 25</Button>}</div>{searchResults.messages.map((msg) => <button key={`${msg.channel_id}:${msg.id}`} type="button" onClick={() => { setActiveChannel(msg.channel_id ?? null); setJumpTo(msg.id); setSearchResults(null) }} className="block w-full border-b border-[#3b3d43] p-2 text-left hover:bg-[#2e3035]"><div className="text-xs"><strong>{msg.author.display_name}</strong><span className="ml-2 text-[#949ba4]">{tree.channels.find((item) => item.id === msg.channel_id)?.name ?? "канал"} · {formatBrowserDate(msg.created_at)}</span></div><div className="mt-1 line-clamp-3 whitespace-pre-wrap text-sm">{msg.content || "Вложение"}</div></button>)}</section> : channel && (channel.type === "forum" || channel.type === "media") ? <section className="flex min-h-0 flex-1 items-center justify-center p-4 text-sm text-[#949ba4]">Выберите ветку форума в списке слева. Контейнер Discord сам по себе не является историей сообщений.</section> : channel && (channel.type === "voice" || channel.type === "stage") ? <section className="min-h-0 flex-1 overflow-y-auto p-4 text-[#dbdee1]" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const memberId = event.dataTransfer.getData("text/plain") || draggingMember; if (memberId) void moveMember(memberId, channel.id) }}><div className="flex items-center gap-2 border-b border-[#3b3d43] pb-3 font-semibold"><Headphones className="size-4 text-[#949ba4]" />{channel.name}</div><p className="mt-3 text-xs text-[#949ba4]">{channel.voice_members?.length ?? 0} участников · перетащите участника сюда, чтобы переместить</p><div className="mt-3 grid grid-cols-2 gap-2">{channel.voice_members?.map((member) => <div key={member.id} className="flex items-center gap-2 rounded bg-[#2b2d31] p-2"><Avatar className="size-9"><AvatarImage src={member.avatar_url ?? undefined} /><AvatarFallback>{initials(member.display_name)}</AvatarFallback></Avatar><div className="min-w-0"><div className="truncate text-sm">{member.display_name}</div><div className="text-[10px] text-[#949ba4]">{member.streaming ? "Транслирует" : member.video ? "Видео" : "В голосе"}{member.deaf || member.self_deaf ? " · звук выключен" : ""}</div></div></div>)}</div></section> : channel ? <ChatPanel channel={channel} guildId={tree.guild.id} canSend={tree.can_send} canManage={!!tree.can_manage_config || tree.can_manage_roles} jumpTo={jumpTo} /> : <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-[#949ba4]">Выберите канал слева</div>}
        </div>
      </main>
      <MembersPanel onSelect={setSelectedMember} draggingMember={draggingMember} setDraggingMember={setDraggingMember} voiceMemberIds={new Set(tree.channels.flatMap((item) => item.voice_members?.map((member) => member.id) ?? []))} />
    </div>
    <AuditLogPanel enabled={!!tree.can_manage_config || !!tree.can_manage_roles} />
    {channel && (channel.type === "voice" || channel.type === "stage") && <VoiceEventsBridge channelId={channel.id} />}
    {selectedMember && <MemberDetailDialog discordId={selectedMember} roles={rolesData?.roles ?? []} voiceTargets={tree.channels.filter((item) => item.type === "voice" || item.type === "stage")} onVoiceMove={moveMember} onClose={() => setSelectedMember(null)} />}
    {memberAccess && <ChannelAccessDialog channelId={memberAccess} onClose={() => setMemberAccess(null)} />}
    {channelMenu && <div ref={channelMenuRef} role="menu" tabIndex={-1} className="fixed z-50 min-w-48 rounded-md border border-[#3b3d43] bg-[#111214] p-1 shadow-xl" style={{ left: channelMenu.x, top: channelMenu.y }} onMouseLeave={() => setChannelMenu(null)} onKeyDown={(event) => { if (event.key === "Escape") setChannelMenu(null) }}>
      <button role="menuitem" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#dbdee1] hover:bg-[#35373c]" onClick={() => { void navigator.clipboard.writeText(channelMenu.id); setChannelMenu(null) }}><Copy className="size-3.5" />Копировать ID канала</button>
      <button role="menuitem" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#dbdee1] hover:bg-[#35373c]" onClick={() => { void navigator.clipboard.writeText(`https://discord.com/channels/${tree.guild.id}/${channelMenu.id}`); setChannelMenu(null) }}><Link2 className="size-3.5" />Копировать ссылку Discord</button>
      <a role="menuitem" className="flex items-center gap-2 rounded px-2 py-1.5 text-xs text-[#dbdee1] hover:bg-[#35373c]" href={`/api/v1/admin/discord-bot/viewer/channels/${channelMenu.id}/export`} download><Download className="size-3.5" />Экспорт истории</a>
      {tree.can_manage_roles && <button role="menuitem" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#dbdee1] hover:bg-[#35373c]" onClick={async () => { try { const invite = await api.post<{ url: string }>(`/api/v1/admin/discord-bot/viewer/channels/${channelMenu.id}/invite`); await navigator.clipboard.writeText(invite.url); toast.success("Приглашение скопировано") } catch (e) { toast.error((e as ApiError).message) } setChannelMenu(null) }}><Link2 className="size-3.5" />Создать приглашение</button>}
      {tree.can_manage_roles && <button role="menuitem" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#dbdee1] hover:bg-[#35373c]" onClick={() => { setMemberAccess(channelMenu.id); setChannelMenu(null) }}><Users className="size-3.5" />Доступ участников</button>}
      {tree.can_manage_roles && <button role="menuitem" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-[#ed4245] hover:bg-[#35373c]" onClick={async () => {
        const item = tree.channels.find((c) => c.id === channelMenu.id)
        if (item && window.confirm(`Удалить канал #${item.name}? Это действие нельзя отменить.`)) {
          if (window.prompt(`Для подтверждения введите имя канала: ${item.name}`) !== item.name) {
            toast.error("Имя канала не совпало; удаление отменено")
          } else {
            try { await api.delete(`/api/v1/admin/discord-bot/viewer/channels/${item.id}`, { reason: `Удаление ${item.name} из Discord-вьюера` }); toast.success("Канал удалён"); setActiveChannel(null); await queryClient.invalidateQueries({ queryKey: ["discord-viewer-tree"] }) }
            catch (e) { toast.error((e as ApiError).message) }
          }
        }
        setChannelMenu(null)
      }}><Trash2 className="size-3.5" />Удалить канал…</button>}
    </div>}
  </div>
}

function ChannelAccessDialog({ channelId, onClose }: { channelId: string; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [memberId, setMemberId] = useState("")
  const [access, setAccess] = useState<"allow" | "deny" | "inherit">("allow")
  const [status, setStatus] = useState("")
  const change = useMutation({ mutationFn: () => api.put<{ effective_view_channel: boolean }>(`/api/v1/admin/discord-bot/viewer/channels/${channelId}/members/${memberId}/access`, { access }), onSuccess: (result) => { setStatus(result.effective_view_channel ? "Участник видит канал." : "Участник не видит канал."); void queryClient.invalidateQueries({ queryKey: ["discord-viewer-tree"] }) }, onError: (e: ApiError) => toast.error(e.message) })
  return <Dialog open onOpenChange={(open) => !open && onClose()}><DialogContent className="max-w-sm"><DialogHeader><DialogTitle>Доступ к каналу</DialogTitle></DialogHeader><p className="text-xs text-muted-foreground">Выдаётся только VIEW_CHANNEL. Ролевые разрешения могут сохранить доступ и после очистки индивидуального правила.</p><Input aria-label="Discord ID участника" value={memberId} onChange={(event) => setMemberId(event.target.value)} placeholder="Discord ID участника" /><select aria-label="Правило доступа" value={access} onChange={(event) => setAccess(event.target.value as typeof access)} className="h-9 rounded-md border bg-background px-2 text-sm"><option value="allow">Разрешить просмотр</option><option value="deny">Запретить просмотр</option><option value="inherit">Унаследовать от роли</option></select>{status && <p role="status" className="text-xs text-muted-foreground">{status}</p>}<Button disabled={!/^\d+$/.test(memberId) || change.isPending} onClick={() => change.mutate()}>Применить</Button></DialogContent></Dialog>
}
