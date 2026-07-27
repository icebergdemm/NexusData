import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { UserPlus, Copy, Ban, Pencil, AlertTriangle, Users } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Checkbox } from "@/components/ui/checkbox"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"

interface Doll {
  public_id: string
  server: string
  label: string | null
  priority: number
  one_shot: boolean
  spawn_mode: string
  team: number
  expires_at: string | null
  bind_steamids: string[]
  spec: Record<string, unknown>
  state: string
  state_message: string | null
  spawned_at: string | null
  spawned_steamid: string | null
  spawned_character_name: string | null
  created_by: string | null
  created_at: string | null
}
interface DollsResponse {
  dolls: Doll[]
  can_create: boolean
  can_cancel: boolean
  reference: {
    jobs: string[]; skills: string[]; slots: string[]; limbs: string[]; spawn_modes: string[]
  }
}

const SERVERS = ["ASPER1", "ASPER2"]

const STATE_LABEL: Record<string, string> = {
  pending: "Ожидает",
  spawned: "Заспавнена",
  spawned_with_errors: "Заспавнена с ошибками",
  failed: "Ошибка",
  cancelled: "Отменена",
  expired: "Просрочена",
}
const STATE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  pending: "default",
  spawned: "secondary",
  spawned_with_errors: "outline",
  failed: "destructive",
  cancelled: "outline",
  expired: "outline",
}

/** Минимальное тело куклы — остальное дописывается в JSON-поле.
 *  Форма намеренно закрывает частый случай (имя + профессия + привязка),
 *  а полная схема из инструкции доступна через редактор JSON. */
const DEFAULT_SPEC = JSON.stringify(
  { identity: { name: "" }, job: { identifier: "assistant" } },
  null, 2,
)

