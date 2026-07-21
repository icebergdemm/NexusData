import type { ReactNode } from "react"
import {
  ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem, ContextMenuSeparator,
} from "@/components/ui/context-menu"

export interface ContextMenuAction {
  label: string
  icon?: React.ComponentType<{ className?: string }>
  onSelect: () => void
  destructive?: boolean
  disabled?: boolean
  /** Renders a separator above this item — group related actions. */
  separatorBefore?: boolean
}

/** Wraps `children` with a right-click menu of `actions` — used on the main
 * list/card surfaces across the app (players, moderation tables, documents,
 * sub-projects, tasks, department rosters) so a right-click always offers
 * the same handful of useful shortcuts instead of requiring a trip into the
 * row/card itself. Renders children unwrapped if there's nothing to show. */
export function AppContextMenu({ actions, children }: { actions: ContextMenuAction[]; children: ReactNode }) {
  if (!actions.length) return <>{children}</>
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className="w-56">
        {actions.map((a, i) => (
          <div key={i}>
            {a.separatorBefore && <ContextMenuSeparator />}
            <ContextMenuItem variant={a.destructive ? "destructive" : "default"} disabled={a.disabled} onSelect={a.onSelect}>
              {a.icon && <a.icon className="size-4" />}
              {a.label}
            </ContextMenuItem>
          </div>
        ))}
      </ContextMenuContent>
    </ContextMenu>
  )
}
