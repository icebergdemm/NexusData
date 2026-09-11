import { Link } from "react-router-dom"
import {
  Users, ShieldAlert, Radio, PlayCircle, Ship, Star, Ticket, BarChart3, LayoutDashboard, VenetianMask,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { PageHeader } from "@/components/layout/PageHeader"
import { canView, useCurrentAdmin, type PermissionPage } from "@/lib/auth"
import { api } from "@/lib/api"
import {
  canEditDisguise, disguiseStatusLabel, makeDisguiseUpdate, type DisguiseMode, type DisguiseState,
} from "./disguise"

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

function DisguiseCard({ isSuperuser, canUse }: { isSuperuser: boolean; canUse: boolean }) {
  const { data, refetch } = useQuery({
    queryKey: ["me", "disguise"],
    queryFn: () => api.get<DisguiseState>("/api/v1/me/disguise"),
    enabled: canUse,
    retry: false,
  })
  const [enabled, setEnabled] = useState(false)
  const [mode, setMode] = useState<DisguiseMode>("random")
  const [fixedAlias, setFixedAlias] = useState("")

  useEffect(() => {
    if (!data) return
    setEnabled(data.enabled)
    setMode(data.mode)
    setFixedAlias(data.fixed_alias ?? "")
  }, [data])

  const save = useMutation({
    mutationFn: (nextEnabled: boolean) => api.put<DisguiseState>(
      "/api/v1/me/disguise", makeDisguiseUpdate(nextEnabled, mode, fixedAlias),
    ),
    onSuccess: (next) => {
      setEnabled(next.enabled)
      toast.success(next.enabled ? "Маскировка включена для следующего входа" : "Маскировка выключена")
      void refetch()
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (!isSuperuser) return null

  if (!canUse) {
    return <Card className="mt-4"><CardContent className="text-sm text-muted-foreground">
      Для маскировки сначала привяжите игровой профиль к своему аккаунту NexusData.
    </CardContent></Card>
  }

  return (
    <Card className="mt-4">
      <CardContent>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 font-semibold"><VenetianMask className="h-5 w-5 text-primary" />Маскировка</div>
            <p className="mt-1 text-xs text-muted-foreground">Меняет публичные имя и SteamID только при следующем входе.</p>
          </div>
          <Switch checked={enabled} disabled={save.isPending || !data} onCheckedChange={(next) => save.mutate(next)} />
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-[180px_1fr_auto] sm:items-end">
          <div className="grid gap-1.5">
            <Label htmlFor="disguise-mode">Режим</Label>
            <select id="disguise-mode" className="h-9 rounded-md border bg-background px-3 text-sm" value={mode} onChange={(event) => setMode(event.target.value as DisguiseMode)}>
              <option value="random">Случайный ник</option>
              <option value="fixed">Закреплённый ник</option>
            </select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="disguise-alias">Закреплённый ник</Label>
            <Input id="disguise-alias" value={fixedAlias} disabled={mode !== "fixed"} maxLength={32} onChange={(event) => setFixedAlias(event.target.value)} />
          </div>
          <Button variant="outline" disabled={save.isPending} onClick={() => save.mutate(enabled)}>Сохранить</Button>
        </div>

        {data && <div className="mt-3 text-xs text-muted-foreground">
          Версия {data.version} · {disguiseStatusLabel(data.servers)}
          {data.servers.length > 0 && ` · ${data.servers.map((server) => `${server.server_name ?? "сервер"}: ${server.status}`).join(", ")}`}
        </div>}
      </CardContent>
    </Card>
  )
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
              <CardContent>
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
        <CardContent className="text-sm text-muted-foreground">
          Некоторые редко используемые разделы пока доступны только в{" "}
          <a href="/adminold" data-no-spa className="text-primary underline-offset-2 hover:underline">
            классической панели
          </a>
          .
        </CardContent>
      </Card>
      <DisguiseCard
        isSuperuser={admin?.is_superuser === true}
        canUse={canEditDisguise(admin, admin?.linked_player)}
      />
    </div>
  )
}
