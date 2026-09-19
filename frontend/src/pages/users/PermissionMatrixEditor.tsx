import type { PermissionRegistry } from "@/lib/auth"

export type PermissionState = boolean | undefined
export type PermissionValue = Record<string, Record<string, PermissionState>>

export function cyclePermissionState(value: PermissionState): PermissionState {
  if (value === undefined) return true
  if (value === true) return false
  return undefined
}

export function permissionStatusLabel(value: boolean): string {
  return value ? "Разрешено" : "Запрещено"
}

export function hasPermissionConflict(sources: Array<{ value: boolean; applies?: boolean }>): boolean {
  const values = new Set(sources.filter((source) => source.applies !== false).map((source) => source.value))
  return values.has(true) && values.has(false)
}

export function resolvePermissionValue(sources: Array<{ value: boolean; applies?: boolean }>, policyMode: string): boolean {
  const active = sources.filter((source) => source.applies !== false)
  if (policyMode === "deny_v2") return active.some((source) => source.value) && !active.some((source) => !source.value)
  return active.some((source) => source.value)
}

export function formatPermissionSource(source: { kind: string; [key: string]: unknown }): string {
  if (source.kind === "individual") return "Индивидуальное правило"
  if (source.kind === "group") return `Группа: ${String(source.group_name ?? "без названия")}`
  if (source.kind === "department_access_level") return `${String(source.department_name ?? "Департамент")} → ${String(source.level_name ?? source.level_key)}`
  if (source.kind === "department_site") return `Департамент: ${String(source.department_name ?? "общее правило")}`
  if (source.kind === "superuser") return "Superuser: полный обход"
  return "Базовое правило"
}

export function permissionSourceHref(source: { kind: string; [key: string]: unknown }): string | undefined {
  const groupId = sourceId(source.group_id)
  if (source.kind === "group" && Number.isInteger(groupId)) {
    return `/security/admins?tab=groups&group_id=${groupId}#group-${groupId}`
  }
  const departmentId = sourceId(source.department_id)
  const levelId = sourceId(source.level_id)
  if (source.kind === "department_access_level" && Number.isInteger(departmentId) && Number.isInteger(levelId)) {
    return `/departments/${departmentId}#level-${levelId}`
  }
  if (source.kind === "department_site" && Number.isInteger(departmentId)) {
    return `/departments/${departmentId}`
  }
  return undefined
}

function sourceId(value: unknown): number {
  if (typeof value === "number") return value
  if (typeof value === "string" && value.trim() !== "") return Number(value)
  return Number.NaN
}

export function setPermissionOverride(value: PermissionValue, page: string, action: string, next: PermissionState): PermissionValue {
  const nextPage = { ...value[page] }
  if (next === undefined) delete nextPage[action]
  else nextPage[action] = next
  const nextValue = { ...value }
  if (Object.keys(nextPage).length === 0) delete nextValue[page]
  else nextValue[page] = nextPage
  return nextValue
}

export function TriStatePermissionButton({ value, onChange, label }: {
  value: PermissionState
  onChange: () => void
  label: string
}) {
  const text = value === true ? "Разрешено" : value === false ? "Запрещено" : "Наследовать"
  const tone = value === true
    ? "border-emerald-500/60 bg-emerald-500/15 text-emerald-300"
    : value === false
      ? "border-red-500/60 bg-red-500/15 text-red-300"
      : "border-muted-foreground/30 bg-muted/30 text-muted-foreground"
  return (
    <button
      type="button"
      className={`inline-flex min-h-6 items-center justify-center rounded border px-1.5 text-[10px] font-semibold transition-colors hover:brightness-125 ${tone}`}
      onClick={onChange}
      aria-label={`${label}: ${value === true ? "разрешено" : value === false ? "запрещено" : "наследовать"}`}
      title={`${label}: ${value === true ? "разрешено" : value === false ? "запрещено" : "наследовать"}`}
    >
      {text}
    </button>
  )
}

/** Registry-driven permission grid: one block per page, one checkbox per
 * that page's own registered actions — replaces the old fixed
 * view/edit/delete-for-every-page table. */
export function PermissionMatrixEditor({ registry, value, onChange, grantOnly = false }: {
  registry: PermissionRegistry
  value: PermissionValue
  onChange: (next: PermissionValue) => void
  grantOnly?: boolean
}) {
  const toggle = (page: string, action: string) => {
    const nextState = grantOnly
      ? value[page]?.[action] === true ? undefined : true
      : cyclePermissionState(value[page]?.[action])
    onChange(setPermissionOverride(value, page, action, nextState))
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
                <TriStatePermissionButton value={grantOnly && value[page]?.[a.key] === false ? undefined : value[page]?.[a.key]} onChange={() => toggle(page, a.key)} label={`${def.label}: ${a.label}`} />
                {a.label}
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
