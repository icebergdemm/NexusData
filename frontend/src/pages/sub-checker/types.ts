/** Типы отчёта и конфигурации проверщика кораблей (см. services_sub_checker.py). */

export type Severity = "error" | "warning" | "info"

export interface Finding {
  rule_id: string
  severity: Severity
  title: string
  detail: string
  category: string
  subject: string
}

export interface ClassItemRow {
  identifier: string
  label: string
  required: number | null
  max: number | null
  actual: number
  ok: boolean
}

export interface ClassGroupReport {
  key: string
  label: string
  ok: boolean
  items: ClassItemRow[]
}

export interface ClassReport {
  key: string
  name: string
  min_tonnage: number | null
  max_tonnage: number | null
  tonnage: number | null
  requirements: Record<string, string>
  groups: ClassGroupReport[]
}

export interface DeviceRow {
  type: string | null
  scale?: number
  [key: string]: unknown
}

export interface SubReport {
  name: string
  filename?: string
  is_drone: boolean
  passed: boolean
  errors: number
  warnings: number
  infos: number
  total_errors?: number
  total_warnings?: number
  summary?: string
  metrics: Record<string, number | null>
  raw: {
    length_cm: number; height_cm: number; hulls_cm2: number; ballast_cm2: number
    total_engine_force: number; total_power_consumption: number; total_power_output: number
    game_version: string | null; sub_class_attr: string | null
    manually_outfitted: boolean; item_count: number
  }
  equipment: {
    reactors: DeviceRow[]; engines: DeviceRow[]; pumps: DeviceRow[]; steering: DeviceRow[]
    batteries: DeviceRow[]; oxygen_generators: DeviceRow[]; power_distributors: number
  }
  class: ClassReport | null
  findings: Finding[]
  drones?: SubReport[]
  manual_checks?: { id: string; label: string; gost: string }[]
}

export interface CheckResponse {
  result_id: number | null
  config_revision: number
  report: SubReport
}

// ── Конфигурация ─────────────────────────────────────────────────────────────

export interface FormulaSpec { label: string; expr: string; help?: string }

export interface RuleSpec {
  id: string
  label: string
  category: string
  severity: Severity
  enabled: boolean
  description?: string
}

export interface CustomRuleSpec {
  id: string
  label: string
  expr: string
  message?: string
  severity: Severity
  enabled: boolean
}

export interface ForbiddenItem {
  identifier: string
  label: string
  enabled: boolean
  note?: string
}

export interface ManualCheck { id: string; label: string; enabled: boolean; gost?: string }

export interface ClassItemSpec {
  identifier: string
  label: string
  min: number | null
  max: number | null
  enabled: boolean
  note?: string
  group?: string
}

export interface ClassGroupSpec {
  key: string
  label: string
  enabled: boolean
  items: ClassItemSpec[]
}

export interface ShipClassSpec {
  key: string
  name: string
  min_tonnage: number | null
  max_tonnage: number | null
  enabled: boolean
  requirements: Record<string, string>
  item_groups: ClassGroupSpec[]
}

export interface CheckerConfig {
  schema_version: number
  formulas: Record<string, FormulaSpec>
  equipment: Record<string, Record<string, unknown>>
  autofill: { exempt_tags: string[] }
  forbidden_items: ForbiddenItem[]
  rules: RuleSpec[]
  manual_checks: ManualCheck[]
  classes: ShipClassSpec[]
  custom_rules: CustomRuleSpec[]
  options: Record<string, boolean>
}

export interface ConfigResponse {
  config: CheckerConfig
  revision: number
  updated_at: string | null
  updated_by: string | null
  can_manage_rules: boolean
  can_manage_classes: boolean
  can_check: boolean
  formula_context: string[]
}

export interface HistoryRow {
  id: number
  project_id: number | null
  version_id: number | null
  project_name: string | null
  version_number: number | null
  filename: string | null
  submarine_name: string | null
  ship_class: string | null
  tonnage: number | null
  passed: boolean
  error_count: number
  warning_count: number
  config_revision: number | null
  is_auto: boolean
  checked_by: string | null
  created_at: string | null
}

/** Краткая сводка проверки версии — приезжает в карточке Sub-проекта. */
export interface VersionCheckSummary {
  id: number
  passed: boolean
  error_count: number
  warning_count: number
  ship_class: string | null
  tonnage: number | null
  created_at: string | null
}
