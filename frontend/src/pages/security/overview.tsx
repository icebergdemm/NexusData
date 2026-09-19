import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { cn } from "@/lib/utils"
import { securityDisplayName, securitySecondaryName } from "@/lib/security-display"

interface SecuritySession {
  id: number
  admin_id: number
  admin_name: string | null
  admin_display_name: string | null
  created_at: string | null
  expires_at: string | null
  last_seen_at: string | null
  last_seen_ip: string | null
  user_agent: string | null
  auth_type: string | null
  is_current: boolean
}

interface AttentionItem {
  action: string
  count: number
  last_at: string
  href: string
}

interface RecentAction {
  id: number
  admin_id: number
  admin_name: string | null
  admin_display_name: string | null
  action: string
  count: number
  details: Array<Record<string, unknown>>
  created_at: string | null
  reason: string | null
  severity: string | null
}

interface OverviewResponse {
  attention: AttentionItem[]
  active_sessions: SecuritySession[]
  recent_actions: RecentAction[]
}

const actionLabels: Record<string, string> = {
  csrf_failure: "Ошибка проверки CSRF",
  login_key_failure: "Неудачный вход по ключу",
  new_device: "Вход с нового устройства",
  login: "Вход в панель",
  logout: "Выход из панели",
  login_key_create: "Создан ключ входа",
  login_key_revoke: "Отозван ключ входа",
  revoke_session: "Отозвана сессия",
  revoke_all_sessions: "Отозваны сессии",
  web_lockdown_change: "Изменён шатдаун",
  task_delete: "Удаление задачи",
  moderation_kick: "Кик игрока",
  "moderation.kick": "Кик игрока",
  "moderation.ban": "Блокировка игрока",
}

