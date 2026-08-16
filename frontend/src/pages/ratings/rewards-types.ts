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

export interface RatingRewardSchedule {
  /** 0=понедельник .. 6=воскресенье */
  weekday: number
  /** "HH:MM" по МСК */
  time_msk: string
}

export interface RatingRewardCondition {
  /** Стабильный id правила — назначается бэкендом при первом сохранении,
   * используется для привязки текущих держателей наград (см. holders) и
   * состояния расписания. Пусто у ещё не сохранённого черновика. */
  id?: string
  role: string
  schedule: RatingRewardSchedule
  places: RatingRewardPlace[]
}

export interface RatingRewardHolder {
  rule_id: string
  place: number
  steam_id: string
  player_name: string | null
  reward_group_id: string | null
  granted_at: string | null
}

export const WEEKDAY_LABELS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

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
