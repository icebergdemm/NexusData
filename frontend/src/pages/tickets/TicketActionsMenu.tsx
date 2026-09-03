import { MoreHorizontal, RotateCcw, Trash2, UserMinus, UserPlus, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type { TicketRow } from "./types"

interface TicketActionsMenuProps {
  ticket: TicketRow
  canAssign: boolean
  canEdit: boolean
  canDelete: boolean
  disabled?: boolean
  onAddMember?: () => void
  onRemoveMember?: () => void
  onClose?: () => void
  onDelete?: () => void
  onRestore?: () => void
}

export function TicketActionsMenu({
  ticket,
  canAssign,
  canEdit,
  canDelete,
  disabled = false,
  onAddMember,
  onRemoveMember,
  onClose,
  onDelete,
  onRestore,
}: TicketActionsMenuProps) {
  const isDeleted = ticket.deleted_at !== null
  const isClosed = ticket.status === "closed"
  const isOpen = !isDeleted && !isClosed
  const hasMemberActions = isOpen && canAssign && (onAddMember || onRemoveMember)
  const hasCloseAction = isOpen && canEdit && onClose
  const hasDeleteAction = isClosed && !isDeleted && canDelete && onDelete
  const hasRestoreAction = isDeleted && canDelete && onRestore

  if (!hasMemberActions && !hasCloseAction && !hasDeleteAction && !hasRestoreAction) return null

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Действия тикета #${ticket.id}`}
          title="Действия тикета"
          disabled={disabled}
          onClick={(event) => event.stopPropagation()}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <MoreHorizontal className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={(event) => event.stopPropagation()}>
        {hasMemberActions && (
          <>
            {onAddMember && (
              <DropdownMenuItem onClick={onAddMember}>
                <UserPlus /> Добавить участника
              </DropdownMenuItem>
            )}
            {onRemoveMember && (
              <DropdownMenuItem variant="destructive" onClick={onRemoveMember}>
                <UserMinus /> Удалить участника
              </DropdownMenuItem>
            )}
          </>
        )}
        {hasCloseAction && (
          <>
            {hasMemberActions && <DropdownMenuSeparator />}
            <DropdownMenuItem variant="destructive" onClick={onClose}>
              <XCircle /> Закрыть тикет
            </DropdownMenuItem>
          </>
        )}
        {hasDeleteAction && (
          <DropdownMenuItem variant="destructive" onClick={onDelete}>
            <Trash2 /> Удалить тикет
          </DropdownMenuItem>
        )}
        {hasRestoreAction && (
          <DropdownMenuItem onClick={onRestore}>
            <RotateCcw /> Восстановить тикет
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
