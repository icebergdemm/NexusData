import type { ReactNode } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { MessageSquare, PanelLeftClose, PanelLeftOpen, ScrollText } from "lucide-react"
import { Button } from "@/components/ui/button"

export function LogsWorkspace({ children }: { children: ReactNode }) {
  return (
    <div data-logs-workspace className="-m-4 flex min-w-0 flex-col bg-background sm:-m-5 h-[calc(100dvh-3.5rem)] overflow-hidden">
      {children}
    </div>
  )
}

export function LogsWorkspaceHeader({ communications, filtersOpen, onToggleFilters, filterCount, actions }: {
  communications: boolean
  filtersOpen: boolean
  onToggleFilters: () => void
  filterCount: number
  actions?: ReactNode
}) {
  const [searchParams] = useSearchParams()
  const eventsQuery = new URLSearchParams(searchParams)
  eventsQuery.delete("view")
  const communicationsQuery = new URLSearchParams(eventsQuery)
  communicationsQuery.set("view", "communications")

  return (
    <header className="flex shrink-0 flex-col gap-3 border-b px-4 py-3 sm:px-5">
      <div className="flex items-center gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center border border-border">
          <ScrollText className="size-4" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <h1 className="text-sm font-semibold uppercase tracking-[0.08em]">Логи сервера</h1>
          <p className="text-xs text-muted-foreground">Действия, события и общение игроков</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <nav aria-label="Разделы логов" className="grid w-full grid-cols-2 gap-1 sm:w-auto">
          <Button asChild variant={communications ? "outline" : "default"}>
            <Link to={{ pathname: "/logs", search: eventsQuery.toString() }} aria-current={!communications ? "page" : undefined}>
              <ScrollText data-icon="inline-start" aria-hidden="true" />
              <span className={!communications ? "underline underline-offset-4" : undefined}>Журнал событий</span>
            </Link>
          </Button>
          <Button asChild variant={communications ? "default" : "outline"}>
            <Link to={{ pathname: "/logs", search: communicationsQuery.toString() }} aria-current={communications ? "page" : undefined}>
              <MessageSquare data-icon="inline-start" aria-hidden="true" />
              <span className={communications ? "underline underline-offset-4" : undefined}>Общение игроков</span>
            </Link>
          </Button>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <Button type="button" variant="outline" size="sm" className="lg:hidden" onClick={onToggleFilters}
            aria-expanded={filtersOpen} aria-controls="logs-filters-v2">
            {filtersOpen ? <PanelLeftClose data-icon="inline-start" /> : <PanelLeftOpen data-icon="inline-start" />}
            Фильтры{filterCount > 0 ? ` (${filterCount})` : ""}
          </Button>
          {actions}
        </div>
      </div>
    </header>
  )
}
