import type { PermissionRegistry } from "@/lib/auth"

export type PermissionState = boolean | undefined
export type PermissionValue = Record<string, Record<string, PermissionState>>

export function cyclePermissionState(value: PermissionState): PermissionState {
  if (value === undefined) return true
  if (value === true) return false
  return undefined
}

export function TriStatePermissionButton({ value, onChange, label }: {
  value: PermissionState
  onChange: () => void
  label: string
}) {
  const text = value === true ? "✓" : value === false ? "×" : "—"
  const tone = value === true
    ? "border-emerald-500/60 bg-emerald-500/15 text-emerald-300"
    : value === false
      ? "border-red-500/60 bg-red-500/15 text-red-300"
      : "border-muted-foreground/30 bg-muted/30 text-muted-foreground"
  return (
    <button
      type="button"
      className={`inline-flex size-6 items-center justify-center rounded border text-xs font-semibold transition-colors hover:brightness-125 ${tone}`}
      onClick={onChange}
      aria-label={`${label}: ${value === true ? "разрешено" : value === false ? "запрещено" : "не задано"}`}
      title={`${label}: ${value === true ? "разрешено" : value === false ? "запрещено" : "не задано"}`}
    >
      {text}
    </button>
  )
}

/** Registry-driven permission grid: one block per page, one checkbox per
 * that page's own registered actions — replaces the old fixed
 * view/edit/delete-for-every-page table. */
export function PermissionMatrixEditor({ registry, value, onChange }: {
  registry: PermissionRegistry
  value: PermissionValue
  onChange: (next: PermissionValue) => void
}) {
  const toggle = (page: string, action: string) => {
    const nextState = cyclePermissionState(value[page]?.[action])
    const nextPage = { ...value[page] }
    if (nextState === undefined) delete nextPage[action]
    else nextPage[action] = nextState
    const next = { ...value }
    if (Object.keys(nextPage).length === 0) delete next[page]
    else next[page] = nextPage
    onChange(next)
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
                <TriStatePermissionButton value={value[page]?.[a.key]} onChange={() => toggle(page, a.key)} label={`${def.label}: ${a.label}`} />
                {a.label}
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
