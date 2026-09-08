import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Clock3, RotateCcw, Settings2, Users } from "lucide-react"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { formatRestartCountdown, restartStatusLabel, type RestartStatus } from "./server-restart-utils"

type RestartSchedule = {
  server: "ASPER1" | "ASPER2"
  enabled: boolean
  daily_time: string
  msg_box: string
  conditions: { no_online: boolean; round_finished: boolean }
  next_run_at: string | null
  scheduled_for: string | null
  online_count: number
  round_active: boolean
  conditions_met: boolean
  status: RestartStatus
  last_action: string | null
  last_action_at: string | null
  last_error: string | null
}

type RestartResponse = { schedules: RestartSchedule[] }
type RestartPayload = {
  enabled: boolean
  daily_time: string
  msg_box: string
  no_online: boolean
  round_finished: boolean
}

function statusVariant(status: RestartStatus): "default" | "secondary" | "destructive" | "outline" {
  if (status === "error") return "destructive"
  if (status === "disabled") return "secondary"
  if (status === "waiting_conditions") return "outline"
  return "default"
}

function RestartConfigDialog({
  schedule,
  open,
  onOpenChange,
  onSave,
  pending,
}: {
  schedule: RestartSchedule | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (payload: RestartPayload) => void
  pending: boolean
}) {
  const [dailyTime, setDailyTime] = useState("05:30")
  const [message, setMessage] = useState("")
  const [noOnline, setNoOnline] = useState(true)
  const [roundFinished, setRoundFinished] = useState(true)

  useEffect(() => {
    if (!schedule) return
    setDailyTime(schedule.daily_time)
    setMessage(schedule.msg_box)
    setNoOnline(schedule.conditions.no_online)
    setRoundFinished(schedule.conditions.round_finished)
  }, [schedule])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Настройка авторестарта {schedule?.server}</DialogTitle>
          <DialogDescription>Время задаётся по MSK, отображение ближайшего запуска — по часовому поясу браузера.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="restart-time">Ежедневное время</Label>
            <Input id="restart-time" className="mt-1 max-w-40" type="time" value={dailyTime} onChange={(event) => setDailyTime(event.target.value)} />
          </div>
          <div>
            <Label htmlFor="restart-message">Текст msg_box</Label>
            <Textarea id="restart-message" className="mt-1" maxLength={500} value={message} onChange={(event) => setMessage(event.target.value)} />
            <p className="mt-1 text-xs text-muted-foreground">Отправляется за 15 секунд до автоматического ребута.</p>
          </div>
          <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
            <p className="text-sm font-medium">Условия запуска — достаточно любого выбранного</p>
            <label className="flex items-start gap-2 text-sm">
              <Checkbox className="mt-0.5" checked={noOnline} onCheckedChange={(value) => setNoOnline(value === true)} />
              <span>На сервере нет игроков</span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <Checkbox className="mt-0.5" checked={roundFinished} onCheckedChange={(value) => setRoundFinished(value === true)} />
              <span>Текущий раунд завершён</span>
            </label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button disabled={pending || !dailyTime || !message.trim()} onClick={() => onSave({ enabled: schedule?.enabled ?? false, daily_time: dailyTime, msg_box: message, no_online: noOnline, round_finished: roundFinished })}>
            Сохранить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RestartCard({
  schedule,
  onConfigure,
  onToggle,
  onPostpone,
  onRestartNow,
  pending,
}: {
  schedule: RestartSchedule
  onConfigure: () => void
  onToggle: (enabled: boolean) => void
  onPostpone: () => void
  onRestartNow: () => void
  pending: boolean
}) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const countdown = schedule.enabled ? formatRestartCountdown(schedule.next_run_at, now) : "—"
  const canAct = schedule.enabled && !pending && schedule.status !== "executing"

  return (
    <Card className="overflow-hidden py-0">
      <CardHeader className="flex flex-row items-center justify-between gap-3 border-b bg-muted/30 py-3">
        <div>
          <CardTitle className="text-sm font-semibold">{schedule.server}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">Ежедневно в {schedule.daily_time} MSK</p>
        </div>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>{schedule.enabled ? "Включён" : "Выключен"}</span>
          <Switch checked={schedule.enabled} disabled={pending} onCheckedChange={onToggle} aria-label={`Авторестарт ${schedule.server}`} />
        </label>
      </CardHeader>
      <CardContent className="space-y-4 py-4">
        <div className="rounded-lg border bg-background/40 p-4 text-center">
          <div className="font-mono text-4xl font-semibold tracking-tight tabular-nums">{countdown}</div>
          <div className="mt-1 text-xs text-muted-foreground">
            {schedule.next_run_at ? formatBrowserDate(schedule.next_run_at) : "Ближайший запуск не назначен"}
          </div>
          <Badge className="mt-3" variant={statusVariant(schedule.status)}>{restartStatusLabel(schedule.status)}</Badge>
        </div>

        <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
          <div className="flex items-center gap-2"><Users className="size-3.5" /> Онлайн: {schedule.online_count}</div>
          <div className="flex items-center gap-2"><RotateCcw className="size-3.5" /> Раунд: {schedule.round_active ? "идёт" : "завершён"}</div>
          <div className={schedule.conditions.no_online && schedule.online_count === 0 ? "text-success" : ""}>Условие онлайна: {schedule.conditions.no_online ? (schedule.online_count === 0 ? "выполнено" : "не выполнено") : "выключено"}</div>
          <div className={schedule.conditions.round_finished && !schedule.round_active ? "text-success" : ""}>Условие раунда: {schedule.conditions.round_finished ? (!schedule.round_active ? "выполнено" : "не выполнено") : "выключено"}</div>
        </div>

        {schedule.last_error && <p className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">{schedule.last_error}</p>}
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={onConfigure} disabled={pending}><Settings2 /> Настроить</Button>
          <Button variant="outline" size="sm" onClick={onPostpone} disabled={!canAct}><Clock3 /> Перенести на 1 час</Button>
          <Button variant="destructive" size="sm" onClick={onRestartNow} disabled={!canAct}><RotateCcw /> Ребут сейчас</Button>
        </div>
      </CardContent>
    </Card>
  )
}

export function ServerRestartSettings() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<RestartSchedule | null>(null)
  const [confirmAction, setConfirmAction] = useState<{ kind: "enable" | "postpone" | "restart"; schedule: RestartSchedule } | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ["server-restarts"],
    queryFn: () => api.get<RestartResponse>("/api/v1/admin/server-restarts"),
    refetchInterval: 5000,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["server-restarts"] })
  const update = useMutation({
    mutationFn: ({ server, payload }: { server: string; payload: RestartPayload }) => api.patch(`/api/v1/admin/server-restarts/${server}`, payload),
    onSuccess: () => { toast.success("Настройки авторестарта сохранены"); setEditing(null); setConfirmAction(null); refresh() },
    onError: (error: Error) => toast.error(error.message),
  })
  const postpone = useMutation({
    mutationFn: (server: string) => api.post(`/api/v1/admin/server-restarts/${server}/postpone`),
    onSuccess: () => { toast.success("Ребут перенесён на 1 час"); setConfirmAction(null); refresh() },
    onError: (error: Error) => toast.error(error.message),
  })
  const restart = useMutation({
    mutationFn: (server: string) => api.post<{ status: string; warning?: string | null }>(`/api/v1/admin/server-restarts/${server}/restart-now`),
    onSuccess: (result: { warning?: string | null }) => { toast.success(result.warning ? `Ребут запущен, msg_box не отправлен: ${result.warning}` : "Ребут запущен"); setConfirmAction(null); refresh() },
    onError: (error: Error) => toast.error(error.message),
  })

  if (isLoading || !data) return <div className="h-36 animate-pulse rounded-lg bg-muted/30" />

  const pending = update.isPending || postpone.isPending || restart.isPending
  return (
    <>
      <Card className="overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Авторестарт серверов</CardTitle></CardHeader>
        <CardContent className="space-y-3 py-4">
          <p className="text-xs text-muted-foreground">Управление ежедневным рестартом через NexusData. Авторестарт выключен по умолчанию. Перед включением отключите старые schedules в Pterodactyl.</p>
          <div className="grid gap-4 md:grid-cols-2">
            {data.schedules.map((schedule) => (
              <RestartCard
                key={schedule.server}
                schedule={schedule}
                pending={pending}
                onConfigure={() => setEditing(schedule)}
                onToggle={(enabled) => enabled
                  ? setConfirmAction({ kind: "enable", schedule })
                  : update.mutate({ server: schedule.server, payload: { enabled: false, daily_time: schedule.daily_time, msg_box: schedule.msg_box, no_online: schedule.conditions.no_online, round_finished: schedule.conditions.round_finished } })}
                onPostpone={() => setConfirmAction({ kind: "postpone", schedule })}
                onRestartNow={() => setConfirmAction({ kind: "restart", schedule })}
              />
            ))}
          </div>
        </CardContent>
      </Card>

      <RestartConfigDialog schedule={editing} open={Boolean(editing)} onOpenChange={(open) => !open && setEditing(null)} pending={update.isPending} onSave={(payload) => editing && update.mutate({ server: editing.server, payload })} />
      <ConfirmDialog
        open={Boolean(confirmAction)}
        onOpenChange={(open) => !open && setConfirmAction(null)}
        title={confirmAction?.kind === "restart" ? `Перезапустить ${confirmAction.schedule.server} сейчас?` : confirmAction?.kind === "enable" ? `Включить авторестарт ${confirmAction.schedule.server}?` : `Перенести ребут ${confirmAction?.schedule.server} на час?`}
        description={confirmAction?.kind === "restart" ? "Будет отправлен msg_box и немедленно вызван restart в Pterodactyl. Условия ожидания не применяются." : confirmAction?.kind === "enable" ? "Сервер начнёт автоматически перезапускаться по настроенному ежедневному расписанию и выбранным условиям." : "Ближайший автоматический запуск будет сдвинут ровно на один час."}
        confirmLabel={confirmAction?.kind === "restart" ? "Ребут сейчас" : confirmAction?.kind === "enable" ? "Включить" : "Перенести"}
        confirmDisabled={pending}
        onConfirm={() => {
          if (!confirmAction) return
          if (confirmAction.kind === "restart") restart.mutate(confirmAction.schedule.server)
          else if (confirmAction.kind === "postpone") postpone.mutate(confirmAction.schedule.server)
          else update.mutate({ server: confirmAction.schedule.server, payload: { enabled: true, daily_time: confirmAction.schedule.daily_time, msg_box: confirmAction.schedule.msg_box, no_online: confirmAction.schedule.conditions.no_online, round_finished: confirmAction.schedule.conditions.round_finished } })
        }}
      />
    </>
  )
}
