import { Link } from "react-router-dom"
import {
  Users, ShieldAlert, Radio, PlayCircle, Ship, Star, Ticket, BarChart3, LayoutDashboard, VenetianMask, Plus, Trash2, Settings2, ExternalLink,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { PageHeader } from "@/components/layout/PageHeader"
import { canDo, canView, useCurrentAdmin, type PermissionPage } from "@/lib/auth"
import { api } from "@/lib/api"
import {
  canEditDisguise, disguiseStatusLabel, makeDisguiseUpdate, type DisguiseMode, type DisguiseProfile, type DisguiseState,
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

function DisguiseCard({ canUsePermission, canUse }: { canUsePermission: boolean; canUse: boolean }) {
  const { data, refetch } = useQuery({
    queryKey: ["me", "disguise"],
    queryFn: () => api.get<DisguiseState>("/api/v1/me/disguise"),
    enabled: canUse,
    retry: false,
  })
  const [enabled, setEnabled] = useState(false)
  const [mode, setMode] = useState<DisguiseMode>("random")
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null)
  const [profiles, setProfiles] = useState<DisguiseProfile[]>([])
  const [draftProfiles, setDraftProfiles] = useState<DisguiseProfile[]>([])
  const [profilesOpen, setProfilesOpen] = useState(false)

  useEffect(() => {
    if (!data) return
    setEnabled(data.enabled)
    setMode(data.mode)
    setSelectedProfileId(data.selected_profile_id)
    setProfiles(data.profiles ?? [])
  }, [data])

  const aliasFor = (profileId: number | null, source = profiles) =>
    source.find((profile) => profile.id === profileId)?.alias ?? ""

  const save = useMutation({
    mutationFn: (input: { enabled: boolean; mode: DisguiseMode; selectedProfileId: number | null; profiles?: DisguiseProfile[] }) => api.put<DisguiseState>(
      "/api/v1/me/disguise",
      makeDisguiseUpdate(input.enabled, input.mode, aliasFor(input.selectedProfileId, input.profiles ?? profiles), input.selectedProfileId, input.profiles),
    ),
    onSuccess: (next) => {
      setEnabled(next.enabled)
      setMode(next.mode)
      setSelectedProfileId(next.selected_profile_id)
      setProfiles(next.profiles ?? [])
      toast.success(next.enabled ? "Маскировка включена для следующего входа" : "Маскировка выключена")
      void refetch()
    },
    onError: (error: Error) => {
      toast.error(error.message)
      void refetch()
    },
  })

  if (!canUsePermission) return null

  if (!canUse) {
    return <Card className="mt-4"><CardContent className="text-sm text-muted-foreground">
      Для маскировки сначала привяжите игровой профиль к своему аккаунту NexusData.
    </CardContent></Card>
  }

  const openProfiles = () => {
    setDraftProfiles(profiles.map((profile) => ({ ...profile })))
    setProfilesOpen(true)
  }

  const updateDraftProfile = (id: number, patch: Partial<DisguiseProfile>) => {
    setDraftProfiles((current) => current.map((profile) => profile.id === id ? { ...profile, ...patch } : profile))
  }

  const addDraftProfile = () => {
    const id = -Date.now()
    setDraftProfiles((current) => [...current, {
      id,
      alias: "Матрос Новый",
      public_steam_id: null,
      enabled: true,
      sort_order: current.length,
    }])
  }

  const removeDraftProfile = (id: number) => {
    setDraftProfiles((current) => current.filter((profile) => profile.id !== id))
    if (selectedProfileId === id) setSelectedProfileId(null)
  }

  const updatePolicy = (next: { enabled: boolean; mode: DisguiseMode; selectedProfileId: number | null; profiles?: DisguiseProfile[] }) => {
    // The control changes immediately; the server queue receives the policy
    // independently and must never make this small form feel blocked.
    setEnabled(next.enabled)
    setMode(next.mode)
    setSelectedProfileId(next.selectedProfileId)
    if (next.profiles) setProfiles(next.profiles)
    save.mutate(next)
  }

  const setRandom = (random: boolean) => {
    const nextSelected = random
      ? selectedProfileId
      : (profiles.some((profile) => profile.id === selectedProfileId && profile.enabled)
        ? selectedProfileId
        : (profiles.find((profile) => profile.enabled)?.id ?? null))
    updatePolicy({ enabled, mode: random ? "random" : "fixed", selectedProfileId: nextSelected })
  }

  const saveProfiles = () => {
    const nextSelected = mode === "fixed"
      ? (draftProfiles.some((profile) => profile.id === selectedProfileId && profile.enabled)
        ? selectedProfileId
        : (draftProfiles.find((profile) => profile.enabled)?.id ?? null))
      : selectedProfileId
    updatePolicy({ enabled, mode, profiles: draftProfiles, selectedProfileId: nextSelected })
    setProfilesOpen(false)
  }

  return (
    <Card className="mt-4">
      <CardContent>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 font-semibold"><VenetianMask className="h-5 w-5 text-primary" />Маскировка</div>
            <p className="mt-1 text-xs text-muted-foreground">Меняет публичные имя и SteamID только при следующем входе.</p>
          </div>
          <Switch checked={enabled} disabled={save.isPending || !data} onCheckedChange={(next) => updatePolicy({ enabled: next, mode, selectedProfileId })} />
        </div>

        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div className="flex h-9 items-center gap-2 rounded-md border px-3">
            <Switch id="disguise-random" checked={mode === "random"} disabled={save.isPending || !data} onCheckedChange={setRandom} />
            <Label htmlFor="disguise-random" className="cursor-pointer text-sm">Случайный профиль</Label>
          </div>
          <div className="grid min-w-[260px] flex-1 gap-1.5">
            <Label htmlFor="disguise-profile">Псевдоним</Label>
            <select id="disguise-profile" className="h-9 w-full rounded-md border bg-background px-3 text-sm disabled:cursor-not-allowed disabled:opacity-50" disabled={mode === "random" || save.isPending} value={selectedProfileId ?? ""} onChange={(event) => {
              const id = event.target.value ? Number(event.target.value) : null
              updatePolicy({ enabled, mode: "fixed", selectedProfileId: id })
            }}>
              <option value="">Выберите профиль</option>
              {profiles.filter((profile) => profile.enabled).map((profile) => <option key={profile.id} value={profile.id}>{profile.alias}</option>)}
            </select>
          </div>
          <Button size="sm" variant="outline" disabled={save.isPending} onClick={openProfiles}><Settings2 className="mr-1.5 h-4 w-4" />Настройки</Button>
        </div>

        {data && <div className="mt-3 text-xs text-muted-foreground">
          Версия {data.version} · {disguiseStatusLabel(data.servers)}. Синхронизация с игровыми серверами идёт в фоне.
        </div>}
      </CardContent>

      <Dialog open={profilesOpen} onOpenChange={setProfilesOpen}>
        <DialogContent className="w-[96vw] max-w-[1000px] sm:max-w-[1000px]">
          <DialogHeader>
            <DialogTitle>Маскировочные профили</DialogTitle>
            <DialogDescription>Ник видят игроки. Пустой SteamID означает синтетический профиль. Для настоящего публичного профиля укажите SteamID64 и проверьте его ссылкой справа.</DialogDescription>
          </DialogHeader>
          <div className="max-h-[55vh] overflow-auto rounded-md border">
            <Table className="min-w-[840px] table-fixed">
              <TableHeader><TableRow><TableHead className="w-[42%]">Публичный ник</TableHead><TableHead className="w-[42%]">SteamID64 (необязательно)</TableHead><TableHead className="w-24">Активен</TableHead><TableHead className="w-12" /></TableRow></TableHeader>
              <TableBody>
                {draftProfiles.map((profile) => <TableRow key={profile.id}>
                  <TableCell><Input value={profile.alias} maxLength={32} onChange={(event) => updateDraftProfile(profile.id, { alias: event.target.value })} /></TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Input value={profile.public_steam_id ?? ""} placeholder="Синтетический" maxLength={17} onChange={(event) => updateDraftProfile(profile.id, { public_steam_id: event.target.value || null })} />
                      {/^(7656119\d{10})$/.test(profile.public_steam_id ?? "") && <a className="text-muted-foreground hover:text-primary" href={`https://steamcommunity.com/profiles/${profile.public_steam_id}`} target="_blank" rel="noreferrer" title="Открыть профиль Steam"><ExternalLink className="h-4 w-4" /></a>}
                    </div>
                  </TableCell>
                  <TableCell><Switch checked={profile.enabled} onCheckedChange={(checked) => updateDraftProfile(profile.id, { enabled: checked })} /></TableCell>
                  <TableCell><Button variant="ghost" size="icon" onClick={() => removeDraftProfile(profile.id)} aria-label="Удалить профиль"><Trash2 className="h-4 w-4" /></Button></TableCell>
                </TableRow>)}
              </TableBody>
            </Table>
          </div>
          {data?.reserved_aliases?.length ? (
            <div className="rounded-md border bg-muted/20 p-3 text-xs">
              <div className="mb-2 font-medium">Занятые имена</div>
              <div className="max-h-32 space-y-1 overflow-y-auto pr-1">
                {data.reserved_aliases.map((reservation) => (
                  <div key={`${reservation.alias}-${reservation.owner_admin_id ?? "unknown"}`} className="flex items-center justify-between gap-3">
                    <span className="truncate">{reservation.alias}</span>
                    <span className="shrink-0 text-muted-foreground">{reservation.is_self ? "Вы" : reservation.owner_name}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <Button variant="outline" onClick={addDraftProfile}><Plus className="mr-2 h-4 w-4" />Добавить профиль</Button>
          <DialogFooter>
            <Button variant="outline" onClick={() => setProfilesOpen(false)}>Отмена</Button>
            <Button disabled={save.isPending || draftProfiles.length === 0} onClick={saveProfiles}>Сохранить профили</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
        canUsePermission={canDo(admin, "disguise", "use")}
        canUse={canEditDisguise(admin, admin?.linked_player)}
      />
    </div>
  )
}
