import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Radio, MoreVertical, Send, Truck, CornerUpLeft, PackagePlus, Zap, Settings,
  Bot, MapPin, Ship, Target, Users2, Warehouse as WarehouseIcon,
} from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PageHeader } from "@/components/layout/PageHeader"
import { api } from "@/lib/api"
import { canDo, useCurrentAdmin } from "@/lib/auth"
import { cn } from "@/lib/utils"
import type { ModerationOverview } from "@/pages/moderation/types"
import { Warehouse } from "./Warehouse"
import { CargoDialog } from "./CargoDialog"
import { TorpedoDialog } from "./TorpedoDialog"
import { dispatchActionsApi } from "./actionsApi"

const TARGET_SERVERS = ["ASPER1", "ASPER2"]

/** Send/recall are simple enough not to need their own file — just a
 * server picker + confirm, unlike cargo/torpedo which need a whole picker
 * UI of their own (see CargoDialog.tsx/TorpedoDialog.tsx). */
function ShuttleDialog({ open, onOpenChange, command, defaultServer }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  command: "send" | "recall"
  defaultServer?: string
}) {
  const [serverName, setServerName] = useState(
    defaultServer && TARGET_SERVERS.includes(defaultServer) ? defaultServer : TARGET_SERVERS[0]
  )
  const mutation = useMutation({
    mutationFn: () => dispatchActionsApi.shuttle(serverName, command),
    onSuccess: () => {
      toast.success(command === "send" ? "Челнок отправлен в очередь" : "Отзыв челнока поставлен в очередь")
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{command === "send" ? "Отправить челнок снабжения" : "Отозвать челнок снабжения"}</DialogTitle>
          <DialogDescription>Выберите сервер, на который нужно поставить это действие в очередь.</DialogDescription>
        </DialogHeader>
        <Select value={serverName} onValueChange={setServerName}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            {TARGET_SERVERS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={mutation.isPending} onClick={() => mutation.mutate()}>Подтвердить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Требует права dispatch:manage_webhooks — configures the outgoing webhook URL (per server) that
 * notifies an external system about incoming dispatch messages, shuttle
 * send requests, and confirmed shuttle departures. See routes/dispatch.py's
 * _send_dispatch_webhook / /api/v1/admin/dispatch/webhook-config. */
function WebhookConfigDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ["dispatch-webhook-config"],
    queryFn: () => api.get<{ ASPER1: string | null; ASPER2: string | null }>("/api/v1/admin/dispatch/webhook-config"),
    enabled: open,
  })
  const [asper1, setAsper1] = useState("")
  const [asper2, setAsper2] = useState("")

  useEffect(() => {
    setAsper1(data?.ASPER1 ?? "")
    setAsper2(data?.ASPER2 ?? "")
  }, [data])

  const saveMutation = useMutation({
    mutationFn: () => api.patch("/api/v1/admin/dispatch/webhook-config", {
      ASPER1: asper1.trim() || null,
      ASPER2: asper2.trim() || null,
    }),
    onSuccess: () => {
      toast.success("Webhook-адреса сохранены")
      queryClient.invalidateQueries({ queryKey: ["dispatch-webhook-config"] })
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Внешний webhook диспетчерской</DialogTitle>
          <DialogDescription>
            На этот адрес будут приходить POST-уведомления о входящих сообщениях,
            запросах на отправку и подтверждённом уходе челнока снабжения —
            отдельно для каждого сервера. Пусто — уведомления для этого сервера отключены.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="mb-1.5 block">ASPER1</Label>
            <Input value={asper1} onChange={(e) => setAsper1(e.target.value)} placeholder="https://..." />
          </div>
          <div>
            <Label className="mb-1.5 block">ASPER2</Label>
            <Input value={asper2} onChange={(e) => setAsper2(e.target.value)} placeholder="https://..." />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface DispatchMessage {
  id: number
  steamid: string | null
  player_name: string | null
  real_name: string | null
  text: string
  sender_type: "player" | "dispatcher"
  server_name: string | null
  created_at: string | null
}

interface AIDispatcherConfig {
  enabled: boolean
  configured: boolean
  history_messages: number
  enabled_tools: DispatcherToolName[]
  report_types: Record<DispatcherReportType, boolean>
}

type DispatcherToolName =
  | "get_world_state"
  | "get_navigation_relation"
  | "get_warehouse"
  | "queue_supply_cargo"
  | "control_supply_shuttle"
  | "schedule_follow_up"

type DispatcherReportType = "round_briefing" | "threat_alert" | "scheduled_follow_up"

type AIDispatcherConfigPatch = Partial<
  Pick<AIDispatcherConfig, "enabled" | "history_messages" | "enabled_tools" | "report_types">
>

const AI_TOOLS: ReadonlyArray<{ name: DispatcherToolName; label: string; description: string }> = [
  { name: "get_world_state", label: "Состояние мира", description: "Карта, цели и телеметрия активного раунда" },
  { name: "get_navigation_relation", label: "Навигация", description: "Расстояние и направление между объектами" },
  { name: "get_warehouse", label: "Склад", description: "Проверка остатков и идентификаторов предметов" },
  { name: "queue_supply_cargo", label: "Заказ груза", description: "Постановка снабжения в очередь" },
  { name: "control_supply_shuttle", label: "Челнок снабжения", description: "Отправка и отзыв челнока" },
  { name: "schedule_follow_up", label: "Повторная проверка", description: "Отложенная самопроверка диспетчера" },
]

const AI_REPORT_TYPES: ReadonlyArray<{ name: DispatcherReportType; label: string; description: string }> = [
  { name: "round_briefing", label: "Первичный доклад", description: "Автоматический доклад в начале раунда" },
  { name: "threat_alert", label: "Тревога по целям", description: "Доклады о новых и существенно сместившихся целях" },
  { name: "scheduled_follow_up", label: "Отложенные доклады", description: "Проверки, запланированные самим диспетчером" },
]

function AIDispatcherConfigDialog({
  open,
  onOpenChange,
  config,
  saving,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  config: AIDispatcherConfig | undefined
  saving: boolean
  onSave: (patch: AIDispatcherConfigPatch) => void
}) {
  const [historyMessages, setHistoryMessages] = useState(5)
  const [enabledTools, setEnabledTools] = useState<DispatcherToolName[]>([])
  const [reportTypes, setReportTypes] = useState<Record<DispatcherReportType, boolean>>({
    round_briefing: true,
    threat_alert: true,
    scheduled_follow_up: true,
  })

  useEffect(() => {
    if (!open || !config) return
    setHistoryMessages(config.history_messages)
    setEnabledTools(config.enabled_tools)
    setReportTypes(config.report_types)
  }, [open, config])

  const toggleTool = (name: DispatcherToolName, enabled: boolean) => {
    setEnabledTools((current) => (
      enabled
        ? AI_TOOLS.map((tool) => tool.name).filter((toolName) => toolName === name || current.includes(toolName))
        : current.filter((toolName) => toolName !== name)
    ))
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Конфигурация ИИ-диспетчера</DialogTitle>
          <DialogDescription>
            Изменения общие для всех серверов и применяются к новым запросам без перезапуска.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="ai-history-messages">Сообщений в истории чата</Label>
            <Input
              id="ai-history-messages"
              type="number"
              min={1}
              max={30}
              step={1}
              value={historyMessages}
              onChange={(event) => setHistoryMessages(Number(event.target.value))}
              className="max-w-28"
            />
            <p className="text-xs text-muted-foreground">
              По умолчанию 5. Допустимо от 1 до 30 предыдущих сообщений.
            </p>
          </div>

          <section className="space-y-2" aria-labelledby="ai-report-types-title">
            <div>
              <h3 id="ai-report-types-title" className="text-sm font-semibold">Типы автоматических докладов</h3>
              <p className="text-xs text-muted-foreground">Выключенный тип не ставится в очередь NexusData.</p>
            </div>
            <div className="divide-y rounded-md border">
              {AI_REPORT_TYPES.map((report) => (
                <div key={report.name} className="flex items-center justify-between gap-4 p-3">
                  <div className="min-w-0">
                    <Label htmlFor={`ai-report-${report.name}`} className="text-sm">{report.label}</Label>
                    <p className="text-xs text-muted-foreground">{report.description}</p>
                  </div>
                  <Switch
                    id={`ai-report-${report.name}`}
                    checked={reportTypes[report.name]}
                    onCheckedChange={(enabled) => setReportTypes((current) => ({ ...current, [report.name]: enabled }))}
                  />
                </div>
              ))}
            </div>
          </section>

          <section className="space-y-2" aria-labelledby="ai-tools-title">
            <div>
              <h3 id="ai-tools-title" className="text-sm font-semibold">Инструменты</h3>
              <p className="text-xs text-muted-foreground">Недоступные инструменты не передаются модели.</p>
            </div>
            <div className="grid gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-2">
              {AI_TOOLS.map((tool) => (
                <div key={tool.name} className="flex items-center justify-between gap-3 bg-card p-3">
                  <div className="min-w-0">
                    <Label htmlFor={`ai-tool-${tool.name}`} className="text-sm">{tool.label}</Label>
                    <p className="text-xs text-muted-foreground">{tool.description}</p>
                  </div>
                  <Switch
                    id={`ai-tool-${tool.name}`}
                    checked={enabledTools.includes(tool.name)}
                    onCheckedChange={(enabled) => toggleTool(tool.name, enabled)}
                  />
                </div>
              ))}
            </div>
          </section>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button
            disabled={saving || !Number.isInteger(historyMessages) || historyMessages < 1 || historyMessages > 30}
            onClick={() => onSave({ history_messages: historyMessages, enabled_tools: enabledTools, report_types: reportTypes })}
          >
            Сохранить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

const SERVERS = [
  { key: "ASPER1", label: "ASPER1" },
  { key: "ASPER2", label: "ASPER2" },
  { key: "", label: "Все" },
]

function formatTime(iso: string | null): string {
  if (!iso) return ""
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
}

function RoundInfoCard({ serverName, state }: { serverName: string; state: ModerationOverview["round_states"][string] }) {
  return (
    <Card className="overflow-hidden py-0">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 border-b bg-muted/30 py-2.5">
        <CardTitle className="text-sm font-semibold">{serverName}</CardTitle>
        <Badge variant={state.active ? "default" : "secondary"} className={state.active ? "bg-success text-white" : ""}>
          {state.active ? "Идёт раунд" : "Раунд не идёт"}
        </Badge>
      </CardHeader>
      {state.active ? (
        <CardContent className="space-y-1.5 py-3 text-xs">
          <div className="flex items-center gap-1.5 text-muted-foreground"><MapPin className="size-3.5 shrink-0" /> {state.location ?? "-"}</div>
          <div className="flex items-center gap-1.5 text-muted-foreground"><Ship className="size-3.5 shrink-0" /> {state.submarine_name ?? "-"}</div>
          <div className="flex items-center gap-1.5 text-muted-foreground"><Target className="size-3.5 shrink-0" /> {state.mission ?? "-"}</div>
          <div className="flex items-center gap-1.5 text-muted-foreground"><Users2 className="size-3.5 shrink-0" /> {state.players.length} игроков</div>
        </CardContent>
      ) : (
        <CardContent className="py-3 text-xs text-muted-foreground">Нет активного раунда</CardContent>
      )}
    </Card>
  )
}

export function DispatchPage() {
  const { data: admin } = useCurrentAdmin()
  const [server, setServer] = useState("ASPER1")
  const [text, setText] = useState("")
  const [sending, setSending] = useState(false)
  const queryClient = useQueryClient()
  const scrollRef = useRef<HTMLDivElement>(null)

  const [shuttleDialog, setShuttleDialog] = useState<"send" | "recall" | null>(null)
  const [cargoOpen, setCargoOpen] = useState(false)
  const [torpedoOpen, setTorpedoOpen] = useState(false)
  const [webhookConfigOpen, setWebhookConfigOpen] = useState(false)
  const [aiConfigOpen, setAIConfigOpen] = useState(false)

  const canSend = canDo(admin, "dispatch", "send_message")
  const canShuttle = canDo(admin, "dispatch", "shuttle")
  const canCargo = canDo(admin, "dispatch", "cargo")
  const canTorpedo = canDo(admin, "dispatch", "torpedo")
  const canWebhooks = canDo(admin, "dispatch", "manage_webhooks")
  const canManageAI = canDo(admin, "dispatch", "manage_ai")

  const { data: messages } = useQuery({
    queryKey: ["dispatch-messages", server],
    queryFn: () =>
      api.get<DispatchMessage[]>(
        `/api/v1/admin/dispatch/messages?${new URLSearchParams({ limit: "100", server }).toString()}`
      ),
    refetchInterval: 3000,
  })

  const { data: overview } = useQuery({
    queryKey: ["dispatch-overview", server],
    queryFn: () => api.get<ModerationOverview>(`/api/v1/admin/moderation/overview${server ? `?server=${server}` : ""}`),
    refetchInterval: 10000,
  })

  const {
    data: aiConfig,
    error: aiConfigError,
    isPending: aiConfigPending,
  } = useQuery({
    queryKey: ["dispatch-ai-config"],
    queryFn: () => api.get<AIDispatcherConfig>("/api/v1/admin/dispatch/ai-config"),
    enabled: Boolean(admin),
  })

  const aiConfigMutation = useMutation({
    mutationFn: (patch: AIDispatcherConfigPatch) =>
      api.patch<AIDispatcherConfig>("/api/v1/admin/dispatch/ai-config", patch),
    onSuccess: (nextConfig, patch) => {
      queryClient.setQueryData(["dispatch-ai-config"], nextConfig)
      if (Object.keys(patch).length === 1 && patch.enabled !== undefined) {
        toast.success(nextConfig.enabled ? "ИИ-диспетчер включён" : "ИИ-диспетчер отключён")
      } else {
        toast.success("Конфигурация ИИ-диспетчера сохранена")
        setAIConfigOpen(false)
      }
    },
    onError: (error: Error) => toast.error(error.message),
  })

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [messages])

  const pollPending = async (targetServer: string) => {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const query = targetServer ? `?${new URLSearchParams({ server: targetServer }).toString()}` : ""
      const res = await api.get<{ pending: boolean }>(`/api/v1/admin/dispatch/pending${query}`)
      if (!res.pending) break
      await new Promise((r) => setTimeout(r, 2000))
    }
  }

  const send = async () => {
    const trimmed = text.trim()
    if (!trimmed || sending) return
    setText("")
    setSending(true)
    let accepted = false
    try {
      await api.post("/api/v1/admin/dispatch/send", { text: trimmed, server_name: server || null })
      accepted = true
      await pollPending(server)
      queryClient.invalidateQueries({ queryKey: ["dispatch-messages"] })
    } catch (error) {
      if (!accepted) setText(trimmed)
      toast.error(error instanceof Error ? error.message : "Не удалось отправить радиограмму")
    } finally {
      setSending(false)
    }
  }

  // Права диспетчерской разбиты по операциям: реплика в эфир, челнок, груз и
  // торпедный удар — разные по последствиям вещи, и группе можно выдать
  // только часть из них.
  const hasAnyAction = canShuttle || canCargo || canTorpedo
  const roundStates = overview ? Object.entries(overview.round_states) : []
  const onlinePlayers = overview?.online_players ?? []
  const aiStatus = aiConfigPending
    ? "Проверка статуса"
    : aiConfigError
      ? "Статус недоступен"
      : !aiConfig?.configured
        ? "Не настроен"
        : aiConfig.enabled
          ? "Запросы включены"
          : "Запросы отключены"
  const aiToggleDisabled = !canManageAI
    || aiConfigPending
    || Boolean(aiConfigError)
    || !aiConfig?.configured
    || aiConfigMutation.isPending
  const aiToggleTitle = !canManageAI
    ? "Нет права на управление ИИ-диспетчером"
    : aiConfigError
      ? "Не удалось получить статус ИИ-диспетчера"
      : !aiConfig?.configured
        ? "ИИ-диспетчер не настроен в окружении NexusData"
        : undefined

  return (
    <div>
      <PageHeader
        title="Диспетчерская"
        description="Радиосвязь с игроками и статус раунда в реальном времени"
        icon={Radio}
        actions={canWebhooks ? (
          <Button variant="outline" size="sm" onClick={() => setWebhookConfigOpen(true)}>
            <Settings data-icon="inline-start" /> Webhook
          </Button>
        ) : undefined}
      />

      <Tabs defaultValue="radio">
        <TabsList className="mb-3">
          <TabsTrigger value="radio" className="gap-1.5"><Radio className="size-3.5" /> Радиосвязь</TabsTrigger>
          <TabsTrigger value="warehouse" className="gap-1.5"><WarehouseIcon className="size-3.5" /> Склад</TabsTrigger>
        </TabsList>

        <TabsContent value="radio">
      <div className="flex h-[calc(100vh-13rem)] gap-4">
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 rounded-lg border bg-card px-3 py-1.5">
              <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div className="flex min-w-0 flex-col">
                <Label htmlFor="ai-dispatcher-toggle" className="cursor-pointer text-xs font-semibold">
                  ИИ-диспетчер
                </Label>
                <span className="text-[10px] text-muted-foreground">{aiStatus}</span>
              </div>
              <Switch
                id="ai-dispatcher-toggle"
                checked={Boolean(aiConfig?.configured && aiConfig.enabled)}
                disabled={aiToggleDisabled}
                onCheckedChange={(enabled) => aiConfigMutation.mutate({ enabled })}
                aria-label="Включить или отключить запросы к ИИ-диспетчеру"
                title={aiToggleTitle}
              />
              {canManageAI ? (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => setAIConfigOpen(true)}
                  disabled={aiConfigPending || Boolean(aiConfigError)}
                  aria-label="Настроить ИИ-диспетчера"
                  title="Настроить ИИ-диспетчера"
                >
                  <Settings />
                </Button>
              ) : null}
            </div>

            <div className="flex gap-1 rounded-lg border bg-card p-1">
              {SERVERS.map((s) => (
                <button
                  key={s.key}
                  onClick={() => setServer(s.key)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-xs font-bold transition-colors",
                    server === s.key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          <Card className="flex flex-1 flex-col overflow-hidden p-0">
            <div ref={scrollRef} className="flex flex-1 flex-col gap-2 overflow-y-auto p-4">
              {!messages?.length && (
                <div className="py-10 text-center text-sm text-muted-foreground">Сообщений пока нет</div>
              )}
              {messages?.map((m) => {
                const outgoing = m.sender_type === "dispatcher"
                return (
                  <div key={m.id} className={cn("flex flex-col", outgoing ? "items-end" : "items-start")}>
                    <div
                      className={cn(
                        "max-w-[75%] rounded-2xl px-3.5 py-2 text-sm",
                        outgoing ? "rounded-br-sm bg-primary text-primary-foreground" : "rounded-bl-sm bg-muted"
                      )}
                    >
                      {!outgoing && (
                        <div
                          className="mb-0.5 text-xs font-semibold text-info"
                          title={m.real_name ? `Настоящий ник: ${m.real_name}` : (m.steamid ?? "")}
                        >
                          {m.player_name || m.steamid || "?"}
                        </div>
                      )}
                      <div className="break-words">{m.text}</div>
                    </div>
                    <span className="mt-0.5 px-1 font-mono text-[10px] text-muted-foreground">
                      {outgoing ? "Диспетчер · " : ""}{formatTime(m.created_at)}
                    </span>
                  </div>
                )
              })}
            </div>

            {(canSend || hasAnyAction) && (
              <div className="flex items-center gap-2 border-t p-3">
                {hasAnyAction && <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="icon" className="shrink-0 rounded-full">
                      <MoreVertical className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" side="top">
                    {canShuttle && (
                      <>
                        <DropdownMenuItem onClick={() => setShuttleDialog("send")}>
                          <Truck className="mr-1 size-4" /> Отправить челнок снабжения
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => setShuttleDialog("recall")}>
                          <CornerUpLeft className="mr-1 size-4" /> Отозвать челнок снабжения
                        </DropdownMenuItem>
                      </>
                    )}
                    {canCargo && (
                      <DropdownMenuItem onClick={() => setCargoOpen(true)}>
                        <PackagePlus className="mr-1 size-4" /> Создать груз в очередь на спавн
                      </DropdownMenuItem>
                    )}
                    {canTorpedo && (
                      <DropdownMenuItem onClick={() => setTorpedoOpen(true)}>
                        <Zap className="mr-1 size-4" /> Сделать торпедный удар
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>}
                {canSend ? (
                  <>
                    <Input
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && send()}
                      placeholder="Диспетчер, введите сообщение..."
                      maxLength={500}
                      disabled={sending}
                      autoComplete="off"
                    />
                    <Button onClick={send} disabled={sending || !text.trim()} size="icon" className="shrink-0 rounded-full">
                      <Send className="size-4" />
                    </Button>
                  </>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    Нет права на отправку сообщений в эфир
                  </span>
                )}
              </div>
            )}
          </Card>
        </div>

        <div className="hidden min-h-0 w-72 shrink-0 flex-col gap-3 lg:flex">
          {roundStates.map(([sn, state]) => <RoundInfoCard key={sn} serverName={sn} state={state} />)}

          <Card className="flex min-h-0 flex-1 flex-col overflow-hidden py-0">
            <CardHeader className="border-b bg-muted/30 py-2.5">
              <CardTitle className="text-sm font-semibold">Игроки онлайн ({onlinePlayers.length})</CardTitle>
            </CardHeader>
            <ScrollArea className="min-h-0 flex-1">
              <div className="divide-y">
                {onlinePlayers.map((p) => (
                  <div key={p.player_steamid} className="flex items-center justify-between px-3 py-2 text-xs">
                    <span
                      className="truncate font-medium"
                      title={p.forced_display_name ? `Настоящий ник: ${p.player_name ?? p.player_steamid}` : undefined}
                    >
                      {p.forced_display_name || p.player_name || p.character_name || "?"}
                    </span>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {server === "" && p.server_name && <Badge variant="outline" className="text-[10px]">{p.server_name}</Badge>}
                      <Badge variant="secondary" className="text-[10px]">{p.job_id ?? "-"}</Badge>
                    </div>
                  </div>
                ))}
                {!onlinePlayers.length && <div className="py-6 text-center text-xs text-muted-foreground">Никого нет онлайн</div>}
              </div>
            </ScrollArea>
          </Card>
        </div>
      </div>
        </TabsContent>

        <TabsContent value="warehouse">
          <Warehouse />
        </TabsContent>
      </Tabs>

      {shuttleDialog && (
        <ShuttleDialog
          open={!!shuttleDialog}
          onOpenChange={(o) => !o && setShuttleDialog(null)}
          command={shuttleDialog}
          defaultServer={server}
        />
      )}
      <CargoDialog open={cargoOpen} onOpenChange={setCargoOpen} defaultServer={server} />
      <TorpedoDialog open={torpedoOpen} onOpenChange={setTorpedoOpen} defaultServer={server} />
      {canWebhooks && (
        <WebhookConfigDialog open={webhookConfigOpen} onOpenChange={setWebhookConfigOpen} />
      )}
      {canManageAI && (
        <AIDispatcherConfigDialog
          open={aiConfigOpen}
          onOpenChange={setAIConfigOpen}
          config={aiConfig}
          saving={aiConfigMutation.isPending}
          onSave={(patch) => aiConfigMutation.mutate(patch)}
        />
      )}
    </div>
  )
}
