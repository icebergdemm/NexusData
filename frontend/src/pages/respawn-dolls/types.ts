/** Типы конструктора кукол респавна.
 *
 *  Тело куклы (DollSpec) — ровно та структура, которую ждёт плагин
 *  RespawnDolls; конструктор редактирует её напрямую, без промежуточной
 *  модели, чтобы вкладка «JSON» показывала именно то, что уйдёт на сервер.
 */

export interface Identity {
  name?: string
  /** Набор тегов пресета целиком (["head3","male"]) — движок сверяет его
   *  точным равенством множества, одного тега недостаточно. */
  head_tags?: string[]
  hair_index?: number
  beard_index?: number
  face_attachment_index?: number
  hair_color?: string
  facial_hair_color?: string
  skin_color?: string
  personality?: string
}

export interface InventoryItem {
  identifier: string
  amount?: number
  /** Прочность в процентах, 0..100. */
  condition?: number
  /** Качество 0..3 (обычное … шедевр). */
  quality?: number
  slot?: string
  tags?: string[]
  contained?: InventoryItem[]
}

export interface DollSpec {
  identity?: Identity
  job?: { identifier?: string; variant?: number; skills?: Record<string, number> }
  progression?: { experience?: number; additional_talent_points?: number; talents?: string[] }
  stats?: { stat_type: string; value: number }[]
  health?: { afflictions?: { identifier: string; strength?: number; limb?: string }[] }
  inventory?: { items?: InventoryItem[] }
}

export interface Doll {
  public_id: string
  server: string
  label: string | null
  priority: number
  one_shot: boolean
  spawn_mode: string
  team: number
  expires_at: string | null
  bind_steamids: string[]
  spec: DollSpec
  state: string
  state_message: string | null
  spawned_at: string | null
  spawned_steamid: string | null
  spawned_character_name: string | null
  created_by: string | null
  created_at: string | null
}

export interface SpriteRef {
  /** Имя файла спрайт-листа в /static/appearance/. */
  sheet: string
  /** Ячейка [колонка, строка] в сетке листа. */
  cell: [number, number]
}

export interface HeadPreset extends SpriteRef {
  tags: string[]
  gender: string
}

export interface WearableSprite extends SpriteRef {
  index: number
  name: string
}

export interface WearableSet {
  hair: WearableSprite[]
  beard: WearableSprite[]
  face_attachment: WearableSprite[]
}

export interface AppearanceCatalog {
  sprite_grid: { columns: number; rows: number; cell: number }
  sprite_base_url: string
  heads: HeadPreset[]
  wearables: Record<string, WearableSet>
  colors: { hair: string[]; facial_hair: string[]; skin: string[] }
  personalities: string[]
  personality_labels: Record<string, string>
}

export interface JobReference {
  identifier: string
  name: string
  /** Число вариантов стартового снаряжения (ItemSet в Jobs.xml). */
  variants: number
  default_skills: Record<string, number>
}

export interface DollReference {
  jobs: JobReference[]
  skill_labels: Record<string, string>
  talents: Record<string, { identifier: string; name: string }[]>
  afflictions: { identifier: string; name: string; max_strength: number; limb_specific: boolean }[]
  stat_types: string[]
}

export interface DollsResponse {
  dolls: Doll[]
  can_create: boolean
  can_cancel: boolean
  reference: {
    jobs: string[]
    skills: string[]
    slots: string[]
    limbs: string[]
    spawn_modes: string[]
    states: string[]
  }
}

export interface BindCandidate {
  steamid: string
  name: string
  online: boolean
  server: string | null
  avatar_url: string | null
  last_login: string | null
}

export const SERVERS = ["ASPER1", "ASPER2"]

export const STATE_LABEL: Record<string, string> = {
  draft: "Черновик",
  pending: "В очереди",
  spawned: "Заспавнена",
  spawned_with_errors: "Заспавнена с ошибками",
  failed: "Ошибка",
  cancelled: "Отменена",
  expired: "Просрочена",
}

export const STATE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  draft: "outline",
  pending: "default",
  spawned: "secondary",
  spawned_with_errors: "outline",
  failed: "destructive",
  cancelled: "outline",
  expired: "outline",
}

/** Слоты снаряжения в том порядке, в котором они идут в игровом инвентаре. */
export const EQUIP_SLOTS: { key: string; label: string }[] = [
  { key: "Head", label: "Голова" },
  { key: "InnerClothes", label: "Одежда" },
  { key: "OuterClothes", label: "Костюм" },
  { key: "Headset", label: "Гарнитура" },
  { key: "Card", label: "Карта" },
  { key: "Bag", label: "Сумка" },
  { key: "RightHand", label: "Правая рука" },
  { key: "LeftHand", label: "Левая рука" },
  { key: "HealthInterface", label: "Мед. интерфейс" },
]

export const LIMBS = [
  "Head", "Torso", "LeftArm", "RightArm", "LeftLeg", "RightLeg",
  "LeftHand", "RightHand", "LeftFoot", "RightFoot", "Waist", "Jaw",
]

export const QUALITY_LABELS = ["Обычное", "Хорошее", "Отличное", "Шедевр"]
