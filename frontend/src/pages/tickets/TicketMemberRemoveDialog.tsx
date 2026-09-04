import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check, UserMinus } from "lucide-react"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import type { TicketMember, TicketMembersResponse } from "./types"

export function TicketMemberRemoveDialog({
  open,
  ticketId,
  onOpenChange,
}: {
  open: boolean
  ticketId: number | null
  onOpenChange: (open: boolean) => void
}) {
  const [selected, setSelected] = useState<TicketMember | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const queryClient = useQueryClient()

  const membersQuery = useQuery({
    queryKey: ["ticket-assigned-members", ticketId],
    queryFn: () => api.get<TicketMembersResponse>(`/api/v1/admin/tickets/${ticketId}/members`),
    enabled: open && ticketId !== null,
  })

  const removeMutation = useMutation({
    mutationFn: () => api.post<{ status: "ok" | "already_absent" }>(`/api/v1/admin/tickets/${ticketId}/members/${selected?.discord_id}/remove`),
    onSuccess: (result) => {
      toast.success(result.status === "already_absent" ? "Участник уже не имеет доступа" : "Участник убран из тикета")
      if (ticketId !== null) queryClient.invalidateQueries({ queryKey: ["ticket-detail", String(ticketId)] })
      queryClient.invalidateQueries({ queryKey: ["tickets"] })
      queryClient.invalidateQueries({ queryKey: ["ticket-assigned-members", ticketId] })
      setConfirmOpen(false)
      onOpenChange(false)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  const close = (nextOpen: boolean) => {
    if (removeMutation.isPending) return
    if (!nextOpen) {
      setSelected(null)
      setConfirmOpen(false)
    }
    onOpenChange(nextOpen)
  }

  return (
    <>
      <Dialog open={open} onOpenChange={close}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><UserMinus className="size-4" /> Удалить участника</DialogTitle>
            <DialogDescription>Выберите любого участника, который сейчас имеет доступ к Discord-каналу этого тикета.</DialogDescription>
          </DialogHeader>

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
                  <span className="block truncate text-xs text-muted-foreground">{member.username ? `@${member.username}` : member.discord_id}</span>
                </span>
                {selected?.discord_id === member.discord_id && <Check className="size-4 text-primary" />}
              </button>
            ))}
            {!membersQuery.isLoading && !membersQuery.isError && !membersQuery.data?.members.length && <div className="p-4 text-center text-sm text-muted-foreground">В тикете нет участников с персональным доступом</div>}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => close(false)} disabled={removeMutation.isPending}>Отмена</Button>
            <Button variant="destructive" onClick={() => setConfirmOpen(true)} disabled={!selected || removeMutation.isPending}>
              <UserMinus className="size-4" /> Убрать из тикета
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={(nextOpen) => !removeMutation.isPending && setConfirmOpen(nextOpen)}
        title="Убрать участника из тикета?"
        description={selected ? `${selected.display_name || selected.username || selected.discord_id} потеряет персональный доступ к Discord-каналу этого тикета.` : undefined}
        confirmLabel={removeMutation.isPending ? "Удаление..." : "Убрать участника"}
        confirmDisabled={removeMutation.isPending}
        onConfirm={() => removeMutation.mutate()}
      />
    </>
  )
}
