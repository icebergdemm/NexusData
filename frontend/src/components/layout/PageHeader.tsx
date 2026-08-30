import type { ReactNode } from "react"

export function PageHeader({
  title, description, icon: Icon, actions,
}: {
  title: string
  description?: string
  icon?: React.ComponentType<{ className?: string }>
  actions?: ReactNode
}) {
  return (
    <div data-slot="page-header" className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-center gap-3">
        {Icon && (
          <div data-slot="page-header-icon" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary">
            <Icon className="h-5 w-5" />
          </div>
        )}
        <div>
          <h1 data-slot="page-header-title" className="text-xl font-bold tracking-tight">{title}</h1>
          {description && <p data-slot="page-header-description" className="text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {actions && <div data-slot="page-header-actions" className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
