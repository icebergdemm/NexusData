import { Checkbox } from "@/components/ui/checkbox"
import type { PermissionRegistry } from "@/lib/auth"

export type PermissionValue = Record<string, Record<string, boolean>>

/** Registry-driven permission grid: one block per page, one checkbox per
 * that page's own registered actions — replaces the old fixed
 * view/edit/delete-for-every-page table. */
export function PermissionMatrixEditor({ registry, value, onChange }: {
  registry: PermissionRegistry
  value: PermissionValue
  onChange: (next: PermissionValue) => void
}) {
  const toggle = (page: string, action: string) => {
    onChange({
      ...value,
      [page]: { ...value[page], [action]: !value[page]?.[action] },
    })
  }

  return (
    <div className="space-y-2">
      {Object.entries(registry).map(([page, def]) => (
        <div key={page} className="rounded-md border p-2.5">
          <div className="mb-1.5 text-sm font-medium">
            {def.label} <span className="font-mono text-xs text-muted-foreground">({page})</span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {def.actions.map((a) => (
              <label key={a.key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Checkbox checked={!!value[page]?.[a.key]} onCheckedChange={() => toggle(page, a.key)} />
                {a.label}
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
