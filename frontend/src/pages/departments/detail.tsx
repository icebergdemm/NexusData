import { useEffect, useState } from "react"
import { useLocation, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Building2, Settings, Settings2, Plus, Crown, Trash2, UserPlus, ArrowLeft, Pencil, Star, ShieldAlert,
  AlertTriangle, KeyRound,
} from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription,
  AlertDialogFooter, AlertDialogAction,
} from "@/components/ui/alert-dialog"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PlatformIcon, PlatformIconField } from "@/components/layout/PlatformIcon"
import { api, ApiError } from "@/lib/api"
import { useCurrentAdmin, usePermissionRegistry } from "@/lib/auth"
import { PermissionMatrixEditor, type PermissionValue } from "@/pages/users/PermissionMatrixEditor"
import {
  departmentsApi, OPTIONAL_MODULE_KEYS, MODULE_KEYS, MODULE_LABELS,
  type DepartmentDetail, type DepartmentAccessLevel, type DepartmentMemberRow, type DepartmentModule,
} from "./api"
import { DocumentationTab } from "./documentation"
import { RatingTab, AddRatingEntryDialog } from "./rating"
import { OrdersTab } from "./orders"
import { NewsTab } from "./news"
import { TestServerTab } from "./test-server"
import { ChatTab } from "./chat"

