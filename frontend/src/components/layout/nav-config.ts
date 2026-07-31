import {
  LayoutDashboard, Radio, Users, ShieldAlert, Gavel, PlayCircle,
  Ship, Star, Bot, Ticket, UserCog, Settings, KeyRound, Wrench,
  Clock, ScrollText, BarChart3, Tags, FileText, NotebookText, Building2,
  KanbanSquare, Globe2, Music, UserPlus, ClipboardCheck,
} from "lucide-react"
import type { CurrentAdmin, PermissionPage } from "@/lib/auth"

export interface NavItem {
  label: string
  to: string
  icon: typeof LayoutDashboard
  /** Permission page gating this item — omit for items visible to any authenticated admin. */
  permission?: PermissionPage
  /** Extra visibility predicate for items not gated by the standard page/
   * action permission model (e.g. department-scoped features like the
   * Polygon zone). Evaluated in addition to `permission` when both are set. */
  isVisible?: (admin: CurrentAdmin | undefined) => boolean
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Обзор",
    items: [
      { label: "Дашборд", to: "/", icon: LayoutDashboard },
      { label: "Статус раунда", to: "/round-status", icon: PlayCircle },
    ],
  },
  {
    label: "Игроки и модерация",
    items: [
      { label: "Игроки", to: "/players", icon: Users, permission: "players" },
      { label: "Метки игроков", to: "/label-categories", icon: Tags, permission: "players" },
      { label: "Модерирование", to: "/moderation", icon: ShieldAlert, permission: "moderation" },
      { label: "Логи сервера", to: "/logs", icon: FileText, permission: "moderation" },
      { label: "Звуки для ивентов", to: "/sounds", icon: Music, permission: "sounds" },
      { label: "Куклы респавна", to: "/respawn-dolls", icon: UserPlus, permission: "respawn_dolls" },
      { label: "Наказания", to: "/punishments", icon: Gavel, permission: "punishments" },
    ],
  },
  {
    label: "Диспетчерская",
    items: [
      { label: "Диспетчерская", to: "/dispatch", icon: Radio, permission: "dispatch" },
      { label: "Обзор мира", to: "/world-overview", icon: Globe2, permission: "world_state" },
      { label: "Прошедшие раунды", to: "/replays", icon: PlayCircle, permission: "replays" },
    ],
  },
  {
    label: "Проекты и рейтинг",
    items: [
      { label: "Sub-карты", to: "/sub-projects", icon: Ship, permission: "sub_projects" },
      {
        label: "Тестовый полигон ASPER", to: "/sub-projects-polygon", icon: Ship,
        isVisible: (admin) => !!admin && (admin.is_superuser || admin.my_departments.some((d) => d.test_server_view)),
      },
      { label: "Чекер кораблей", to: "/sub-checker", icon: ClipboardCheck, permission: "sub_checker" },
      { label: "Рейтинг", to: "/ratings", icon: Star, permission: "ratings" },
    ],
  },
  {
    label: "Discord и тикеты",
    items: [
      { label: "Discord бот", to: "/discord-bot", icon: Bot, permission: "discord_bot" },
      { label: "Тикеты", to: "/tickets", icon: Ticket, permission: "tickets" },
    ],
  },
  {
    label: "Рабочая зона",
    items: [
      { label: "Документы", to: "/documents", icon: NotebookText, permission: "documents" },
      { label: "Задачи", to: "/tasks", icon: KanbanSquare, permission: "tasks" },
      { label: "Отделы", to: "/departments", icon: Building2, permission: "departments" },
    ],
  },
  {
    label: "Система",
    items: [
      { label: "Пользователи Nexus Data", to: "/users", icon: UserCog, permission: "admin" },
      { label: "Администрирование", to: "/administration", icon: Settings, permission: "admin" },
      { label: "Серверные привилегии", to: "/server-privileges", icon: KeyRound, permission: "privileges" },
      { label: "Редактирование китов", to: "/kits", icon: Wrench, permission: "kits" },
      { label: "Сессии", to: "/sessions", icon: Clock, permission: "sessions" },
      { label: "Аудит", to: "/audit", icon: ScrollText, permission: "audit" },
      { label: "Аналитика", to: "/analytics", icon: BarChart3, permission: "admin" },
    ],
  },
]
