import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Clock3, MessageCircle, Play, Radio, Settings2, Users } from "lucide-react"
import { api } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { lobbyServerStatusLabel, shouldShowLobbyCountdown } from "./lobby-advertisement-utils"

type Stage = "round" | "lobby" | "waiting" | "shown"

type AdvertisementCycle = {
  status: "pending" | "attempted" | "sent" | "skipped" | "error"
  due_at: string | null
  trigger_reason: string | null
  discord_link: string | null
  discord_source: string | null
  last_error: string | null
  sent_at: string | null
  skipped_at: string | null
}

type Advertisement = {
  server: "ASPER1" | "ASPER2"
  enabled: boolean
  delay_seconds: number
  message: string
  fallback_url: string
  keywords: string[]
  early_trigger_enabled: boolean
  online_count: number
  round_active: boolean
  lobby_active: boolean
  server_state: string | null
  server_running: boolean | null
  server_error: string | null
  cycle: AdvertisementCycle | null
  current_stage: Stage
}

type AdvertisementResponse = { advertisements: Advertisement[] }
type AdvertisementPayload = {
  enabled: boolean
  delay_seconds: number
  message: string
  fallback_url: string
  keywords: string[]
  early_trigger_enabled: boolean
}

const stages: { key: Stage | "new_round"; label: string }[] = [
  { key: "round", label: "Раунд" },
  { key: "lobby", label: "Лобби" },
  { key: "waiting", label: "Ожидание показа" },
  { key: "shown", label: "Показ" },
  { key: "new_round", label: "Новый раунд" },
]