function EditDepartmentDialog({ open, onOpenChange, department, isSuperuser }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  department: DepartmentDetail
  isSuperuser: boolean
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    description: department.description ?? "",
    icon: department.icon ?? "",
    color: department.color ?? "#1a68de",
    base_discord_role_id: department.base_discord_role_id ?? "",
    base_discord_role_name: department.base_discord_role_name ?? "",
    enabled_modules: department.enabled_modules ?? [],
    orders_channel_id: department.orders_channel_id ?? "",
    news_channel_id: department.news_channel_id ?? "",
    rating_channel_id: department.rating_channel_id ?? "",
    pterodactyl_server_uuid: department.pterodactyl_server_uuid ?? "",
    discord_category_id: department.discord_category_id ?? "",
    head_server_privilege_group_id: department.head_server_privilege_group_id,
  })

  const { data: privGroupsData } = useQuery({
    queryKey: ["server-privileges-groups-picker"],
    queryFn: () => api.get<{ priv_groups: { id: number; name: string }[] }>("/api/v1/admin/server-privileges"),
    enabled: isSuperuser && open,
  })

  const updateMutation = useMutation({
    mutationFn: () => departmentsApi.update(department.id, form),
    onSuccess: () => {
      toast.success("Отдел обновлён")
      queryClient.invalidateQueries({ queryKey: ["department", department.id] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const toggleModule = (mod: DepartmentModule, checked: boolean) => {
    setForm((f) => ({
      ...f,
      enabled_modules: checked ? [...f.enabled_modules, mod] : f.enabled_modules.filter((m) => m !== mod),
    }))
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Настройки отдела</DialogTitle>
          <DialogDescription>{isSuperuser ? "Полный доступ суперпользователя." : "Доступно главе отдела: описание, иконка, цвет."}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="mb-1.5 block">Иконка</Label>
              <PlatformIconField value={form.icon} onChange={(icon) => setForm((f) => ({ ...f, icon }))} />
            </div>
            <div>
              <Label className="mb-1.5 block">Цвет</Label>
              <Input type="color" value={form.color} onChange={(e) => setForm((f) => ({ ...f, color: e.target.value }))} className="h-9 p-1" />
            </div>
          </div>
          <div>
            <Label className="mb-1.5 block">Описание</Label>
            <Textarea rows={3} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
          </div>

          {isSuperuser && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="mb-1.5 block">ID базовой роли Discord</Label>
                  <Input value={form.base_discord_role_id} onChange={(e) => setForm((f) => ({ ...f, base_discord_role_id: e.target.value }))} />
                </div>
                <div>
                  <Label className="mb-1.5 block">Название роли</Label>
                  <Input value={form.base_discord_role_name} onChange={(e) => setForm((f) => ({ ...f, base_discord_role_name: e.target.value }))} />
                </div>
              </div>
              <div>
                <Label className="mb-1.5 block">Модули</Label>
                <div className="grid grid-cols-2 gap-2">
                  {OPTIONAL_MODULE_KEYS.map((mod) => (
                    <label key={mod} className="flex items-center gap-2 rounded-md border px-2.5 py-2 text-sm">
                      <Checkbox checked={form.enabled_modules.includes(mod)} onCheckedChange={(c) => toggleModule(mod, c === true)} />
                      {MODULE_LABELS[mod]}
                    </label>
                  ))}
                </div>
              </div>
              {form.enabled_modules.includes("orders") && (
                <div>
                  <Label className="mb-1.5 block">ID канала Discord для публикации приказов</Label>
                  <Input value={form.orders_channel_id} onChange={(e) => setForm((f) => ({ ...f, orders_channel_id: e.target.value }))} placeholder="123456789012345678" />
                </div>
              )}
              <div>
                <Label className="mb-1.5 block">ID канала Discord для публикации новостей</Label>
                <Input value={form.news_channel_id} onChange={(e) => setForm((f) => ({ ...f, news_channel_id: e.target.value }))} placeholder="123456789012345678" />
              </div>
              {form.enabled_modules.includes("rating") && (
                <div>
                  <Label className="mb-1.5 block">ID канала Discord для публикации рейтинга отдела</Label>
                  <Input value={form.rating_channel_id} onChange={(e) => setForm((f) => ({ ...f, rating_channel_id: e.target.value }))} placeholder="123456789012345678" />
                  <p className="mt-1 text-[11px] text-muted-foreground">Бот публикует и обновляет таблицу рейтинга отдела в этом канале раз в час.</p>
                </div>
              )}
              {form.enabled_modules.includes("test_server") && (
                <div>
                  <Label className="mb-1.5 block">UUID сервера Pterodactyl</Label>
                  <Input value={form.pterodactyl_server_uuid} onChange={(e) => setForm((f) => ({ ...f, pterodactyl_server_uuid: e.target.value }))} placeholder="c1a2b3..." />
                </div>
              )}
              <div>
                <Label className="mb-1.5 block">ID категории Discord отдела</Label>
                <Input value={form.discord_category_id} onChange={(e) => setForm((f) => ({ ...f, discord_category_id: e.target.value }))} placeholder="123456789012345678" />
                <p className="mt-1 text-[11px] text-muted-foreground">Используется, чтобы при публикации документа с вручную указанным каналом проверить, что канал принадлежит этой категории.</p>
              </div>
              <div>
                <Label className="mb-1.5 block">Серверная группа привилегий для главы/зам. главы отдела</Label>
                <Select
                  value={form.head_server_privilege_group_id ? String(form.head_server_privilege_group_id) : "none"}
                  onValueChange={(v) => setForm((f) => ({ ...f, head_server_privilege_group_id: v === "none" ? null : Number(v) }))}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Не назначать</SelectItem>
                    {privGroupsData?.priv_groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                <p className="mt-1 text-[11px] text-muted-foreground">Игрок привязанного главы/зам. главы автоматически получит эту группу — если она не назначена вручную.</p>
              </div>
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={updateMutation.isPending} onClick={() => updateMutation.mutate()}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RequireSteamIdDialog({ open, onOpenChange, onConfirm, pending }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (steamid: string) => void
  pending: boolean
}) {
  const [steamid, setSteamid] = useState("")
  useEffect(() => { if (open) setSteamid("") }, [open])
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Требуется SteamID сотрудника</DialogTitle>
          <DialogDescription>
            У сотрудника нет привязанного игрового профиля. Чтобы выдать уровень доступа выше базового, укажите его SteamID64 — привязка выполнится автоматически.
          </DialogDescription>
        </DialogHeader>
        <div>
          <Label className="mb-1.5 block">SteamID64</Label>
          <Input value={steamid} onChange={(e) => setSteamid(e.target.value)} placeholder="7656119..." />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!steamid.trim() || pending} onClick={() => onConfirm(steamid.trim())}>Привязать и продолжить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AddMemberDialog({ open, onOpenChange, department, isSuperuser }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  department: DepartmentDetail
  isSuperuser: boolean
}) {
  const queryClient = useQueryClient()
  const [discordId, setDiscordId] = useState("")
  const [heldKeys, setHeldKeys] = useState<string[]>(["base"])
  const [isHead, setIsHead] = useState(false)
  const [isDeputyHead, setIsDeputyHead] = useState(false)
  const [autoLinkWarningOpen, setAutoLinkWarningOpen] = useState(false)
  const [steamIdPromptOpen, setSteamIdPromptOpen] = useState(false)

  const addMutation = useMutation({
    mutationFn: (steamid?: string) => departmentsApi.members.add(department.id, {
      discord_id: discordId.trim(), held_level_keys: heldKeys, is_head: isHead, is_deputy_head: isDeputyHead,
      ...(steamid ? { steamid } : {}),
    }),
    onSuccess: (res) => {
      toast.success("Сотрудник добавлен")
      queryClient.invalidateQueries({ queryKey: ["department-members", department.id] })
      queryClient.invalidateQueries({ queryKey: ["department", department.id] })
      onOpenChange(false)
      setSteamIdPromptOpen(false)
      setDiscordId("")
      setHeldKeys(["base"])
      setIsHead(false)
      setIsDeputyHead(false)
      if (res.auto_link === "not_found") setAutoLinkWarningOpen(true)
    },
    onError: (e: Error) => {
      if (e instanceof ApiError && e.status === 428) { setSteamIdPromptOpen(true); return }
      toast.error(e.message)
    },
  })

  const toggleKey = (key: string, checked: boolean) => {
    setHeldKeys((keys) => (checked ? [...keys, key] : keys.filter((k) => k !== key)))
  }

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Добавить сотрудника</DialogTitle>
          <DialogDescription>Если аккаунта ND с таким Discord ID ещё нет, он будет создан автоматически.</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">Discord ID</Label>
            <Input value={discordId} onChange={(e) => setDiscordId(e.target.value)} placeholder="123456789012345678" />
          </div>
          <div>
            <Label className="mb-1.5 block">Уровни доступа</Label>
            <div className="flex flex-col gap-1.5">
              {department.access_levels.map((lvl) => (
                <label key={lvl.key} className="flex items-center gap-2 rounded-md border px-2.5 py-2 text-sm">
                  <Checkbox checked={heldKeys.includes(lvl.key)} onCheckedChange={(c) => toggleKey(lvl.key, c === true)} />
                  {lvl.name} <span className="text-xs text-muted-foreground">({lvl.kind})</span>
                </label>
              ))}
            </div>
          </div>
          {isSuperuser && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={isHead} onCheckedChange={(c) => setIsHead(c === true)} />
              Назначить главой отдела
            </label>
          )}
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={isDeputyHead} onCheckedChange={(c) => setIsDeputyHead(c === true)} />
            Назначить заместителем главы
          </label>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!discordId.trim() || addMutation.isPending} onClick={() => addMutation.mutate(undefined)}>Добавить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <RequireSteamIdDialog
      open={steamIdPromptOpen}
      onOpenChange={setSteamIdPromptOpen}
      pending={addMutation.isPending}
      onConfirm={(sid) => addMutation.mutate(sid)}
    />

    <AlertDialog open={autoLinkWarningOpen} onOpenChange={setAutoLinkWarningOpen}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Сотрудник добавлен, но...</AlertDialogTitle>
          <AlertDialogDescription>
            Похоже мы не смогли найти его игровой профиль. Вашему сотруднику придётся самостоятельно ввести свой SteamID при входе — сообщите ему об этом.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogAction onClick={() => setAutoLinkWarningOpen(false)}>Я сообщу сотруднику</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  )
}

function EditMemberLevelsDialog({ open, onOpenChange, department, member, isSuperuser }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  department: DepartmentDetail
  member: DepartmentMemberRow
  isSuperuser: boolean
}) {
  const queryClient = useQueryClient()
  const [heldKeys, setHeldKeys] = useState<string[]>(member.held_level_keys)
  const [isHead, setIsHead] = useState(member.is_head)
  const [isDeputyHead, setIsDeputyHead] = useState(member.is_deputy_head)
  const [steamIdPromptOpen, setSteamIdPromptOpen] = useState(false)

  useEffect(() => {
    if (open) {
      setHeldKeys(member.held_level_keys)
      setIsHead(member.is_head)
      setIsDeputyHead(member.is_deputy_head)
    }
  }, [open, member])

  const updateMutation = useMutation({
    mutationFn: (steamid?: string) => departmentsApi.members.update(department.id, member.id, {
      held_level_keys: heldKeys, is_head: isHead, is_deputy_head: isDeputyHead,
      ...(steamid ? { steamid } : {}),
    }),
    onSuccess: () => {
      toast.success("Уровни обновлены")
      queryClient.invalidateQueries({ queryKey: ["department-members", department.id] })
      queryClient.invalidateQueries({ queryKey: ["department", department.id] })
      onOpenChange(false)
      setSteamIdPromptOpen(false)
    },
    onError: (e: Error) => {
      if (e instanceof ApiError && e.status === 428) { setSteamIdPromptOpen(true); return }
      toast.error(e.message)
    },
  })

  const toggleKey = (key: string, checked: boolean) => {
    setHeldKeys((keys) => (checked ? [...keys, key] : keys.filter((k) => k !== key)))
  }

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Уровни доступа — {member.forced_display_name ?? member.admin_name ?? `#${member.admin_id}`}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          {department.access_levels.map((lvl) => (
            <label key={lvl.key} className="flex items-center gap-2 rounded-md border px-2.5 py-2 text-sm">
              <Checkbox checked={heldKeys.includes(lvl.key)} onCheckedChange={(c) => toggleKey(lvl.key, c === true)} />
              {lvl.name} <span className="text-xs text-muted-foreground">({lvl.kind})</span>
            </label>
          ))}
          {isSuperuser && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={isHead} onCheckedChange={(c) => setIsHead(c === true)} />
              Глава отдела
            </label>
          )}
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={isDeputyHead} onCheckedChange={(c) => setIsDeputyHead(c === true)} />
            Заместитель главы
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={updateMutation.isPending} onClick={() => updateMutation.mutate(undefined)}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <RequireSteamIdDialog
      open={steamIdPromptOpen}
      onOpenChange={setSteamIdPromptOpen}
      pending={updateMutation.isPending}
      onConfirm={(sid) => updateMutation.mutate(sid)}
    />
    </>
  )
}

