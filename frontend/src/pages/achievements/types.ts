// Контракт с routes/achievements.py — см. ACHIEVEMENTS_API_GUIDE.md.

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
  trigger: Record<string, unknown>
  rewards: unknown[]
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

export interface AchievementsReference {
  events: Record<string, EventDef>
  condition_ops: string[]
  reward_types: string[]
  scopes: string[]
  skills: string[]
  jobs: { identifier: string; name: string; variants: number; default_skills: Record<string, number> }[]
  talents: Record<string, { identifier: string; name: string }[]>
  stat_types: string[]
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

/** Форма редактора — все составные поля как текст (JSON/CSV), парсятся при отправке. */
export interface AchievementFormState {
  key: string
  title: string
  description: string
  icon: string
  category: string
  enabled: boolean
  hidden: boolean
  scope: "persistent" | "round"
  serversCsv: string
  requiresCsv: string
  chainGroup: string
  chainOrder: string
  sortOrder: string
  notifyDiscord: boolean
  triggerJson: string
  rewardsJson: string
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
  serversCsv: "",
  requiresCsv: "",
  chainGroup: "",
  chainOrder: "0",
  sortOrder: "0",
  notifyDiscord: true,
  triggerJson: '{\n  "event": "kill",\n  "count": 5,\n  "conditions": {\n    "field": "target_type",\n    "op": "eq",\n    "value": "monster"\n  }\n}',
  rewardsJson: "[]",
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
    serversCsv: (r.servers ?? []).join(", "),
    requiresCsv: (r.requires ?? []).join(", "),
    chainGroup: r.chain_group ?? "",
    chainOrder: String(r.chain_order ?? 0),
    sortOrder: String(r.sort_order ?? 0),
    notifyDiscord: r.notify_discord,
    triggerJson: JSON.stringify(r.trigger ?? {}, null, 2),
    rewardsJson: JSON.stringify(r.rewards ?? [], null, 2),
  }
}

function splitCsv(v: string): string[] {
  return v.split(",").map((s) => s.trim()).filter(Boolean)
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
    servers: splitCsv(f.serversCsv),
    requires: splitCsv(f.requiresCsv),
    chain_group: f.chainGroup.trim() || null,
    chain_order: Number(f.chainOrder || 0),
    sort_order: Number(f.sortOrder || 0),
    notify_discord: f.notifyDiscord,
    trigger: JSON.parse(f.triggerJson || "{}"),
    rewards: JSON.parse(f.rewardsJson || "[]"),
  }
}
