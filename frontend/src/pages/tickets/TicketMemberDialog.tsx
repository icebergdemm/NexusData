import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check, Search, UserMinus, UserPlus } from "lucide-react"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import type { TicketMembersResponse, TicketMember } from "./types"

export function TicketMemberDialog({
  open,
  ticketId,
  onOpenChange,
}: {
  open: boolean
  ticketId: number | null
  onOpenChange: (open: boolean) => void
}) {
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<TicketMember | null>(null)
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false)
  const queryClient = useQueryClient()

  const membersQuery = useQuery({
    queryKey: ["ticket-members", search],
    queryFn: () => api.get<TicketMembersResponse>(`/api/v1/admin/tickets/members?search=${encodeURIComponent(search)}`),
    enabled: open,
  })

  const addMutation = useMutation({
    mutationFn: () => api.post<{ status: "ok" | "already_present" }>(`/api/v1/admin/tickets/${ticketId}/members`, { discord_id: selected?.discord_id }),
    onSuccess: (result) => {
      toast.success(result.status === "already_present" ? "Участник уже добавлен" : "Участник добавлен в тикет")
      if (ticketId !== null) queryClient.invalidateQueries({ queryKey: ["ticket-detail", String(ticketId)] })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
      onOpenChange(false)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const removeMutation = useMutation({
    mutationFn: () => api.post<{ status: "ok" | "already_absent" }>(`/api/v1/admin/tickets/${ticketId}/members/${selected?.discord_id}/remove`),
    onSuccess: (result) => {
      toast.success(result.status === "already_absent" ? "Участник уже не имеет доступа" : "Участник убран из тикета")
      if (ticketId !== null) queryClient.invalidateQueries({ queryKey: ["ticket-detail", String(ticketId)] })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
      onOpenChange(false)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const isPending = addMutation.isPending || removeMutation.isPending

  const close = (nextOpen: boolean) => {
    if (isPending) return
    if (!nextOpen) {
      setSelected(null)
      setSearch("")
      setRemoveConfirmOpen(false)
    }
    onOpenChange(nextOpen)
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><UserPlus className="size-4" /> Добавить участника</DialogTitle>
          <DialogDescription>Выберите участника Discord, затем добавьте или уберите его из тикета. Операция повторно проверяется на сервере.</DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" autoFocus placeholder="Имя или никнейм Discord" value={search} onChange={(event) => { setSearch(event.target.value); setSelected(null) }} />
        </div>

        <div className="max-h-64 space-y-1 overflow-y-auto rounded-md border p-1">
          {membersQuery.isLoading && <div className="space-y-2 p-2"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>}
          {membersQuery.isError && <div className="p-4 text-center text-sm text-destructive">{(membersQuery.error as Error).message}</div>}
          {membersQuery.data?.members.map((member) => (
            <button
              key={member.discord_id}
              type="button"
              className={`flex w-full items-center gap-2 rounded-md p-2 text-left hover:bg-muted ${selected?.discord_id === member.discord_id ? "bg-muted" : ""}`}
              onClick={() => setSelected(member)}
            >
              {member.avatar_url ? <img src={member.avatar_url} alt="" className="size-8 rounded-full" /> : <div className="size-8 rounded-full bg-muted" />}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{member.display_name || member.username || member.discord_id}</span>
                <span className="block truncate text-xs text-muted-foreground">{member.username ? `@${member.username}` : "Discord участник"}</span>
              </span>
              {selected?.discord_id === member.discord_id && <Check className="size-4 text-primary" />}
            </button>
          ))}
          {!membersQuery.isLoading && !membersQuery.isError && !membersQuery.data?.members.length && <div className="p-4 text-center text-sm text-muted-foreground">Участники не найдены</div>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)} disabled={isPending}>Отмена</Button>
          <Button variant="destructive" onClick={() => setRemoveConfirmOpen(true)} disabled={!selected || isPending}>
            {removeMutation.isPending ? "Удаление..." : <><UserMinus className="size-4" /> Убрать</>}
          </Button>
          <Button onClick={() => addMutation.mutate()} disabled={!selected || isPending}>
            {addMutation.isPending ? "Добавление..." : <><UserPlus className="size-4" /> Добавить</>}
          </Button>
        </DialogFooter>
      </DialogContent>

      <ConfirmDialog
        open={removeConfirmOpen}
        onOpenChange={(open) => !removeMutation.isPending && setRemoveConfirmOpen(open)}
        title="Убрать участника из тикета?"
        description={selected ? `${selected.display_name || selected.username || selected.discord_id} потеряет персональный доступ к Discord-каналу этого тикета.` : undefined}
        confirmLabel={removeMutation.isPending ? "Удаление..." : "Убрать участника"}
        confirmDisabled={removeMutation.isPending}
        onConfirm={() => removeMutation.mutate()}
      />
    </Dialog>
  )
}
