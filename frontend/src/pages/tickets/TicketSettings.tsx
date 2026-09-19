import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ArrowDown, ArrowUp, Check, GripVertical, Hash, Link2, Save, Send, ShieldCheck, Trash2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"

type RoleRule = { role_id: string; access: boolean; notify: boolean }
type TicketType = {
  name: string; emoji: string; color: string; template: string
  allow_cooldown?: boolean; role_rules: RoleRule[]; ping_roles?: string[]
}
type TicketButton = {
  ticket_type: string; label: string; emoji: string; style: "primary" | "secondary" | "success" | "danger"
  enabled: boolean; allowed_role_ids: string[]
}
type Placement = { channel_id: string; kind: string; button_ids: string[] }
export type TicketConfig = {
  schema_version?: number; category_id: string; status_channel_id: string; public_panel_channel_id?: string
  manager_role_ids: string[]; types: Record<string, TicketType>; buttons: Record<string, TicketButton>
  placements: Record<string, Placement>
}
type PanelState = Record<string, { channel_id: string; message_id: string | null; published: boolean; button_ids: string[] }>
type SettingsResponse = { config: TicketConfig; can_manage_config: boolean; panel_state: PanelState }
type Role = { id: string; name: string; color: string | null; position: number; managed: boolean }

const ROLE_IDS = new Set(["1535306271568306216", "1536021581258821642", "1543720354520899817"])

function move<T>(items: T[], from: number, to: number): T[] {
  if (from < 0 || to < 0 || from >= items.length || to >= items.length) return items
  const next = [...items]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

function RoleRulesEditor({ value, roles, disabled, onChange }: { value: RoleRule[]; roles: Role[]; disabled: boolean; onChange: (value: RoleRule[]) => void }) {
  const [search, setSearch] = useState("")
  const selected = new Set(value.map((rule) => rule.role_id))
  const visible = roles.filter((role) => {
    const needle = search.trim().toLocaleLowerCase()
    return !needle || role.name.toLocaleLowerCase().includes(needle) || role.id.includes(needle)
  })
  const add = (role: Role) => {
    if (selected.has(role.id)) return
    onChange([...value, { role_id: role.id, access: true, notify: true }])
  }
  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/15 p-3">
      <div>
        <p className="text-xs font-medium">Роли тикета</p>
        <p className="text-[11px] text-muted-foreground">Доступ открывает канал. Уведомление добавляет роль в упоминание. Порядок задаётся списком.</p>
      </div>
      <Input value={search} disabled={disabled} onChange={(event) => setSearch(event.target.value)} placeholder="Добавить роль по имени или ID…" className="h-8 text-xs" />
      {value.length > 0 && (
        <div className="space-y-1">
          {value.map((rule, index) => {
            const role = roles.find((item) => item.id === rule.role_id)
            return (
              <div key={rule.role_id} draggable={!disabled} onDragStart={(event) => event.dataTransfer.setData("text/plain", String(index))} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { const from = Number(event.dataTransfer.getData("text/plain")); if (Number.isInteger(from)) onChange(move(value, from, index)) }} className="flex flex-wrap items-center gap-2 rounded border bg-background px-2 py-1.5 text-xs">
                <GripVertical className="size-3.5 cursor-grab text-muted-foreground" aria-label="Перетащить роль" />
                <span className="min-w-0 flex-1 truncate">{role?.name ?? "Неизвестная роль"}</span>
                <span className="font-mono text-[10px] text-muted-foreground">{rule.role_id}</span>
                <label className="flex items-center gap-1"><Checkbox checked={rule.access} disabled={disabled} onCheckedChange={(checked) => onChange(value.map((item) => item.role_id === rule.role_id ? { ...item, access: checked === true } : item))} /> Доступ</label>
                <label className="flex items-center gap-1"><Checkbox checked={rule.notify} disabled={disabled} onCheckedChange={(checked) => onChange(value.map((item) => item.role_id === rule.role_id ? { ...item, notify: checked === true } : item))} /> Пинг</label>
                {!disabled && <Button type="button" size="icon" variant="ghost" className="size-7" onClick={() => onChange(value.filter((item) => item.role_id !== rule.role_id))} aria-label="Убрать роль"><Trash2 className="size-3.5" /></Button>}
              </div>
            )
          })}
        </div>
      )}
      {!disabled && <div className="max-h-36 overflow-y-auto rounded border border-border/60 bg-background/60 p-1">
        {visible.filter((role) => !selected.has(role.id)).slice(0, 80).map((role) => (
          <button key={role.id} type="button" className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-muted" onClick={() => add(role)}>
            <span className="size-2.5 rounded-full" style={{ background: role.color ?? "#99aab5" }} />
            <span className="min-w-0 flex-1 truncate">{role.name}</span>
            <span className="font-mono text-[10px] text-muted-foreground">{role.id}</span>
          </button>
        ))}
        {!visible.filter((role) => !selected.has(role.id)).length && <p className="p-2 text-xs text-muted-foreground">Новых ролей не найдено.</p>}
      </div>}
    </div>
  )
}