export function SecurityOverviewPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ["security-overview"],
    queryFn: () => api.get<OverviewResponse>("/api/v1/admin/security/overview"),
    refetchInterval: 30_000,
  })
  const revoke = useMutation({
    mutationFn: (id: number) => api.post(`/api/v1/admin/sessions/${id}/revoke`),
    onSuccess: () => {
      toast.success("Сессия отозвана")
      queryClient.invalidateQueries({ queryKey: ["security-overview"] })
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (isLoading) return <div className="border-y border-border py-10 text-center text-sm text-muted-foreground">Загрузка состояния доступа…</div>
  if (isError || !data) return <div className="border border-destructive/30 p-5 text-sm text-destructive">Не удалось загрузить состояние безопасности.</div>

  return (
    <div className="space-y-8">
      <section aria-labelledby="security-attention-title" className="rounded-lg border bg-card/30 p-4 sm:p-5">
        <SectionHeading id="security-attention-title" title="Требует внимания" />
        {data.attention.length ? (
          <div className="divide-y divide-border border-y border-border">
            {data.attention.slice(0, 5).map((item) => <div key={item.action} className="flex flex-wrap items-center gap-3 py-3">
              <span className="size-2 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
              <div className="min-w-0 flex-1 text-sm"><span className="font-medium">{attentionLabel(item)}</span><span className="ml-2 text-xs text-muted-foreground">{formatBrowserDate(item.last_at)}</span></div>
              <Button variant="ghost" size="sm" asChild><Link to={item.href}>Открыть в аудите</Link></Button>
            </div>)}
            {data.attention.length > 5 && <div className="py-2 text-xs text-muted-foreground">Ещё {data.attention.length - 5}</div>}
          </div>
        ) : (
          <div className="flex items-center gap-3 border-y border-border py-3 text-sm"><span className="size-2 rounded-full bg-emerald-500" aria-hidden="true" />Всё спокойно. За последний час нет ошибок входа и подозрительных действий.</div>
        )}
      </section>

      <section aria-labelledby="security-sessions-title" className="rounded-lg border bg-card/30 p-4 sm:p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2"><SectionHeading id="security-sessions-title" title="Сейчас в панели" /><Link className="text-sm text-primary underline-offset-4 hover:underline" to="/security/sessions">Все сессии</Link></div>
        <div className="divide-y divide-border border-y border-border">
          {data.active_sessions.map((session) => {
            const displayName = securityDisplayName(session.admin_display_name, session.admin_name, `#${session.admin_id}`)
            const nexusName = securitySecondaryName(session.admin_display_name, session.admin_name)
            return <div key={session.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
              <div className="min-w-0 flex-1"><div className="font-medium">{displayName} {session.is_current && <span className="ml-1 text-xs text-primary">это вы</span>}</div>{nexusName && <div className="text-xs text-muted-foreground">Имя Nexus Data: {nexusName}</div>}<div className="text-xs text-muted-foreground">с {formatTime(session.created_at)}, до {formatDate(session.expires_at)} · {authLabel(session.auth_type)}</div></div>
              {!session.is_current && <Button size="sm" variant="destructive" onClick={() => revoke.mutate(session.id)} disabled={revoke.isPending}>Отозвать</Button>}
            </div>
          })}
          {!data.active_sessions.length && <div className="py-5 text-sm text-muted-foreground">Активных сессий нет.</div>}
        </div>
      </section>

      <section aria-labelledby="security-actions-title" className="rounded-lg border bg-card/30 p-4 sm:p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2"><SectionHeading id="security-actions-title" title="Последние действия" /><Link className="text-sm text-primary underline-offset-4 hover:underline" to="/security/audit">Открыть весь аудит</Link></div>
        <div className="divide-y divide-border border-y border-border">
          {data.recent_actions.map((action) => {
            const displayName = securityDisplayName(action.admin_display_name, action.admin_name, `#${action.admin_id}`)
            const nexusName = securitySecondaryName(action.admin_display_name, action.admin_name)
            const details = activityDetails(action)
            return <Link key={`${action.id}-${action.action}`} to={`/security/audit?event=${action.id}`} className="flex gap-3 py-3 text-sm transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", activityDot(action))} aria-hidden="true" />
              <div className="min-w-0 flex-1"><div><span className="font-medium">{displayName}</span><span className="text-muted-foreground"> — {activityLabel(action)}</span><span className="ml-2 text-xs text-muted-foreground">{formatBrowserDate(action.created_at)}</span></div>{nexusName && <div className="text-xs text-muted-foreground">Имя Nexus Data: {nexusName}</div>}{details && <div className="mt-1 text-xs text-muted-foreground">{details}</div>}</div>
            </Link>
          })}
          {!data.recent_actions.length && <div className="py-5 text-sm text-muted-foreground">Важных действий пока нет.</div>}
        </div>
      </section>
    </div>
  )
}

function SectionHeading({ id, title }: { id: string; title: string }) {
  return <h2 id={id} className="mb-2 text-sm font-semibold tracking-wide">{title}</h2>
}

function attentionLabel(item: AttentionItem) {
  const label = actionLabels[item.action] ?? item.action
  return item.count > 1 ? `${item.count} события: ${label.toLowerCase()}` : label
}

function activityLabel(action: RecentAction) {
  const label = actionLabels[action.action] ?? action.action
  return action.count > 1 ? `${label.toLowerCase()} (${action.count})` : label
}

function activityDetails(action: RecentAction) {
  const taskIds = action.details.map((item) => item.task_id).filter((id): id is number | string => typeof id === "number" || typeof id === "string")
  if (taskIds.length) return `Задачи: ${taskIds.map((id) => `#${id}`).join(", ")}`
  return action.reason ?? null
}

function activityDot(action: RecentAction) {
  const value = action.action.toLowerCase()
  if (/(delete|remove|revoke|void|kick|ban|unban)/.test(value)) return "bg-red-500"
  if (/(create|add|grant)/.test(value)) return "bg-emerald-500"
  if (/(login|logout|auth|csrf)/.test(value)) return "bg-muted-foreground"
  return "bg-amber-400"
}

function authLabel(value: string | null) {
  if (value === "discord_oauth") return "Discord"
  if (value === "login_key" || value === "slogin") return "slogin"
  return "браузер"
}

function formatTime(value: string | null) {
  return formatBrowserDate(value, { hour: "2-digit", minute: "2-digit" })
}

function formatDate(value: string | null) {
  return formatBrowserDate(value, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
}