function AccessLevelPermissionsDialog({ open, onOpenChange, department, level, isSuperuser }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  department: DepartmentDetail
  level: DepartmentAccessLevel
  isSuperuser: boolean
}) {
  const queryClient = useQueryClient()
  const { data: registryData } = usePermissionRegistry()
  const [perms, setPerms] = useState<Record<string, { view: boolean; edit: boolean }>>({})
  const [sitePerms, setSitePerms] = useState<PermissionValue>({})
  const [kitBindingKey, setKitBindingKey] = useState<string>("none")
  const [serverPrivGroupId, setServerPrivGroupId] = useState<number | null>(null)

  const { data: kitsData } = useQuery({
    queryKey: ["kits"],
    queryFn: () => api.get<{ qual_kits: { id: number; job_id: string; kit_level: number }[] }>("/api/v1/admin/kits"),
    enabled: isSuperuser,
  })

  const { data: privGroupsData } = useQuery({
    queryKey: ["server-privileges-groups-picker"],
    queryFn: () => api.get<{ priv_groups: { id: number; name: string }[] }>("/api/v1/admin/server-privileges"),
    enabled: isSuperuser && open,
  })

  useEffect(() => {
    if (!open) return
    const pages = level.permissions?.pages ?? {}
    const next: Record<string, { view: boolean; edit: boolean }> = {}
    for (const mod of MODULE_KEYS) next[mod] = { view: !!pages[mod]?.view, edit: !!pages[mod]?.edit }
    setPerms(next)
    setSitePerms((level.site_permissions?.pages as PermissionValue) ?? {})
    setKitBindingKey(level.kit_binding ? `${level.kit_binding.job_id}::${level.kit_binding.kit_level}` : "none")
    setServerPrivGroupId(level.server_privilege_group_id)
  }, [open, level])

  const updateMutation = useMutation({
    mutationFn: () => {
      let kitBinding: { job_id: string; kit_level: number } | null | undefined = undefined
      if (isSuperuser) {
        if (kitBindingKey === "none") {
          kitBinding = null
        } else {
          const [jobId, levelStr] = kitBindingKey.split("::")
          kitBinding = { job_id: jobId, kit_level: Number(levelStr) }
        }
      }
      return departmentsApi.accessLevels.update(department.id, level.id, {
        permissions: { pages: perms },
        ...(isSuperuser ? { site_permissions: { pages: sitePerms }, kit_binding: kitBinding, server_privilege_group_id: serverPrivGroupId } : {}),
      })
    },
    onSuccess: () => {
      toast.success("Права обновлены")
      queryClient.invalidateQueries({ queryKey: ["department", department.id] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Права уровня «{level.name}»</DialogTitle>
          <DialogDescription>Какие вкладки отдела доступны сотрудникам с этим уровнем.</DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          {MODULE_KEYS.map((mod) => (
            <div key={mod} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
              <span>{MODULE_LABELS[mod]}</span>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Checkbox
                    checked={perms[mod]?.view ?? false}
                    onCheckedChange={(c) => setPerms((p) => ({ ...p, [mod]: { view: c === true, edit: p[mod]?.edit ?? false } }))}
                  /> Просмотр
                </label>
                {mod === "news" ? (
                  <span className="text-xs text-muted-foreground/60" title="Публикация новостей доступна только главе отдела или суперпользователю">
                    Изменение — только у главы
                  </span>
                ) : (
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Checkbox
                      checked={perms[mod]?.edit ?? false}
                      onCheckedChange={(c) => setPerms((p) => ({ ...p, [mod]: { view: p[mod]?.view ?? false, edit: c === true } }))}
                    /> Изменение
                  </label>
                )}
              </div>
            </div>
          ))}
        </div>

        {isSuperuser && registryData && (
          <div className="space-y-1.5 border-t pt-3">
            <div className="text-xs font-semibold text-muted-foreground">
              Дополнительные права на весь сайт (только для суперпользователя)
            </div>
            <p className="text-xs text-muted-foreground">
              Выдаются в ДОПОЛНЕНИЕ к правам из «Пользователи ND» всем, кто держит этот уровень доступа.
            </p>
            <PermissionMatrixEditor registry={registryData.pages} value={sitePerms} onChange={setSitePerms} />
          </div>
        )}

        {isSuperuser && (
          <div className="space-y-1.5 border-t pt-3">
            <div className="text-xs font-semibold text-muted-foreground">
              Привязанный квал-кит (только для суперпользователя)
            </div>
            <p className="text-xs text-muted-foreground">
              Фоновый воркер автоматически выдаёт/снимает этот квал-кит у игрока, привязанного к сотруднику, пока он держит этот уровень доступа.
            </p>
            <Select value={kitBindingKey} onValueChange={setKitBindingKey}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Не привязан</SelectItem>
                {kitsData?.qual_kits.map((k) => (
                  <SelectItem key={k.id} value={`${k.job_id}::${k.kit_level}`}>{k.job_id} (ур. {k.kit_level})</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {isSuperuser && (
          <div className="space-y-1.5 border-t pt-3">
            <div className="text-xs font-semibold text-muted-foreground">
              Серверная группа привилегий (только для суперпользователя)
            </div>
            <p className="text-xs text-muted-foreground">
              Игрок, привязанный к сотруднику, держащему этот уровень, автоматически получит эту группу — если она не назначена вручную.
            </p>
            <Select
              value={serverPrivGroupId ? String(serverPrivGroupId) : "none"}
              onValueChange={(v) => setServerPrivGroupId(v === "none" ? null : Number(v))}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Не назначать</SelectItem>
                {privGroupsData?.priv_groups.map((g) => <SelectItem key={g.id} value={String(g.id)}>{g.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={updateMutation.isPending} onClick={() => updateMutation.mutate()}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CreateAccessLevelDialog({ open, onOpenChange, department, isSuperuser }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  department: DepartmentDetail
  isSuperuser: boolean
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState("")
  const [kind, setKind] = useState<"qualification" | "head" | "deputy_head">("qualification")
  const [roleId, setRoleId] = useState("")

  const createMutation = useMutation({
    mutationFn: () => departmentsApi.accessLevels.create(department.id, { name: name.trim(), kind: isSuperuser ? kind : "qualification", discord_role_id: roleId.trim() || undefined }),
    onSuccess: () => {
      toast.success("Уровень доступа создан")
      queryClient.invalidateQueries({ queryKey: ["department", department.id] })
      onOpenChange(false)
      setName("")
      setRoleId("")
      setKind("qualification")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Новый уровень доступа</DialogTitle>
          <DialogDescription>
            Квалификация даёт доступ дополнительно к базовой роли.
            {isSuperuser ? " «Глава»/«Заместитель главы» дают полный доступ к отделу." : " Главу и заместителя главы отдела может назначить только суперпользователь."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">Название</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Врач-специалист" />
          </div>
          {isSuperuser && (
            <div>
              <Label className="mb-1.5 block">Тип</Label>
              <Select value={kind} onValueChange={(v) => setKind(v as "qualification" | "head" | "deputy_head")}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="qualification">Квалификация</SelectItem>
                  <SelectItem value="head">Глава отдела</SelectItem>
                  <SelectItem value="deputy_head">Заместитель главы отдела</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          <div>
            <Label className="mb-1.5 block">ID роли Discord <span className="font-normal text-muted-foreground">(необязательно)</span></Label>
            <Input value={roleId} onChange={(e) => setRoleId(e.target.value)} placeholder="123456789012345678 — оставьте пустым для ручной выдачи" />
            <p className="mt-1 text-[11px] text-muted-foreground">Без роли уровень выдаётся только вручную через «Участники» и не синхронизируется с Discord.</p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!name.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>Создать</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditAccessLevelDialog({ open, onOpenChange, department, level, isSuperuser }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  department: DepartmentDetail
  level: DepartmentAccessLevel
  isSuperuser: boolean
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(level.name)
  const [roleId, setRoleId] = useState(level.discord_role_id ?? "")
  const [kind, setKind] = useState<DepartmentAccessLevel["kind"]>(level.kind)

  useEffect(() => {
    if (open) {
      setName(level.name)
      setRoleId(level.discord_role_id ?? "")
      setKind(level.kind)
    }
  }, [open, level])

  const updateMutation = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {}
      if (name.trim() !== level.name) body.name = name.trim()
      const normalizedRole = roleId.trim() || ""
      const currentRole = level.discord_role_id ?? ""
      if (normalizedRole !== currentRole) body.discord_role_id = normalizedRole || null
      if (isSuperuser && kind !== level.kind) body.kind = kind
      if (Object.keys(body).length === 0) return Promise.resolve({ status: "ok" } as unknown as { status: string; access_level: DepartmentAccessLevel })
      return departmentsApi.accessLevels.update(department.id, level.id, body as never)
    },
    onSuccess: () => {
      toast.success("Уровень доступа обновлён")
      queryClient.invalidateQueries({ queryKey: ["department", department.id] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const isBase = level.kind === "base"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Редактировать уровень «{level.name}»</DialogTitle>
          <DialogDescription>Можно изменить название, Discord-роль и тип уровня доступа.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">Название</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Название уровня" />
          </div>
          <div>
            <Label className="mb-1.5 block">ID роли Discord <span className="font-normal text-muted-foreground">(необязательно)</span></Label>
            <Input value={roleId} onChange={(e) => setRoleId(e.target.value)} placeholder="123456789012345678 — пусто = ручной уровень без синхронизации" />
            <p className="mt-1 text-[11px] text-muted-foreground">Без роли уровень выдаётся только вручную и не синхронизируется с Discord.</p>
          </div>
          {isSuperuser && (
            <div>
              <Label className="mb-1.5 block">Тип</Label>
              {isBase ? (
                <p className="rounded-md border bg-muted px-3 py-2 text-sm text-muted-foreground">Базовый уровень — тип изменить нельзя</p>
              ) : (
                <Select value={kind} onValueChange={(v) => setKind(v as DepartmentAccessLevel["kind"])}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="qualification">Квалификация</SelectItem>
                    <SelectItem value="head">Глава отдела</SelectItem>
                    <SelectItem value="deputy_head">Заместитель главы отдела</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </div>
          )}
          {!isSuperuser && <p className="text-xs text-muted-foreground">Тип уровня может изменить только суперпользователь.</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!name.trim() || updateMutation.isPending} onClick={() => updateMutation.mutate()}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AddReprimandDialog({ open, onOpenChange, department, member }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  department: DepartmentDetail
  member: DepartmentMemberRow | null
}) {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState("")

  const addMutation = useMutation({
    mutationFn: () => departmentsApi.reprimands.create(department.id, { admin_id: member!.admin_id, reason: reason.trim() }),
    onSuccess: () => {
      toast.success("Выговор выдан")
      queryClient.invalidateQueries({ queryKey: ["department-reprimands", department.id] })
      onOpenChange(false)
      setReason("")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Выговор — {member?.forced_display_name ?? member?.admin_name ?? `#${member?.admin_id}`}</DialogTitle>
          <DialogDescription>Только глава отдела может выдавать выговоры.</DialogDescription>
        </DialogHeader>
        <Textarea rows={4} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Причина выговора" />
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={!reason.trim() || addMutation.isPending} onClick={() => addMutation.mutate()}>Выдать</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RosterTab({ department, canEdit, isSuperuser }: { department: DepartmentDetail; canEdit: boolean; isSuperuser: boolean }) {
  const queryClient = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<DepartmentMemberRow | null>(null)
  const [removeTarget, setRemoveTarget] = useState<DepartmentMemberRow | null>(null)
  const [ratingTarget, setRatingTarget] = useState<DepartmentMemberRow | null>(null)
  const [reprimandTarget, setReprimandTarget] = useState<DepartmentMemberRow | null>(null)
  const [linkSteamIdTarget, setLinkSteamIdTarget] = useState<DepartmentMemberRow | null>(null)
  const canView = department.my_access.roster?.view
  const canRate = !!department.my_access.rating?.edit
  const isHead = isSuperuser || department.is_head

  const { data, isLoading } = useQuery({
    queryKey: ["department-members", department.id],
    queryFn: () => departmentsApi.members.list(department.id),
    enabled: canView,
  })

  const linkSteamIdMutation = useMutation({
    mutationFn: ({ member, steamid }: { member: DepartmentMemberRow; steamid: string }) =>
      departmentsApi.members.update(department.id, member.id, {
        held_level_keys: member.held_level_keys, is_head: member.is_head, is_deputy_head: member.is_deputy_head, steamid,
      }),
    onSuccess: () => {
      toast.success("SteamID привязан")
      queryClient.invalidateQueries({ queryKey: ["department-members", department.id] })
      setLinkSteamIdTarget(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const removeMutation = useMutation({
    mutationFn: (memberId: number) => departmentsApi.members.remove(department.id, memberId),
    onSuccess: () => {
      toast.success("Сотрудник удалён из отдела")
      queryClient.invalidateQueries({ queryKey: ["department-members", department.id] })
      queryClient.invalidateQueries({ queryKey: ["department", department.id] })
      setRemoveTarget(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!canView) {
    return <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">Недостаточно прав для просмотра состава отдела.</CardContent></Card>
  }

  const levelName = (key: string) => department.access_levels.find((l) => l.key === key)?.name ?? key

  return (
    <div className="space-y-3">
      {canEdit && (
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setAddOpen(true)}><UserPlus className="size-4" /> Добавить сотрудника</Button>
        </div>
      )}
      {isLoading ? (
        <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
      ) : !data?.members.length ? (
        <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">В отделе пока никого нет.</CardContent></Card>
      ) : (
        <div className="flex flex-col gap-2">
          {data.members.map((m) => {
            const needsSteamId = !m.has_linked_profile && (m.held_level_keys.some((k) => k !== "base") || m.is_head || m.is_deputy_head)
            return (
            <Card key={m.id} className={`overflow-hidden py-0 ${needsSteamId ? "border-warning/50 bg-warning/10" : ""}`}>
              <CardContent className="flex flex-wrap items-center justify-between gap-2 p-3">
                <div className="flex items-center gap-2">
                  <span className="font-medium" title={m.forced_display_name ? `Discord: ${m.admin_name ?? m.admin_id}` : undefined}>
                    {m.forced_display_name ?? m.admin_name ?? `#${m.admin_id}`}
                  </span>
                  {m.is_head && <Badge className="gap-1 bg-primary/15 text-primary"><Crown className="size-3" /> Глава</Badge>}
                  {m.is_deputy_head && <Badge className="gap-1 bg-primary/10 text-primary"><Crown className="size-3" /> Зам. главы</Badge>}
                  {m.is_auto && <Badge variant="outline" className="text-[10px]">авто</Badge>}
                  {m.created_by_worker && <Badge variant="outline" className="text-[10px]">аккаунт создан авто</Badge>}
                  {needsSteamId && (
                    <Badge className="gap-1 bg-warning/20 text-warning"><AlertTriangle className="size-3" /> Нет игрового профиля</Badge>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {m.held_level_keys.map((k) => <Badge key={k} variant="secondary" className="text-[10px]">{levelName(k)}</Badge>)}
                  {needsSteamId && canEdit && (
                    <Button size="sm" variant="outline" className="gap-1 text-warning" onClick={() => setLinkSteamIdTarget(m)}>
                      <KeyRound className="size-3.5" /> Указать SteamID
                    </Button>
                  )}
                  {canRate && (
                    <Button size="icon-xs" variant="ghost" onClick={() => setRatingTarget(m)} title="Изменить рейтинг">
                      <Star className="size-3.5" />
                    </Button>
                  )}
                  {isHead && (
                    <Button size="icon-xs" variant="ghost" className="text-warning" onClick={() => setReprimandTarget(m)} title="Выдать выговор">
                      <ShieldAlert className="size-3.5" />
                    </Button>
                  )}
                  {canEdit && (
                    <>
                      <Button size="icon-xs" variant="ghost" onClick={() => setEditTarget(m)} title="Изменить уровни">
                        <Pencil className="size-3.5" />
                      </Button>
                      <Button size="icon-xs" variant="ghost" className="text-destructive" onClick={() => setRemoveTarget(m)} title="Удалить из отдела">
                        <Trash2 className="size-3.5" />
                      </Button>
                    </>
                  )}
                </div>
              </CardContent>
            </Card>
            )
          })}
        </div>
      )}

      <RequireSteamIdDialog
        open={!!linkSteamIdTarget}
        onOpenChange={(o) => !o && setLinkSteamIdTarget(null)}
        pending={linkSteamIdMutation.isPending}
        onConfirm={(sid) => linkSteamIdTarget && linkSteamIdMutation.mutate({ member: linkSteamIdTarget, steamid: sid })}
      />

      <AddMemberDialog open={addOpen} onOpenChange={setAddOpen} department={department} isSuperuser={isSuperuser} />
      {editTarget && (
        <EditMemberLevelsDialog
          open={!!editTarget}
          onOpenChange={(o) => !o && setEditTarget(null)}
          department={department}
          member={editTarget}
          isSuperuser={isSuperuser}
        />
      )}
      <ConfirmDialog
        open={!!removeTarget}
        onOpenChange={(o) => !o && setRemoveTarget(null)}
        title="Удалить сотрудника из отдела?"
        description={removeTarget?.forced_display_name ?? removeTarget?.admin_name ?? undefined}
        onConfirm={() => removeTarget && removeMutation.mutate(removeTarget.id)}
      />
      {ratingTarget && (
        <AddRatingEntryDialog
          open={!!ratingTarget}
          onOpenChange={(o) => !o && setRatingTarget(null)}
          department={department}
          presetMember={{ admin_id: ratingTarget.admin_id, admin_name: ratingTarget.forced_display_name ?? ratingTarget.admin_name }}
        />
      )}
      <AddReprimandDialog
        open={!!reprimandTarget}
        onOpenChange={(o) => !o && setReprimandTarget(null)}
        department={department}
        member={reprimandTarget}
      />
    </div>
  )
}

export function DepartmentDetailPage() {
  const { departmentId } = useParams<{ departmentId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { data: admin } = useCurrentAdmin()
  const [editOpen, setEditOpen] = useState(false)
  const [createLevelOpen, setCreateLevelOpen] = useState(false)
  const [permsTarget, setPermsTarget] = useState<DepartmentAccessLevel | null>(null)
  const [editLevelTarget, setEditLevelTarget] = useState<DepartmentAccessLevel | null>(null)
  const [deleteLevelTarget, setDeleteLevelTarget] = useState<DepartmentAccessLevel | null>(null)
  // Restores whichever tab a document/link sent us back to (see
  // OnlyOfficeEditorPage's "returnTo" navigation state) instead of always
  // resetting to "Обзор".
  const [activeTab, setActiveTab] = useState(() => (location.state as { tab?: string } | null)?.tab || "overview")

  const { data, isLoading, isError } = useQuery({
    queryKey: ["department", Number(departmentId)],
    queryFn: () => departmentsApi.get(Number(departmentId)),
    enabled: !!departmentId,
    retry: false,
  })

  const canViewRoster = !!data?.my_access.roster?.view
  const { data: membersData } = useQuery({
    queryKey: ["department-members", Number(departmentId)],
    queryFn: () => departmentsApi.members.list(Number(departmentId)),
    enabled: !!departmentId && canViewRoster,
  })
  const unlinkedElevated = (membersData?.members ?? []).filter(
    (m) => !m.has_linked_profile && (m.held_level_keys.some((k) => k !== "base") || m.is_head || m.is_deputy_head)
  )

  const deleteLevelMutation = useMutation({
    mutationFn: (levelId: number) => departmentsApi.accessLevels.delete(Number(departmentId), levelId),
    onSuccess: () => {
      toast.success("Уровень удалён")
      queryClient.invalidateQueries({ queryKey: ["department", Number(departmentId)] })
      setDeleteLevelTarget(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (isError) {
    return (
      <div className="space-y-3">
        <Button variant="ghost" onClick={() => navigate("/departments")}><ArrowLeft className="size-4" /> К списку отделов</Button>
        <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">Отдел не найден, или вы не состоите в нём.</CardContent></Card>
      </div>
    )
  }

  if (isLoading || !data) {
    return <div className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-64" /></div>
  }

  const isSuperuser = !!admin?.is_superuser
  const canManage = isSuperuser || data.is_head || data.is_deputy_head
  const visibleModules = data.enabled_modules.filter((m) => m !== "roster" && m !== "news")

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={() => navigate("/departments")}><ArrowLeft className="size-4" /></Button>
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-xl">
            <PlatformIcon value={data.icon} fallback={Building2} className="size-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight">{data.name}</h1>
            {data.description && <p className="text-sm text-muted-foreground">{data.description}</p>}
          </div>
        </div>
        {canManage && (
          <Button variant="outline" onClick={() => setEditOpen(true)}><Settings className="size-4" /> Настройки</Button>
        )}
      </div>

      {unlinkedElevated.length > 0 && (
        <Card className="mb-4 border-warning/40 bg-warning/10">
          <CardContent className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
            <div className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-warning" />
              <span>
                {unlinkedElevated.length === 1
                  ? "У одного сотрудника есть уровень доступа выше базового, но не привязан игровой профиль."
                  : `У ${unlinkedElevated.length} сотрудников есть уровень доступа выше базового, но не привязан игровой профиль.`}
                {" "}Актуализируйте информацию на странице участников.
              </span>
            </div>
            <Button size="sm" variant="outline" onClick={() => setActiveTab("roster")}>К участникам</Button>
          </CardContent>
        </Card>
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview">Обзор</TabsTrigger>
          <TabsTrigger value="roster">Участники</TabsTrigger>
          <TabsTrigger value="news">Новости</TabsTrigger>
          {visibleModules.map((mod) => <TabsTrigger key={mod} value={mod}>{MODULE_LABELS[mod]}</TabsTrigger>)}
        </TabsList>

        <TabsContent value="overview" className="space-y-3">
          <Card>
            <CardContent className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              <div>
                <div className="text-xs text-muted-foreground">Сотрудников</div>
                <div className="text-lg font-semibold">{data.member_count}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Глава отдела</div>
                <div className="text-lg font-semibold">{data.head?.forced_display_name ?? data.head?.admin_name ?? "Не назначен"}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Базовая роль Discord</div>
                <div className="text-sm">{data.base_discord_role_name ?? "-"} {data.base_discord_role_id && <span className="font-mono text-xs text-muted-foreground">({data.base_discord_role_id})</span>}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Модули</div>
                <div className="flex flex-wrap gap-1.5">
                  {visibleModules.length ? visibleModules.map((m) => <Badge key={m} variant="secondary">{MODULE_LABELS[m]}</Badge>) : <span className="text-sm text-muted-foreground">Не подключены</span>}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-2 p-4">
              <div className="flex items-center justify-between">
                <div className="text-xs font-semibold text-muted-foreground">Уровни доступа</div>
                {canManage && (
                  <Button size="xs" variant="outline" onClick={() => setCreateLevelOpen(true)}><Plus className="size-3.5" /> Уровень</Button>
                )}
              </div>
              {data.access_levels.length ? (() => {
                const withRole = data.access_levels.filter((l) => !!l.discord_role_id)
                const manual = data.access_levels.filter((l) => !l.discord_role_id)
                const renderRow = (l: DepartmentAccessLevel, manualOnly: boolean) => (
                  <div key={l.id} className={`flex flex-wrap items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-sm ${manualOnly ? "border-dashed border-warning/30 bg-warning/5" : ""}`}>
                    <span className="flex items-center gap-1.5">
                      {(l.kind === "head" || l.kind === "deputy_head") && <Crown className="size-3.5 text-primary" />}
                      {l.name}
                      <Badge variant="secondary" className="text-[10px]">{l.kind}</Badge>
                      {l.discord_role_id ? (
                        <span className="font-mono text-xs text-muted-foreground">{l.discord_role_id}</span>
                      ) : (
                        <Badge variant="outline" className="border-warning/30 bg-warning/10 text-[10px] text-warning" title="Без Discord-роли — выдаётся только вручную">ручной · без роли</Badge>
                      )}
                    </span>
                    {canManage && (
                      <div className="flex items-center gap-1">
                        <Button size="icon-xs" variant="ghost" onClick={() => setEditLevelTarget(l)} title="Переименовать / сменить роль и тип">
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button size="icon-xs" variant="ghost" onClick={() => setPermsTarget(l)} title="Настроить права">
                          <Settings2 className="size-3.5" />
                        </Button>
                        {isSuperuser && l.kind !== "base" && (
                          <Button size="icon-xs" variant="ghost" className="text-destructive" onClick={() => setDeleteLevelTarget(l)} title="Удалить уровень">
                            <Trash2 className="size-3.5" />
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                )
                return (
                <div className="space-y-3">
                  {withRole.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-[11px] font-medium text-muted-foreground">С привязкой к Discord-роли · синхронизируются автоматически</div>
                      <div className="flex flex-col gap-1.5">{withRole.map((l) => renderRow(l, false))}</div>
                    </div>
                  )}
                  {manual.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-[11px] font-medium text-warning">Ручные · без Discord-роли — выдаются только вручную</div>
                      <div className="flex flex-col gap-1.5">{manual.map((l) => renderRow(l, true))}</div>
                    </div>
                  )}
                </div>
                )
              })() : (
                <p className="text-sm text-muted-foreground">Уровней пока нет.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="roster">
          <RosterTab department={data} canEdit={canManage} isSuperuser={isSuperuser} />
        </TabsContent>

        <TabsContent value="news">
          <NewsTab department={data} canEdit={data.my_access.news?.edit ?? false} />
        </TabsContent>

        {visibleModules.includes("documentation") && (
          <TabsContent value="documentation">
            <DocumentationTab department={data} canEdit={data.my_access.documentation?.edit ?? false} isSuperuser={isSuperuser} />
          </TabsContent>
        )}

        {visibleModules.includes("rating") && (
          <TabsContent value="rating">
            <RatingTab department={data} canEdit={data.my_access.rating?.edit ?? false} />
          </TabsContent>
        )}

        {visibleModules.includes("orders") && (
          <TabsContent value="orders">
            <OrdersTab department={data} canEdit={data.my_access.orders?.edit ?? false} />
          </TabsContent>
        )}

        {visibleModules.includes("test_server") && (
          <TabsContent value="test_server">
            <TestServerTab department={data} canEdit={data.my_access.test_server?.edit ?? false} />
          </TabsContent>
        )}

        {visibleModules.includes("chat") && (
          <TabsContent value="chat">
            <ChatTab department={data} canEdit={data.my_access.chat?.edit ?? false} isSuperuser={isSuperuser} />
          </TabsContent>
        )}

        {visibleModules.filter((m) => !["documentation", "rating", "orders", "test_server", "chat"].includes(m)).map((mod) => (
          <TabsContent key={mod} value={mod}>
            <Card>
              <CardContent className="flex flex-col items-center gap-2 py-12 text-center text-sm text-muted-foreground">
                <Plus className="size-6 opacity-40" />
                Модуль «{MODULE_LABELS[mod]}» появится в одном из следующих обновлений.
              </CardContent>
            </Card>
          </TabsContent>
        ))}
      </Tabs>

      {canManage && <EditDepartmentDialog open={editOpen} onOpenChange={setEditOpen} department={data} isSuperuser={isSuperuser} />}
      {canManage && <CreateAccessLevelDialog open={createLevelOpen} onOpenChange={setCreateLevelOpen} department={data} isSuperuser={isSuperuser} />}
      {editLevelTarget && (
        <EditAccessLevelDialog
          open={!!editLevelTarget}
          onOpenChange={(o) => !o && setEditLevelTarget(null)}
          department={data}
          level={editLevelTarget}
          isSuperuser={isSuperuser}
        />
      )}
      {permsTarget && (
        <AccessLevelPermissionsDialog
          open={!!permsTarget}
          onOpenChange={(o) => !o && setPermsTarget(null)}
          department={data}
          level={permsTarget}
          isSuperuser={isSuperuser}
        />
      )}
      <ConfirmDialog
        open={!!deleteLevelTarget}
        onOpenChange={(o) => !o && setDeleteLevelTarget(null)}
        title="Удалить уровень доступа?"
        description={deleteLevelTarget?.name}
        onConfirm={() => deleteLevelTarget && deleteLevelMutation.mutate(deleteLevelTarget.id)}
      />
    </div>
  )
}
