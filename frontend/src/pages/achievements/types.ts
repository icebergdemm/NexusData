// Контракт с routes/achievements.py — см. ACHIEVEMENTS_API_GUIDE.md.
//
// Источник истины формы — СТРУКТУРЫ (TriggerSpec/RewardSpec), а не текст JSON:
// конструктор правит объект напрямую, вкладка JSON показывает и принимает тот
// же объект (как в конструкторе кукол, см. respawn-dolls/DollEditor.tsx). Так
// поля и JSON не могут разъехаться.

export interface AchievementRow {
  id: number
  key: string
  title: string
  description: string | null
  icon: string | null
  category: string | null
  enabled: boolean
  hidden: boolean
  scope: "persistent" | "round"
  servers: string[]
  trigger: TriggerSpec
  rewards: RewardSpec[]
  requires: string[]
  chain_group: string | null
  chain_order: number
  sort_order: number
  notify_discord: boolean
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

export interface EventFieldDef {
  label: string
  values?: (string | boolean)[]
}

export interface EventDef {
  label: string
  fields: Record<string, EventFieldDef>
}

/** Какой виджет показать для значения условия — см. FIELD_KINDS в
 *  services_achievements.py. Неизвестное поле = свободный ввод. */
export type FieldKind = "item" | "job" | "talent" | "affliction" | "skill" | "number" | "text"

export interface AchievementsReference {
  events: Record<string, EventDef>
  field_kinds: Record<string, FieldKind>
  condition_ops: string[]
  reward_types: string[]
  scopes: string[]
  skills: string[]
  skill_labels: Record<string, string>
  jobs: { identifier: string; name: string; variants: number; default_skills: Record<string, number> }[]
  talents: Record<string, { identifier: string; name: string }[]>
  stat_types: string[]
  afflictions: { identifier: string; name: string; max_strength: number; limb_specific: boolean }[]
  zone_types: string[]
  servers: string[]
}

export interface AchievementsListResponse {
  achievements: AchievementRow[]
  can_edit: boolean
  can_grant: boolean
  reference: AchievementsReference
}

export interface PlayerProgressRow {
  achievement_key: string
  title: string
  icon: string | null
  category: string | null
  progress: number
  target: number
  distinct_values: string[]
  unlocked: boolean
  unlocked_at: string | null
  granted_manually: boolean
}

// ── Триггер ───────────────────────────────────────────────────────────────

export type ConditionValue = string | number | boolean | (string | number)[]

export type ConditionNode =
  | { all: ConditionNode[] }
  | { any: ConditionNode[] }
  | { not: ConditionNode }
  | { field: string; op: string; value: ConditionValue }

export interface TriggerSpec {
  event: string
  count?: number
  distinct_by?: string
  sum_field?: string
  cooldown_seconds?: number
  max_per_round?: number
  window_seconds?: number
  reset_on_death?: boolean
  conditions?: ConditionNode
}

export function isGroup(n: ConditionNode): n is { all: ConditionNode[] } | { any: ConditionNode[] } {
  return "all" in n || "any" in n
}

export function isNot(n: ConditionNode): n is { not: ConditionNode } {
  return "not" in n
}

export function isLeaf(n: ConditionNode): n is { field: string; op: string; value: ConditionValue } {
  return !isGroup(n) && !isNot(n)
}

export function groupKey(n: { all: ConditionNode[] } | { any: ConditionNode[] }): "all" | "any" {
  return "all" in n ? "all" : "any"
}

export function kindOf(field: string, reference: AchievementsReference): FieldKind {
  return reference.field_kinds?.[field] ?? "text"
}

/** Значение по умолчанию при смене поля/оператора: пустая строка в числовом
 *  поле — гарантированная ошибка валидации на сохранении. */
export function defaultValue(
  field: string, op: string, eventDef: EventDef | undefined, reference: AchievementsReference,
): ConditionValue {
  if (op === "in" || op === "not_in") return []
  const values = eventDef?.fields?.[field]?.values
  if (values?.length) return typeof values[0] === "boolean" ? (values[0] as boolean) : String(values[0])
  return kindOf(field, reference) === "number" ? 0 : ""
}

/** Заготовка листа условия для первого поля события. */
export function blankLeaf(eventDef: EventDef | undefined, reference: AchievementsReference): ConditionNode {
  const field = Object.keys(eventDef?.fields ?? {})[0] ?? ""
  return { field, op: "eq", value: defaultValue(field, "eq", eventDef, reference) }
}

/** Режим подсчёта прогресса — в JSON это три взаимоисключающих варианта
 *  (обычный счётчик / distinct_by / sum_field), в UI один переключатель. */
export type CountMode = "events" | "distinct" | "sum"

export function countModeOf(t: TriggerSpec): CountMode {
  if (t.distinct_by) return "distinct"
  if (t.sum_field) return "sum"
  return "events"
}

// ── Награды ───────────────────────────────────────────────────────────────

export interface ItemReward {
  type: "item"
  identifier: string
  amount?: number
  quality?: number
  condition?: number
}

export interface StatReward {
  type: "stat"
  stat_type: string
  identifier: string
  value: number
  remove_on_death?: boolean
}

export interface SkillReward {
  type: "skill"
  skill: string
  amount: number
}

export interface TalentReward {
  type: "talent"
  talent_id: string
}

export type RewardSpec = ItemReward | StatReward | SkillReward | TalentReward

export const REWARD_TYPE_LABELS: Record<RewardSpec["type"], string> = {
  item: "Предмет",
  stat: "Постоянный модификатор (стат)",
  skill: "Навык",
  talent: "Талант",
}

export const QUALITY_LABELS = ["Обычное", "Хорошее", "Отличное", "Шедевр"]

export const OP_LABELS: Record<string, string> = {
  eq: "= равно",
  neq: "≠ не равно",
  gt: "> больше",
  gte: "≥ больше или равно",
  lt: "< меньше",
  lte: "≤ меньше или равно",
  in: "∈ входит в список",
  not_in: "∉ не входит в список",
  contains: "содержит подстроку",
}

/** Операторы сравнения по величине бессмысленны для строковых полей —
 *  плагин сравнивает их численно (см. AchievementEngine.Match). */
export const NUMERIC_OPS = ["gt", "gte", "lt", "lte"]

// ── Состояние формы ───────────────────────────────────────────────────────

export interface AchievementFormState {
  key: string
  title: string
  description: string
  icon: string
  category: string
  enabled: boolean
  hidden: boolean
  scope: "persistent" | "round"
  servers: string[]
  requires: string[]
  chainGroup: string
  chainOrder: string
  sortOrder: string
  notifyDiscord: boolean
  trigger: TriggerSpec
  rewards: RewardSpec[]
}

export const EMPTY_FORM: AchievementFormState = {
  key: "",
  title: "",
  description: "",
  icon: "🏆",
  category: "",
  enabled: true,
  hidden: false,
  scope: "persistent",
  servers: [],
  requires: [],
  chainGroup: "",
  chainOrder: "0",
  sortOrder: "0",
  notifyDiscord: true,
  trigger: {
    event: "kill",
    count: 5,
    conditions: { field: "target_type", op: "eq", value: "monster" },
  },
  rewards: [],
}

export function rowToForm(r: AchievementRow): AchievementFormState {
  return {
    key: r.key,
    title: r.title,
    description: r.description ?? "",
    icon: r.icon ?? "",
    category: r.category ?? "",
    enabled: r.enabled,
    hidden: r.hidden,
    scope: r.scope,
    servers: r.servers ?? [],
    requires: r.requires ?? [],
    chainGroup: r.chain_group ?? "",
    chainOrder: String(r.chain_order ?? 0),
    sortOrder: String(r.sort_order ?? 0),
    notifyDiscord: r.notify_discord,
    trigger: { count: 1, ...(r.trigger ?? {}), event: r.trigger?.event ?? "kill" },
    rewards: (r.rewards ?? []) as RewardSpec[],
  }
}

/** Выкидывает пустые/выключенные модификаторы, чтобы в базу и на игровой
 *  сервер уходил ровно тот минимум, который админ настроил, а не объект с
 *  десятком null-полей. */
export function cleanTrigger(t: TriggerSpec): TriggerSpec {
  const out: TriggerSpec = { event: t.event, count: Math.max(1, Number(t.count) || 1) }
  if (t.distinct_by) out.distinct_by = t.distinct_by
  if (t.sum_field) out.sum_field = t.sum_field
  if (t.cooldown_seconds != null && t.cooldown_seconds > 0) out.cooldown_seconds = t.cooldown_seconds
  if (t.max_per_round != null && t.max_per_round > 0) out.max_per_round = t.max_per_round
  if (t.window_seconds != null && t.window_seconds > 0) out.window_seconds = t.window_seconds
  if (t.reset_on_death) out.reset_on_death = true
  if (t.conditions) out.conditions = t.conditions
  return out
}

export function formToPayload(f: AchievementFormState): Record<string, unknown> {
  return {
    key: f.key.trim().toLowerCase(),
    title: f.title.trim(),
    description: f.description.trim() || null,
    icon: f.icon.trim() || null,
    category: f.category.trim() || null,
    enabled: f.enabled,
    hidden: f.hidden,
    scope: f.scope,
    servers: f.servers,
    requires: f.requires,
    chain_group: f.chainGroup.trim() || null,
    chain_order: Number(f.chainOrder || 0),
    sort_order: Number(f.sortOrder || 0),
    notify_discord: f.notifyDiscord,
    trigger: cleanTrigger(f.trigger),
    rewards: f.rewards,
  }
}