function DollDialog({ open, onOpenChange, editing, reference }: {
  open: boolean
  onOpenChange: (o: boolean) => void
  editing: Doll | null
  reference?: DollsResponse["reference"]
}) {
  const queryClient = useQueryClient()
  const [server, setServer] = useState(editing?.server ?? SERVERS[0])
  const [label, setLabel] = useState(editing?.label ?? "")
  const [steamids, setSteamids] = useState((editing?.bind_steamids ?? []).join("\n"))
  const [spawnMode, setSpawnMode] = useState(editing?.spawn_mode ?? "auto")
  const [priority, setPriority] = useState(String(editing?.priority ?? 0))
  const [oneShot, setOneShot] = useState(editing?.one_shot ?? true)
  const [specText, setSpecText] = useState(
    editing ? JSON.stringify(editing.spec ?? {}, null, 2) : DEFAULT_SPEC,
  )

  const save = useMutation({
    mutationFn: () => {
      let spec: unknown
      try {
        spec = JSON.parse(specText || "{}")
      } catch {
        throw new Error("Тело куклы — некорректный JSON")
      }
      const payload = {
        server, label, spec, spawn_mode: spawnMode,
        priority: Number(priority) || 0,
        one_shot: oneShot,
        bind_steamids: steamids.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
      }
      return editing
        ? api.patch<{ warnings: string[] }>(`/api/v1/admin/dolls/${editing.public_id}`, payload)
        : api.post<{ warnings: string[] }>("/api/v1/admin/dolls", payload)
    },
    onSuccess: (res) => {
      const warnings = res?.warnings ?? []
      toast.success(editing ? "Кукла обновлена" : "Кукла создана")
      warnings.forEach((w) => toast.warning(w))
      queryClient.invalidateQueries({ queryKey: ["dolls"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? "Изменить куклу" : "Новая кукла"}</DialogTitle>
          <DialogDescription>
            Кукла попадёт на игровой сервер в течение ~10 секунд и применится при
            следующей волне респавна привязанных игроков.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label className="text-xs">Сервер</Label>
            <Select value={server} onValueChange={setServer}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {SERVERS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs">Название (для админки)</Label>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Капитан для ивента" />
          </div>
          <div>
            <Label className="text-xs">Режим спавна</Label>
            <Select value={spawnMode} onValueChange={setSpawnMode}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {(reference?.spawn_modes ?? ["auto", "player", "bot"]).map((m) => (
                  <SelectItem key={m} value={m}>
                    {m === "auto" ? "auto — игроку, если есть привязка" : m === "player" ? "player — только игроку" : "bot — всегда ботом"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs">Приоритет</Label>
            <Input type="number" value={priority} onChange={(e) => setPriority(e.target.value)} />
          </div>
        </div>

        <div>
          <Label className="text-xs">SteamID64 игроков (по одному в строке; пусто — кукла-бот)</Label>
          <Textarea rows={3} value={steamids} onChange={(e) => setSteamids(e.target.value)}
            placeholder="76561198000000000" className="font-mono text-xs" />
        </div>

        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={oneShot} onCheckedChange={(v) => setOneShot(!!v)} />
          Одноразовая — после спавна кукла снимается (снимите галочку, чтобы сделать шаблон,
          применяемый при каждом респавне)
        </label>

        <div>
          <Label className="text-xs">
            Тело куклы (identity / job / progression / stats / health / inventory)
          </Label>
          <Textarea rows={14} value={specText} onChange={(e) => setSpecText(e.target.value)}
            className="font-mono text-xs" spellCheck={false} />
          {!!reference && (
            <p className="mt-1 text-xs text-muted-foreground">
              Профессии: {reference.jobs.join(", ")} · Навыки: {reference.skills.join(", ")}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function RespawnDollsPage() {
  const queryClient = useQueryClient()
  const [server, setServer] = useState("all")
  const [state, setState] = useState("all")
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Doll | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["dolls", server, state],
    queryFn: () => {
      const p = new URLSearchParams()
      if (server !== "all") p.set("server", server)
      if (state !== "all") p.set("state", state)
      return api.get<DollsResponse>(`/api/v1/admin/dolls?${p}`)
    },
    // Плагин опрашивает сайт раз в ~10 с — статус меняется без действий
    // пользователя, поэтому список сам подтягивается.
    refetchInterval: 15000,
  })

  const cancel = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/admin/dolls/${id}`),
    onSuccess: () => {
      toast.success("Кукла отменена — сервер снимет её со спавна в течение ~10 секунд")
      queryClient.invalidateQueries({ queryKey: ["dolls"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const clone = useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/admin/dolls/${id}/clone`, {}),
    onSuccess: () => {
      toast.success("Копия создана — укажите в ней SteamID")
      queryClient.invalidateQueries({ queryKey: ["dolls"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div>
      <PageHeader
        title="Куклы респавна"
        description="Заранее подготовленные персонажи для ивентов: внешность, профессия, навыки, инвентарь"
        icon={Users}
        actions={data?.can_create && (
          <Button onClick={() => { setEditing(null); setDialogOpen(true) }}>
            <UserPlus className="mr-1 size-4" /> Новая кукла
          </Button>
        )}
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select value={server} onValueChange={setServer}>
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все серверы</SelectItem>
            {SERVERS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={state} onValueChange={setState}>
          <SelectTrigger className="w-52"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Все состояния</SelectItem>
            {Object.entries(STATE_LABEL).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      <Card className="overflow-hidden py-0">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8" />)}</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Кукла</TableHead>
                  <TableHead className="w-24">Сервер</TableHead>
                  <TableHead className="w-40">Привязки</TableHead>
                  <TableHead className="w-44">Состояние</TableHead>
                  <TableHead className="w-32 text-right">Действия</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.dolls ?? []).map((d) => (
                  <TableRow key={d.public_id}>
                    <TableCell>
                      <div className="font-medium">{d.label || d.public_id}</div>
                      <div className="text-xs text-muted-foreground">
                        {d.public_id} · {d.spawn_mode}
                        {!d.one_shot && " · шаблон"}
                        {d.priority !== 0 && ` · приоритет ${d.priority}`}
                      </div>
                    </TableCell>
                    <TableCell><Badge variant="secondary">{d.server}</Badge></TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {d.bind_steamids.length
                        ? d.bind_steamids.map((s) => <div key={s} className="font-mono">{s}</div>)
                        : <span className="italic">бот</span>}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATE_VARIANT[d.state] ?? "outline"}>
                        {STATE_LABEL[d.state] ?? d.state}
                      </Badge>
                      {d.spawned_character_name && (
                        <div className="mt-1 text-xs text-muted-foreground">{d.spawned_character_name}</div>
                      )}
                      {d.state_message && (
                        <div className="mt-1 flex items-start gap-1 text-xs text-warning">
                          <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                          <span>{d.state_message}</span>
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {data?.can_create && (
                          <>
                            {d.state === "pending" && (
                              <Button variant="ghost" size="icon-sm" title="Изменить"
                                onClick={() => { setEditing(d); setDialogOpen(true) }}>
                                <Pencil className="size-4" />
                              </Button>
                            )}
                            <Button variant="ghost" size="icon-sm" title="Клонировать"
                              onClick={() => clone.mutate(d.public_id)}>
                              <Copy className="size-4" />
                            </Button>
                          </>
                        )}
                        {data?.can_cancel && d.state === "pending" && (
                          <Button variant="ghost" size="icon-sm" title="Отменить"
                            onClick={() => {
                              if (confirm(`Снять куклу «${d.label || d.public_id}» со спавна?`)) cancel.mutate(d.public_id)
                            }}>
                            <Ban className="size-4 text-destructive" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
                {!data?.dolls.length && (
                  <TableRow>
                    <TableCell colSpan={5} className="py-8 text-center text-sm text-muted-foreground">
                      Кукол нет
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {dialogOpen && (
        <DollDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          editing={editing}
          reference={data?.reference}
        />
      )}
    </div>
  )
}
