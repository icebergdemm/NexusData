export interface RewardGroup {
  id: string
  name: string
  items: Record<string, number>
  stats: Record<string, number | string>
  talents: Record<string, string[]>
}

export interface RatingRewardPlace {
  place: number
  reward_group_id: string
}

export interface RatingRewardCondition {
  role: string
  check_interval_days: number
  places: RatingRewardPlace[]
}

export type PlaytimePeriod = "day" | "week" | "month"

export interface PlaytimeCondition {
  period: PlaytimePeriod
  min_seconds: number
  reward_group_id: string
}

export interface PlaytimePlayerStat {
  name?: string
  steamid: string
  day: number
  week: number
  month: number
}

export interface PlaytimeStats {
  generated_at: string | null
  players: Record<string, PlaytimePlayerStat>
}

export const ROLES = ["captain", "mechanic", "medic", "security", "engineer"] as const
export const ROLE_LABELS: Record<string, string> = {
  captain: "Капитан", mechanic: "Механик", medic: "Врач", security: "СБ", engineer: "Инженер",
}
export const PERIOD_LABELS: Record<PlaytimePeriod, string> = { day: "день", week: "неделю", month: "месяц" }
