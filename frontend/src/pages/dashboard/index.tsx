import { Link } from "react-router-dom"
import {
  Users, ShieldAlert, Radio, PlayCircle, Ship, Star, Ticket, BarChart3, LayoutDashboard,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { PageHeader } from "@/components/layout/PageHeader"
import { canView, useCurrentAdmin, type PermissionPage } from "@/lib/auth"

const QUICK_LINKS: { label: string; description: string; to: string; icon: typeof Users; permission?: PermissionPage; accent: string }[] = [
  { label: "Статус раунда", description: "Онлайн, локация, миссия обоих серверов", to: "/round-status", icon: PlayCircle, accent: "primary" },
  { label: "Игроки", description: "Профили, статистика, награды", to: "/players", permission: "players", icon: Users, accent: "info" },
  { label: "Модерирование", description: "Кто онлайн, банхаммер, античит", to: "/moderation", permission: "moderation", icon: ShieldAlert, accent: "warning" },
  { label: "Диспетчерская", description: "Радиосвязь с игроками в реальном времени", to: "/dispatch", permission: "dispatch", icon: Radio, accent: "success" },
  { label: "Sub-карты", description: "Проекты подлодок и станций", to: "/sub-projects", permission: "sub_projects", icon: Ship, accent: "info" },
  { label: "Рейтинг", description: "Лидерборды и система наград", to: "/ratings", permission: "ratings", icon: Star, accent: "warning" },
  { label: "Тикеты", description: "Обращения игроков", to: "/tickets", permission: "tickets", icon: Ticket, accent: "primary" },
  { label: "Аналитика", description: "Графики онлайна и активности", to: "/analytics", permission: "admin", icon: BarChart3, accent: "success" },
]

const ACCENT_CLASSES: Record<string, string> = {
  primary: "bg-primary/15 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  info: "bg-info/15 text-info",
}

export function DashboardPage() {
  const { data: admin } = useCurrentAdmin()
  const visibleLinks = QUICK_LINKS.filter((l) => !l.permission || canView(admin, l.permission))

  return (
    <div>
      <PageHeader
        title={`Добро пожаловать, ${admin?.discord_username ?? ""}`}
        description="Панель управления NexusData"
        icon={LayoutDashboard}
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {visibleLinks.map((link) => (
          <Link key={link.to} to={link.to}>
            <Card className="h-full transition-all hover:border-primary/50 hover:shadow-md">
              <CardContent className="pt-6">
                <div className={`mb-3 flex h-10 w-10 items-center justify-center rounded-lg ${ACCENT_CLASSES[link.accent]}`}>
                  <link.icon className="h-5 w-5" />
                </div>
                <div className="font-semibold">{link.label}</div>
                <div className="mt-1 text-xs text-muted-foreground">{link.description}</div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      <Card className="mt-4">
        <CardContent className="pt-6 text-sm text-muted-foreground">
          Некоторые редко используемые разделы пока доступны только в{" "}
          <a href="/admin" data-no-spa className="text-primary underline-offset-2 hover:underline">
            классической панели
          </a>
          .
        </CardContent>
      </Card>
    </div>
  )
}