function AllowedRolesEditor({ value, roles, disabled, onChange }: { value: string[]; roles: Role[]; disabled: boolean; onChange: (value: string[]) => void }) {
  const selected = new Set(value)
  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/15 p-3">
      <div><p className="text-xs font-medium">Кто может нажать</p><p className="text-[11px] text-muted-foreground">Пустой список означает доступ всем участникам.</p></div>
      <div className="max-h-44 overflow-y-auto rounded border border-border/60 bg-background/60 p-1">
        {roles.filter((role) => !role.managed).map((role) => (
          <label key={role.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs hover:bg-muted">
            <Checkbox checked={selected.has(role.id)} disabled={disabled} onCheckedChange={(checked) => onChange(checked === true ? [...value, role.id] : value.filter((id) => id !== role.id))} />
            <span className="min-w-0 flex-1 truncate">{role.name}</span><span className="font-mono text-[10px] text-muted-foreground">{role.id}</span>
          </label>
        ))}
      </div>
      {value.filter((id) => !roles.some((role) => role.id === id)).map((id) => <p key={id} className="text-[10px] text-amber-300">Сохранённая роль не найдена: {id}</p>)}
    </div>
  )
}

function ButtonOrder({ placement, buttons, disabled, onChange }: { placement: Placement; buttons: Record<string, TicketButton>; disabled: boolean; onChange: (ids: string[]) => void }) {
  const [dragId, setDragId] = useState<string | null>(null)
  const reorder = (target: string) => {
    if (!dragId || dragId === target) return
    const next = [...placement.button_ids]
    const from = next.indexOf(dragId), to = next.indexOf(target)
    onChange(move(next, from, to)); setDragId(null)
  }
  return <div className="space-y-1">
    {placement.button_ids.map((id, index) => {
      const button = buttons[id]
      if (!button) return null
      return <div key={id} draggable={!disabled} onDragStart={() => setDragId(id)} onDragOver={(event) => event.preventDefault()} onDrop={() => reorder(id)} className={`flex items-center gap-2 rounded border px-2 py-1.5 text-xs ${dragId === id ? "opacity-50" : ""}`}>
        <GripVertical className="size-3.5 cursor-grab text-muted-foreground" /><span className="font-mono text-[10px] text-muted-foreground">{index + 1}</span><span className="truncate">{button.emoji} {button.label}</span><Badge variant={button.enabled ? "secondary" : "outline"} className="ml-auto">{button.enabled ? "включена" : "выключена"}</Badge>
        {!disabled && <><Button type="button" size="icon" variant="ghost" className="size-7" disabled={index === 0} onClick={() => onChange(move(placement.button_ids, index, index - 1))}><ArrowUp className="size-3.5" /></Button><Button type="button" size="icon" variant="ghost" className="size-7" disabled={index === placement.button_ids.length - 1} onClick={() => onChange(move(placement.button_ids, index, index + 1))}><ArrowDown className="size-3.5" /></Button></>}
      </div>
    })}
  </div>
}

