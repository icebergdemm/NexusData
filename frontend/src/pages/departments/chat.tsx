import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { MessagesSquare, Plus, Send, Trash2, Hash } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { departmentsApi, type ChatChannel, type DepartmentDetail } from "./api"

function AddChannelDialog({ open, onOpenChange, department }: {
  open: boolean; onOpenChange: (open: boolean) => void; department: DepartmentDetail
}) {
  const queryClient = useQueryClient()
  const [channelId, setChannelId] = useState("")
  const [label, setLabel] = useState("")

  const addMutation = useMutation({
    mutationFn: () => departmentsApi.chat.addChannel(department.id, { discord_channel_id: channelId.trim(), label: label.trim() || undefined }),
    onSuccess: () => {
      toast.success("Канал добавлен")
      queryClient.invalidateQueries({ queryKey: ["department-chat-channels", department.id] })
      onOpenChange(false)
      setChannelId(""); setLabel("")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader><DialogTitle>Привязать канал Discord</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">ID канала</Label>
            <Input value={channelId} onChange={(e) => setChannelId(e.target.value)} placeholder="123456789012345678" />
          </div>
          <div>
            <Label className="mb-1.5 block">Название (необязательно)</Label>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Общий" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!channelId.trim() || addMutation.isPending} onClick={() => addMutation.mutate()}>Добавить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ChatTab({ department, canEdit, isSuperuser }: { department: DepartmentDetail; canEdit: boolean; isSuperuser: boolean }) {
  const queryClient = useQueryClient()
  const [activeChannel, setActiveChannel] = useState<ChatChannel | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [removeTarget, setRemoveTarget] = useState<ChatChannel | null>(null)
  const [text, setText] = useState("")
  const scrollRef = useRef<HTMLDivElement>(null)
  const canView = department.my_access.chat?.view
  const canManageChannels = isSuperuser || department.is_head

  const { data: channelsData } = useQuery({
    queryKey: ["department-chat-channels", department.id],
    queryFn: () => departmentsApi.chat.channels(department.id),
    enabled: canView,
  })
  const channels = channelsData?.channels ?? []

  useEffect(() => {
    if (!activeChannel && channels.length) setActiveChannel(channels[0])
  }, [channels, activeChannel])

  const { data: messagesData } = useQuery({
    queryKey: ["department-chat-messages", department.id, activeChannel?.id],
    queryFn: () => departmentsApi.chat.messages(department.id, activeChannel!.id),
    enabled: canView && !!activeChannel,
    refetchInterval: 5000,
  })

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messagesData])

  const removeMutation = useMutation({
    mutationFn: (channelId: number) => departmentsApi.chat.removeChannel(department.id, channelId),
    onSuccess: () => {
      toast.success("Канал отвязан")
      queryClient.invalidateQueries({ queryKey: ["department-chat-channels", department.id] })
      setActiveChannel(null)
      setRemoveTarget(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const sendMutation = useMutation({
    mutationFn: () => departmentsApi.chat.send(department.id, activeChannel!.id, text.trim()),
    onSuccess: () => {
      setText("")
      queryClient.invalidateQueries({ queryKey: ["department-chat-messages", department.id, activeChannel?.id] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!canView) {
    return <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">Недостаточно прав для просмотра чата отдела.</CardContent></Card>
  }

  if (!channels.length) {
    return (
      <div className="space-y-3">
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-12 text-center text-sm text-muted-foreground">
            <MessagesSquare className="size-6 opacity-40" />
            К отделу не привязано ни одного канала Discord.
          </CardContent>
        </Card>
        {canManageChannels && (
          <div className="flex justify-end">
            <Button size="sm" onClick={() => setAddOpen(true)}><Plus className="size-4" /> Привязать канал</Button>
          </div>
        )}
        <AddChannelDialog open={addOpen} onOpenChange={setAddOpen} department={department} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {channels.map((c) => (
          <button
            key={c.id}
            onClick={() => setActiveChannel(c)}
            className={cn(
              "flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-sm",
              activeChannel?.id === c.id ? "border-primary bg-primary/10 text-primary" : "hover:bg-accent"
            )}
          >
            <Hash className="size-3.5" /> {c.label || c.discord_channel_id}
          </button>
        ))}
        {canManageChannels && (
          <Button size="icon-xs" variant="outline" onClick={() => setAddOpen(true)} title="Привязать канал">
            <Plus className="size-3.5" />
          </Button>
        )}
        {canManageChannels && activeChannel && (
          <Button size="icon-xs" variant="outline" className="text-destructive" onClick={() => setRemoveTarget(activeChannel)} title="Отвязать канал">
            <Trash2 className="size-3.5" />
          </Button>
        )}
      </div>

      <Card className="flex h-[60vh] flex-col overflow-hidden py-0">
        <ScrollArea className="min-h-0 flex-1">
          <div ref={scrollRef} className="flex flex-col gap-2 p-4">
            {!messagesData?.messages.length && (
              <div className="py-10 text-center text-sm text-muted-foreground">Сообщений пока нет</div>
            )}
            {messagesData?.messages.map((m) => (
              <div key={m.id} className="flex flex-col items-start">
                <div className="rounded-2xl rounded-bl-sm bg-muted px-3.5 py-2 text-sm">
                  <div className="mb-0.5 text-xs font-semibold text-info">{m.author_name ?? m.sent_by_admin ?? "?"}</div>
                  <div className="break-words">{m.content}</div>
                </div>
                <span className="mt-0.5 px-1 font-mono text-[10px] text-muted-foreground">
                  {m.created_at ? new Date(m.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : ""}
                </span>
              </div>
            ))}
          </div>
        </ScrollArea>
        {canEdit && (
          <div className="flex items-center gap-2 border-t p-3">
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && text.trim() && sendMutation.mutate()}
              placeholder="Написать сообщение..."
              maxLength={2000}
            />
            <Button size="icon" disabled={!text.trim() || sendMutation.isPending} onClick={() => sendMutation.mutate()} className="shrink-0 rounded-full">
              <Send className="size-4" />
            </Button>
          </div>
        )}
      </Card>

      <AddChannelDialog open={addOpen} onOpenChange={setAddOpen} department={department} />
      <ConfirmDialog
        open={!!removeTarget}
        onOpenChange={(o) => !o && setRemoveTarget(null)}
        title="Отвязать канал?"
        description={removeTarget?.label ?? removeTarget?.discord_channel_id}
        onConfirm={() => removeTarget && removeMutation.mutate(removeTarget.id)}
      />
    </div>
  )
}
