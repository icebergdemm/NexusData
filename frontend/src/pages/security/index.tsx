import { useState } from "react"
import { Link, Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Search } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { cn } from "@/lib/utils"
import { UsersPage } from "@/pages/users"
import { SessionsPage } from "@/pages/sessions"
import { AuditPage } from "@/pages/audit"

interface LockdownState {
  enabled: boolean
  message: string | null
  updated_at: string | null
  active_non_superuser_sessions: number
}

const tabs = [
  { label: "Обзор", to: "/security" },
  { label: "Администраторы", to: "/security/admins" },
  { label: "Сессии", to: "/security/sessions" },
  { label: "Аудит", to: "/security/audit" },
]

export function SecurityLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { data: admin, isLoading: adminLoading } = useCurrentAdmin()
  const queryClient = useQueryClient()
  const [quickSearch, setQuickSearch] = useState(searchParams.get("q") ?? "")
  const [confirmState, setConfirmState] = useState<boolean | null>(null)
  const { data: lockdown } = useQuery({
    queryKey: ["security-lockdown"],
    queryFn: () => api.get<LockdownState>("/api/v1/admin/security/lockdown"),
    enabled: !!admin?.is_superuser,
  })
  const updateLockdown = useMutation({
    mutationFn: (enabled: boolean) => api.post<LockdownState & { revoked_sessions: number }>("/api/v1/admin/security/lockdown", { enabled }),
    onSuccess: (state) => {
      queryClient.setQueryData(["security-lockdown"], state)
      queryClient.invalidateQueries({ queryKey: ["security-overview"] })
      toast.success(state.enabled ? `Аварийная блокировка включена. Отозвано сессий обычных пользователей: ${state.revoked_sessions}. Суперпользователи остаются подключены.` : "Аварийная блокировка выключена")
      setConfirmState(null)
    },
    onError: (error: Error) => toast.error(error.message),
  })

  if (!adminLoading && !admin?.is_superuser) {
    return <div className="mx-auto max-w-xl border border-destructive/30 p-6 text-sm text-destructive">Раздел «Безопасность» доступен только суперпользователям.</div>
  }

  const submitSearch = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const value = quickSearch.trim()
    navigate(value ? `/security/audit?q=${encodeURIComponent(value)}` : "/security/audit")
  }

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5">
      <header className="border-b border-border/80 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div>
              <h1 className="text-xl font-semibold tracking-tight">Безопасность</h1>
              <p className="text-sm text-muted-foreground">Кто в панели и что там происходило</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            <form onSubmit={submitSearch} className="relative w-full sm:w-72">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
              <Input value={quickSearch} onChange={(event) => setQuickSearch(event.target.value)} placeholder="Админ, Discord ID или действие" className="h-9 pl-8" aria-label="Быстрый поиск по безопасности" />
            </form>
            {admin?.is_superuser && (
              <div className="flex items-center gap-2 border-l border-border pl-3 text-xs">
                <span className={cn(lockdown?.enabled ? "text-destructive" : "text-muted-foreground")}>Шатдаун</span>
                <Switch checked={!!lockdown?.enabled} disabled={!lockdown || updateLockdown.isPending} onCheckedChange={setConfirmState} aria-label="Аварийная блокировка сайта" />
              </div>
            )}
          </div>
        </div>
        <nav aria-label="Разделы безопасности" className="mt-5 flex gap-1 overflow-x-auto">
          {tabs.map((tab) => {
            const active = tab.to === "/security" ? location.pathname === tab.to : location.pathname.startsWith(tab.to)
            return <Link key={tab.to} to={tab.to} className={cn("whitespace-nowrap border-b-2 px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", active ? "border-primary text-foreground" : "border-transparent")}>{tab.label}</Link>
          })}
        </nav>
      </header>

      <Outlet />

      <ConfirmDialog
        open={confirmState !== null}
        onOpenChange={(open) => !open && setConfirmState(null)}
        title={confirmState ? "Включить аварийную блокировку?" : "Выключить аварийную блокировку?"}
        description={confirmState ? `Обычные пользователи потеряют доступ к сайту. Будет отозвано активных сессий обычных пользователей: ${lockdown?.active_non_superuser_sessions ?? "неизвестно"}. Вы останетесь подключены как суперпользователь. Сервисные API-ключи продолжат работать.` : "Обычные пользователи снова получат доступ к сайту."}
        confirmLabel={confirmState ? "Включить шатдаун" : "Выключить шатдаун"}
        confirmDisabled={updateLockdown.isPending}
        onConfirm={() => confirmState !== null && updateLockdown.mutate(confirmState)}
      />
    </div>
  )
}

export function SecurityAdminsPage() {
  return <SecurityEmbeddedPage path="admins" />
}

export function SecuritySessionsPage() {
  return <SecurityEmbeddedPage path="sessions" />
}

export function SecurityAuditPage() {
  return <SecurityEmbeddedPage path="audit" />
}

function SecurityEmbeddedPage({ path }: { path: "admins" | "sessions" | "audit" }) {
  if (path === "admins") {
    return <UsersPageEmbedded />
  }
  if (path === "sessions") {
    return <SessionsPageEmbedded />
  }
  return <AuditPageEmbedded />
}

function UsersPageEmbedded() {
  return <UsersPage embedded />
}

function SessionsPageEmbedded() {
  return <SessionsPage embedded />
}

function AuditPageEmbedded() {
  return <AuditPage embedded />
}