function TicketButtonEditor({ buttonId, config, roles, disabled, onChange }: { buttonId: string; config: TicketConfig; roles: Role[]; disabled: boolean; onChange: (config: TicketConfig) => void }) {
  const button = config.buttons[buttonId]
  const type = config.types[button.ticket_type]
  const updateButton = (patch: Partial<TicketButton>) => onChange({ ...config, buttons: { ...config.buttons, [buttonId]: { ...button, ...patch } } })
  const updateType = (patch: Partial<TicketType>) => onChange({ ...config, types: { ...config.types, [button.ticket_type]: { ...type, ...patch } } })
  return <Card className="border-primary/25">
    <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0"><CardTitle className="text-sm">{button.emoji} {button.label}</CardTitle><Switch checked={button.enabled} disabled={disabled} onCheckedChange={(checked) => updateButton({ enabled: checked })} aria-label="Включить кнопку" /></CardHeader>
    <CardContent className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div><Label className="mb-1 block text-xs text-muted-foreground">Подпись</Label><Input value={button.label} disabled={disabled} onChange={(event) => updateButton({ label: event.target.value })} /></div>
        <div><Label className="mb-1 block text-xs text-muted-foreground">Emoji</Label><Input value={button.emoji} disabled={disabled} onChange={(event) => updateButton({ emoji: event.target.value })} /></div>
        <div><Label className="mb-1 block text-xs text-muted-foreground">Стиль Discord</Label><select value={button.style} disabled={disabled} onChange={(event) => updateButton({ style: event.target.value as TicketButton["style"] })} className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"><option value="primary">Синий</option><option value="secondary">Серый</option><option value="success">Зелёный</option><option value="danger">Красный</option></select></div>
        <div><Label className="mb-1 block text-xs text-muted-foreground">Тип обращения</Label><select value={button.ticket_type} disabled={disabled} onChange={(event) => updateButton({ ticket_type: event.target.value })} className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm">{Object.entries(config.types).map(([id, item]) => <option key={id} value={id}>{item.emoji} {item.name} ({id})</option>)}</select></div>
      </div>
      <AllowedRolesEditor value={button.allowed_role_ids} roles={roles} disabled={disabled} onChange={(allowed_role_ids) => updateButton({ allowed_role_ids })} />
      <div className="border-t pt-4"><p className="mb-3 text-xs font-medium">Содержимое типа обращения</p><div className="grid gap-3 sm:grid-cols-[1fr_110px]"><div><Label className="mb-1 block text-xs text-muted-foreground">Название</Label><Input value={type.name} disabled={disabled} onChange={(event) => updateType({ name: event.target.value })} /></div><div><Label className="mb-1 block text-xs text-muted-foreground">Цвет</Label><Input value={type.color} disabled={disabled} onChange={(event) => updateType({ color: event.target.value.replace(/^#/, "") })} /></div></div><div className="mt-3"><Label className="mb-1 block text-xs text-muted-foreground">Шаблон первого сообщения</Label><Textarea value={type.template} disabled={disabled} onChange={(event) => updateType({ template: event.target.value })} className="min-h-24 text-sm" /></div><label className="mt-3 flex items-center gap-2 text-xs"><Checkbox checked={!!type.allow_cooldown} disabled={disabled} onCheckedChange={(checked) => updateType({ allow_cooldown: checked === true })} /> Разрешить кулдаун после закрытия</label></div>
      <RoleRulesEditor value={type.role_rules} roles={roles} disabled={disabled} onChange={(role_rules) => updateType({ role_rules })} />
    </CardContent>
  </Card>
}

export function TicketSettings() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<TicketConfig | null>(null)
  const [selectedButton, setSelectedButton] = useState("technical")
  const { data, isLoading, isError } = useQuery({ queryKey: ["ticket-config"], queryFn: () => api.get<SettingsResponse>("/api/v1/admin/tickets/config") })
  const rolesQuery = useQuery({ queryKey: ["ticket-config-roles"], queryFn: () => api.get<{ roles: Role[] }>("/api/v1/admin/discord-bot/viewer/roles"), retry: false })
  useEffect(() => { if (data?.config) { setDraft(data.config); if (!data.config.buttons[selectedButton]) setSelectedButton(Object.keys(data.config.buttons)[0] ?? "technical") } }, [data?.config, selectedButton])

  const save = useMutation({ mutationFn: (config: TicketConfig) => api.put<SettingsResponse>("/api/v1/admin/tickets/config", { config }), onSuccess: (result) => { setDraft(result.config); queryClient.setQueryData(["ticket-config"], result); toast.success("Настройки тикетов сохранены") }, onError: (error: Error) => toast.error(error.message) })
  const apply = useMutation({ mutationFn: () => api.post<{ status: string; placements: PanelState }>("/api/v1/admin/tickets/config/apply"), onSuccess: (result) => { queryClient.setQueryData(["ticket-config"], (current: SettingsResponse | undefined) => current ? { ...current, panel_state: result.placements } : current); toast.success("Панели применены в Discord") }, onError: (error: Error) => toast.error(error.message) })
  const canEdit = !!data?.can_manage_config
  const dirty = !!draft && !!data && JSON.stringify(draft) !== JSON.stringify(data.config)
  const roles = rolesQuery.data?.roles ?? []
  if (isLoading) return <Card><CardContent className="py-8 text-sm text-muted-foreground">Загрузка конфигурации тикетов…</CardContent></Card>
  if (isError || !draft) return <Card className="border-destructive/50"><CardContent className="py-8 text-sm text-destructive">Не удалось загрузить конфигурацию тикетов.</CardContent></Card>

  const updatePlacement = (id: string, patch: Partial<Placement>) => setDraft({ ...draft, placements: { ...draft.placements, [id]: { ...draft.placements[id], ...patch } } })
  const state = data?.panel_state ?? {}
  return <div className="space-y-4">
    <Card className={dirty ? "border-amber-500/50 bg-amber-500/5" : "border-emerald-500/40 bg-emerald-500/5"}>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4"><div className="flex items-center gap-3"><ShieldCheck className="size-5 text-primary" /><div><p className="font-medium">Конфигурация Discord-тикетов</p><p className="text-xs text-muted-foreground">{dirty ? "Есть несохранённые изменения." : "Конфигурация синхронизирована с сервером."} Настройки старого редактора перенесены сюда.</p></div></div><div className="flex flex-wrap gap-2">{!canEdit && <Badge variant="outline">Только просмотр</Badge>}{canEdit && <><Button size="sm" variant="outline" disabled={!dirty || save.isPending} onClick={() => save.mutate(draft)}><Save className="size-3.5" /> Сохранить</Button><Button size="sm" disabled={dirty || apply.isPending} onClick={() => apply.mutate()}><Send className="size-3.5" /> Применить в Discord</Button></>}</div></CardContent>
    </Card>
    <div className="grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
      <div className="space-y-4">
        <Card><CardHeader><CardTitle className="text-sm">Общие настройки</CardTitle></CardHeader><CardContent className="space-y-3"><div><Label className="mb-1 block text-xs text-muted-foreground">Категория каналов тикетов</Label><Input value={draft.category_id} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, category_id: event.target.value })} placeholder="Discord ID категории" /></div><div><Label className="mb-1 block text-xs text-muted-foreground">Роли персонала</Label><Input value={draft.manager_role_ids.join(", ")} disabled={!canEdit} onChange={(event) => setDraft({ ...draft, manager_role_ids: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} placeholder="ID ролей через запятую" /></div></CardContent></Card>
        {Object.entries(draft.placements).map(([id, placement]) => <Card key={id}><CardHeader><CardTitle className="flex items-center gap-2 text-sm"><Hash className="size-4" /> {id === "status" ? "Статус обращений" : "Публичная панель"}</CardTitle></CardHeader><CardContent className="space-y-3"><div><Label className="mb-1 block text-xs text-muted-foreground">Канал Discord</Label><Input value={placement.channel_id} disabled={!canEdit} onChange={(event) => updatePlacement(id, { channel_id: event.target.value })} /></div><div className="flex items-center gap-2 text-xs text-muted-foreground">{state[id]?.published ? <><Check className="size-3.5 text-emerald-500" /> Сообщение привязано: <span className="font-mono">{state[id].message_id}</span></> : <><Link2 className="size-3.5" /> Сообщение ещё не опубликовано</>}</div><ButtonOrder placement={placement} buttons={draft.buttons} disabled={!canEdit} onChange={(button_ids) => updatePlacement(id, { button_ids })} /></CardContent></Card>)}
      </div>
      <div className="space-y-3"><div className="flex flex-wrap items-center gap-2"><p className="text-xs font-semibold text-muted-foreground">Кнопки создания</p>{Object.keys(draft.buttons).map((id) => <Button key={id} type="button" size="sm" variant={selectedButton === id ? "default" : "outline"} onClick={() => setSelectedButton(id)}>{draft.buttons[id].emoji} {draft.buttons[id].label}</Button>)}</div>{draft.buttons[selectedButton] ? <TicketButtonEditor buttonId={selectedButton} config={draft} roles={roles} disabled={!canEdit} onChange={setDraft} /> : <Card><CardContent className="py-8 text-sm text-muted-foreground">Выберите кнопку для настройки.</CardContent></Card>}</div>
    </div>
    {rolesQuery.isError && <p className="text-xs text-amber-300">Список ролей Discord недоступен. Сохранённые ID остаются рабочими, но выбрать новые роли сейчас нельзя.</p>}
    {roles.some((role) => ROLE_IDS.has(role.id)) && <p className="text-[11px] text-muted-foreground">Три роли технического тикета уже загружены из Discord и доступны в редакторе.</p>}
  </div>
}