function countdown(value: string | null, now: number) {
  if (!value) return "—"
  const seconds = Math.max(0, Math.ceil((new Date(value).getTime() - now) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}

function stageIndex(stage: Stage) {
  if (stage === "round") return 0
  if (stage === "lobby") return 1
  if (stage === "waiting") return 2
  return 3
}

function cycleLabel(cycle: AdvertisementCycle | null) {
  if (!cycle) return "Цикл ещё не создан"
  if (cycle.status === "pending" || cycle.status === "attempted") return "Ожидает подходящего момента"
  if (cycle.status === "sent") return `Отправлено: ${cycle.trigger_reason === "keyword" ? "по ключевому слову" : "по таймеру"}`
  if (cycle.status === "skipped") return `Пропущено: ${cycle.trigger_reason || "условия не выполнены"}`
  return "Ошибка отправки"
}

function configFrom(advertisement: Advertisement): AdvertisementPayload {
  return {
    enabled: advertisement.enabled,
    delay_seconds: advertisement.delay_seconds,
    message: advertisement.message,
    fallback_url: advertisement.fallback_url,
    keywords: advertisement.keywords,
    early_trigger_enabled: advertisement.early_trigger_enabled,
  }
}

function AdvertisementDialog({
  advertisement,
  open,
  onOpenChange,
  onSave,
  pending,
}: {
  advertisement: Advertisement | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (payload: AdvertisementPayload) => void
  pending: boolean
}) {
  const [delay, setDelay] = useState(120)
  const [message, setMessage] = useState("")
  const [fallback, setFallback] = useState("ds-asper.ru")
  const [keywords, setKeywords] = useState("")
  const [earlyTrigger, setEarlyTrigger] = useState(true)

  useEffect(() => {
    if (!advertisement) return
    setDelay(advertisement.delay_seconds)
    setMessage(advertisement.message)
    setFallback(advertisement.fallback_url)
    setKeywords(advertisement.keywords.join(", "))
    setEarlyTrigger(advertisement.early_trigger_enabled)
  }, [advertisement])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Discord-реклама: {advertisement?.server}</DialogTitle>
          <DialogDescription>Один показ на лобби. Цветные теги здесь специально не добавляются: msg_box выводит их текстом.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="lobby-ad-delay">Задержка после раунда, секунд</Label>
              <Input id="lobby-ad-delay" className="mt-1" type="number" min={0} max={3600} value={delay} onChange={(event) => setDelay(Number(event.target.value))} />
            </div>
            <div>
              <Label htmlFor="lobby-ad-fallback">Fallback-ссылка</Label>
              <Input id="lobby-ad-fallback" className="mt-1" value={fallback} onChange={(event) => setFallback(event.target.value)} />
            </div>
          </div>
          <div>
            <Label htmlFor="lobby-ad-message">Текст сообщения</Label>
            <Textarea id="lobby-ad-message" className="mt-1" maxLength={200} value={message} onChange={(event) => setMessage(event.target.value)} />
            <p className="mt-1 text-xs text-muted-foreground">Используйте <code>{"{discord_link}"}</code> — NexusData подставит актуальную ссылку Discord.</p>
          </div>
          <div>
            <Label htmlFor="lobby-ad-keywords">Ключевые слова</Label>
            <Input id="lobby-ad-keywords" className="mt-1" value={keywords} onChange={(event) => setKeywords(event.target.value)} />
            <p className="mt-1 text-xs text-muted-foreground">Через запятую. Поиск понимает простые русские окончания и не срабатывает на “серверный”.</p>
          </div>
          <label className="flex items-start gap-2 text-sm">
            <Checkbox className="mt-0.5" checked={earlyTrigger} onCheckedChange={(value) => setEarlyTrigger(value === true)} />
            <span>Отправлять сразу, если в лобби игрок произнёс ключевое слово</span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={pending || !message.trim()} onClick={() => onSave({
            enabled: advertisement?.enabled ?? false,
            delay_seconds: Math.max(0, Math.min(3600, delay || 0)),
            message,
            fallback_url: fallback,
            keywords: keywords.split(",").map((value) => value.trim()).filter(Boolean),
            early_trigger_enabled: earlyTrigger,
          })}>Сохранить</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AdvertisementCard({
  advertisement,
  pending,
  onConfigure,
  onToggle,
  onTest,
}: {
  advertisement: Advertisement
  pending: boolean
  onConfigure: () => void
  onToggle: (enabled: boolean) => void
  onTest: () => void
}) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const current = stageIndex(advertisement.current_stage)
  const canTest = !pending && advertisement.server_running === true && advertisement.online_count > 0
  const serverStatus = lobbyServerStatusLabel(advertisement.server_state, advertisement.server_running, advertisement.server_error)
  const countdownText = advertisement.cycle?.status === "pending" && shouldShowLobbyCountdown(advertisement.server_state, advertisement.server_running, advertisement.server_error)
    ? countdown(advertisement.cycle.due_at, now)
    : "—"
  const cycleDescription = advertisement.server_running === false && !advertisement.server_error
    ? "Сервер выключен — объявление не отправляется"
    : advertisement.server_error
      ? "Состояние сервера не определено — объявление не отправляется"
      : advertisement.current_stage === "round"
        ? "Раунд идёт — объявление не отправляется"
        : cycleLabel(advertisement.cycle)

  return (
    <Card className="overflow-hidden py-0">
      <CardHeader className="flex flex-row items-center justify-between gap-3 border-b bg-muted/30 py-3">
        <div>
          <CardTitle className="text-sm font-semibold">{advertisement.server}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">Одно объявление в каждом лобби</p>
        </div>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>{advertisement.enabled ? "Включено" : "Выключено"}</span>
          <Switch checked={advertisement.enabled} disabled={pending} onCheckedChange={onToggle} aria-label={`Discord-реклама ${advertisement.server}`} />
        </label>
      </CardHeader>
      <CardContent className="space-y-4 py-4">
        <div className="grid grid-cols-5 gap-1.5">
          {stages.map((stage, index) => {
            const active = stage.key !== "new_round" && index <= current
            const currentStage = stage.key !== "new_round" && index === current
            return (
              <div key={stage.key} className={`rounded-md border px-1.5 py-2 text-center text-[11px] leading-tight ${currentStage ? "border-primary bg-primary/10 text-foreground" : active ? "border-primary/30 bg-primary/5 text-muted-foreground" : "text-muted-foreground/60"}`}>
                <div className="mx-auto mb-1 flex size-5 items-center justify-center rounded-full border text-[10px]">{index + 1}</div>
                {stage.label}
              </div>
            )
          })}
        </div>

        <div className="rounded-lg border bg-background/40 p-4 text-center">
          <div className="font-mono text-3xl font-semibold tracking-tight tabular-nums">{countdownText}</div>
          <div className="mt-1 text-xs text-muted-foreground">{cycleDescription}</div>
          <Badge className="mt-3" variant={advertisement.enabled ? "default" : "secondary"}>{advertisement.enabled ? "Контроллер активен" : "Контроллер выключен"}</Badge>
        </div>

        <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
          <div className="flex items-center gap-2"><Users className="size-3.5" /> Онлайн: {advertisement.online_count}</div>
          <div className={`flex items-center gap-2 ${advertisement.server_running === false ? "text-amber-300" : advertisement.server_error ? "text-destructive" : ""}`}><Radio className="size-3.5" /> Сервер: {serverStatus}</div>
          <div className="flex items-center gap-2"><Clock3 className="size-3.5" /> Таймер: {advertisement.delay_seconds} с</div>
          <div className="flex items-center gap-2"><MessageCircle className="size-3.5" /> Ранний триггер: {advertisement.early_trigger_enabled ? "включён" : "выключен"}</div>
        </div>

        {advertisement.cycle?.discord_source && <p className="text-xs text-muted-foreground">Ссылка: {advertisement.cycle.discord_source === "discord" ? "актуальная из Discord" : "fallback"}{advertisement.cycle.discord_link ? ` — ${advertisement.cycle.discord_link}` : ""}</p>}
        {(advertisement.server_error || advertisement.cycle?.last_error) && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">{advertisement.server_error || advertisement.cycle?.last_error}</p>}
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={onConfigure} disabled={pending}><Settings2 /> Настроить</Button>
          <Button variant="outline" size="sm" onClick={onTest} disabled={!canTest}><Play /> Тестовый msg_box</Button>
          {!canTest && <span className="self-center text-xs text-muted-foreground">Тест доступен только при запущенном сервере и онлайне</span>}
        </div>
      </CardContent>
    </Card>
  )
}

export function LobbyAdvertisementSettings() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<Advertisement | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ["lobby-advertisements"],
    queryFn: () => api.get<AdvertisementResponse>("/api/v1/admin/lobby-advertisements"),
    refetchInterval: 5000,
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["lobby-advertisements"] })
  const update = useMutation({
    mutationFn: ({ server, payload }: { server: string; payload: AdvertisementPayload }) => api.patch(`/api/v1/admin/lobby-advertisements/${server}`, payload),
    onSuccess: () => { toast.success("Настройки Discord-рекламы сохранены"); setEditing(null); refresh() },
    onError: (error: Error) => toast.error(error.message),
  })
  const test = useMutation({
    mutationFn: (server: string) => api.post<{ message: string; discord_source: string }>(`/api/v1/admin/lobby-advertisements/${server}/test`),
    onSuccess: (result) => { toast.success(`Тестовый msg_box отправлен (${result.discord_source})`); refresh() },
    onError: (error: Error) => toast.error(error.message),
  })

  if (isLoading || !data) return <div className="h-48 animate-pulse rounded-lg bg-muted/30" />

  const pending = update.isPending || test.isPending
  return (
    <>
      <Card className="overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Оповещение Discord в лобби</CardTitle></CardHeader>
        <CardContent className="space-y-3 py-4">
          <p className="text-xs text-muted-foreground">NexusData отправляет максимум одно plain-text сообщение через Pterodactyl в каждом цикле “раунд → лобби”. Приоритет у ключевого слова из распознанной речи, затем работает таймер.</p>
          <div className="grid gap-4 md:grid-cols-2">
            {data.advertisements.map((advertisement) => (
              <AdvertisementCard
                key={advertisement.server}
                advertisement={advertisement}
                pending={pending}
                onConfigure={() => setEditing(advertisement)}
                onToggle={(enabled) => update.mutate({ server: advertisement.server, payload: { ...configFrom(advertisement), enabled } })}
                onTest={() => test.mutate(advertisement.server)}
              />
            ))}
          </div>
        </CardContent>
      </Card>
      <AdvertisementDialog
        advertisement={editing}
        open={Boolean(editing)}
        onOpenChange={(open) => !open && setEditing(null)}
        pending={update.isPending}
        onSave={(payload) => editing && update.mutate({ server: editing.server, payload })}
      />
    </>
  )
}
