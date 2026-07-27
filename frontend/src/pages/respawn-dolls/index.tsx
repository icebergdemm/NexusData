import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { UserPlus, Copy, Ban, Pencil, AlertTriangle, Users, PlayCircle, Undo2 } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { DollEditor } from "./DollEditor"
import { QueueDialog } from "./QueueDialog"
import { SpriteStack, appearanceLayers } from "./sprites"
import {
  SERVERS, STATE_LABEL, STATE_VARIANT,
  type AppearanceCatalog, type Doll, type DollReference, type DollsResponse,
} from "./types"

/** Миниатюра куклы в списке — по ней видно, кого именно собрали, без
 *  открытия конструктора. */
function DollAvatar({ doll, catalog }: { doll: Doll; catalog?: AppearanceCatalog }) {
  if (!catalog?.heads?.length) return null
  const layers = appearanceLayers(doll.spec?.identity ?? {}, catalog).map((l) => l.sprite)
  if (!layers.length) return null
  return <SpriteStack layers={layers} catalog={catalog} size={40} className="rounded-md bg-muted/40" />
}

export function RespawnDollsPage() {
  const queryClient = useQueryClient()
  const [server, setServer] = useState("all")
  const [state, setState] = useState("all")
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<Doll | null>(null)
  const [queueTarget, setQueueTarget] = useState<Doll | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["dolls", server, state],
    queryFn: () => {
      const p = new URLSearchParams()
      if (server !== "all") p.set("server", server)
      if (state !== "all") p.set("state", state)
      return api.get<DollsResponse>(`/api/v1/admin/dolls?${p}`)
    },
    // Плагин опрашивает сайт раз в ~10 с — статус меняется без действий
    // пользователя, поэтому список подтягивается сам.
    refetchInterval: 15000,
  })

  const { data: catalog } = useQuery({
    queryKey: ["dolls-appearance"],
    queryFn: () => api.get<AppearanceCatalog>("/api/v1/admin/dolls/appearance"),
    staleTime: Infinity,   // выгрузка из игрового контента, при работе не меняется
  })

  const { data: reference } = useQuery({
    queryKey: ["dolls-reference"],
    queryFn: () => api.get<DollReference>("/api/v1/admin/dolls/reference"),
    staleTime: Infinity,
  })

  const simpleAction = (path: string, message: string) => ({
    mutationFn: (id: string) => api.post(`/api/v1/admin/dolls/${id}${path}`, {}),
    onSuccess: () => {
      toast.success(message)
      queryClient.invalidateQueries({ queryKey: ["dolls"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const cancel = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/admin/dolls/${id}`),
    onSuccess: () => {
      toast.success("Кукла отменена — сервер снимет её со спавна в течение ~10 секунд")
      queryClient.invalidateQueries({ queryKey: ["dolls"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const clone = useMutation(simpleAction("/clone", "Создан черновик-копия"))
  const unqueue = useMutation(simpleAction("/unqueue", "Кукла снята с очереди и вернулась в черновики"))

  const openEditor = (doll: Doll | null) => { setEditing(doll); setEditorOpen(true) }

  return (
    <div>
      <PageHeader
        title="Куклы респавна"
        description="Заготовки персонажей для ивентов: внешность, профессия, навыки, инвентарь. Игрок привязывается при постановке в очередь"
        icon={Users}
        actions={data?.can_create && (
          <Button onClick={() => openEditor(null)}>
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
                  <TableHead className="w-40 text-right">Действия</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.dolls ?? []).map((d) => {
                  const editable = d.state === "draft" || d.state === "pending"
                  return (
                    <TableRow key={d.public_id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <DollAvatar doll={d} catalog={catalog} />
                          <div className="min-w-0">
                            <div className="truncate font-medium">
                              {d.label || d.spec?.identity?.name || d.public_id}
                            </div>
                            <div className="truncate text-xs text-muted-foreground">
                              {d.spec?.job?.identifier ?? "—"}
                              {" · "}{d.spawn_mode}
                              {!d.one_shot && " · шаблон"}
                              {d.priority !== 0 && ` · приоритет ${d.priority}`}
                            </div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell><Badge variant="secondary">{d.server}</Badge></TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {d.bind_steamids.length
                          ? d.bind_steamids.map((s) => <div key={s} className="font-mono">{s}</div>)
                          : <span className="italic">не привязана</span>}
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
                              {editable && (
                                <Button
                                  variant={d.state === "draft" ? "default" : "ghost"}
                                  size={d.state === "draft" ? "sm" : "icon-sm"}
                                  title={d.state === "draft" ? "Поставить в очередь" : "Изменить привязки"}
                                  onClick={() => setQueueTarget(d)}
                                >
                                  <PlayCircle className="size-4" />
                                  {d.state === "draft" && <span className="ml-1">В очередь</span>}
                                </Button>
                              )}
                              {editable && (
                                <Button variant="ghost" size="icon-sm" title="Изменить" onClick={() => openEditor(d)}>
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
                            <Button variant="ghost" size="icon-sm" title="Снять с очереди в черновики"
                              onClick={() => unqueue.mutate(d.public_id)}>
                              <Undo2 className="size-4" />
                            </Button>
                          )}
                          {data?.can_cancel && editable && (
                            <Button variant="ghost" size="icon-sm" title="Отменить"
                              onClick={() => {
                                if (confirm(`Отменить куклу «${d.label || d.public_id}»?`)) cancel.mutate(d.public_id)
                              }}>
                              <Ban className="size-4 text-destructive" />
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
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

      {editorOpen && (
        <DollEditor
          // key заставляет форму пересобраться под другую куклу: состояние
          // вкладок инициализируется из props только при монтировании.
          key={editing?.public_id ?? "new"}
          open={editorOpen}
          onOpenChange={setEditorOpen}
          editing={editing}
          catalog={catalog}
          reference={reference}
        />
      )}

      {queueTarget && (
        <QueueDialog
          key={queueTarget.public_id}
          doll={queueTarget}
          open={!!queueTarget}
          onOpenChange={(o) => !o && setQueueTarget(null)}
        />
      )}
    </div>
  )
}
