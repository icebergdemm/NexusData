import {
  LayoutDashboard, Radio, Users, ShieldAlert, Gavel, PlayCircle,
  Ship, Star, Bot, Ticket, UserCog, Settings, KeyRound, Wrench,
  Clock, ScrollText, BarChart3, Tags, FileText, NotebookText,
} from "lucide-react"
import type { PermissionPage } from "@/lib/auth"

export interface NavItem {
  label: string
  to: string
  icon: typeof LayoutDashboard
  /** Permission page gating this item — omit for items visible to any authenticated admin. */
  permission?: PermissionPage
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
      { label: "Наказания", to: "/punishments", icon: Gavel, permission: "punishments" },
    ],
  },
  {
    label: "Диспетчерская",
    items: [
      { label: "Диспетчерская", to: "/dispatch", icon: Radio, permission: "dispatch" },
      { label: "Реплеи раундов", to: "/replays", icon: PlayCircle, permission: "replays" },
    ],
  },
  {
    label: "Проекты и рейтинг",
    items: [
      { label: "Sub-карты", to: "/sub-projects", icon: Ship, permission: "sub_projects" },
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
    label: "Документы",
    items: [
      { label: "Документы", to: "/documents", icon: NotebookText, permission: "documents" },
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
