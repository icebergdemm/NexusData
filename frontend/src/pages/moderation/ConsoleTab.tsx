/** Консоль игрового сервера (Pterodactyl) — прямой доступ к серверной
 *  консоли, поэтому это самая чувствительная вкладка страницы. Доступ на
 *  КАЖДЫЙ сервер проверяется отдельным правом (moderation:console_server1/2,
 *  см. routes/moderation.py) — сюда выведены только те серверы, к которым у
 *  текущего админа реально есть право, а не заглушки с "нет доступа".
 *
 *  Живого вывода консоли здесь нет намеренно (см. докстринг серверной части)
 *  — только отправка команд и журнал того, что и кем было отправлено. Это
 *  сознательное сужение объёма: полноценная трансляция вывода потребовала бы
 *  отдельного WebSocket-моста через бэкенд (чтобы токен Pterodactyl не
 *  попадал в браузер) — не тот объём, что можно безопасно сделать за раз.
 */
import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Terminal, Send, RefreshCw } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, ApiError } from "@/lib/api"

interface ConsolePermissions { servers: { server: string; enabled: boolean }[]; can_manage_settings: boolean }
interface ConsoleSettings { panel_url: string; api_key: string; server1_uuid: string; server2_uuid: string }
interface ConsoleStatus { state: string | null; cpu_percent: number | null; memory_bytes: number | null; memory_limit_bytes: number | null }
interface ConsoleHistoryEntry {
  id: number; admin_name: string; command: string | null; failed: boolean; error: string | null; created_at: string | null
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "-"
}

function fmtBytes(n: number | null): string {
  if (n == null) return "-"
  return `${(n / 1024 / 1024).toFixed(0)} МБ`
}

const STATE_LABEL: Record<string, string> = {
  running: "🟢 Запущен", starting: "🟡 Запускается", stopping: "🟡 Останавливается", offline: "⚪ Остановлен",
}

export function ConsoleTab() {
  const queryClient = useQueryClient()
  const [activeServer, setActiveServer] = useState<string | null>(null)
  const [command, setCommand] = useState("")
  const [settings, setSettings] = useState<ConsoleSettings | null>(null)

  const { data: perms } = useQuery({
    queryKey: ["console-permissions"],
    queryFn: () => api.get<ConsolePermissions>("/api/v1/admin/moderation/console/permissions"),
  })
  const allowedServers = (perms?.servers ?? []).filter((s) => s.enabled).map((s) => s.server)
  const { data: settingsData } = useQuery({
    queryKey: ["console-settings"],
    queryFn: () => api.get<ConsoleSettings>("/api/v1/admin/moderation/console/settings"),
    enabled: !!perms?.can_manage_settings,
  })
  useEffect(() => { if (settingsData) setSettings(settingsData) }, [settingsData])
  const saveSettings = useMutation({
    mutationFn: () => api.put("/api/v1/admin/moderation/console/settings", settings),
    onSuccess: () => { toast.success("Настройки консоли сохранены"); queryClient.invalidateQueries({ queryKey: ["console-settings"] }) },
    onError: (e: ApiError) => toast.error(e.message),
  })

  useEffect(() => {
    if (!activeServer && allowedServers.length) setActiveServer(allowedServers[0])
  }, [allowedServers, activeServer])

  const { data: status } = useQuery({
    queryKey: ["console-status", activeServer],
    queryFn: () => api.get<ConsoleStatus>(`/api/v1/admin/moderation/console/${activeServer}/status`),
    enabled: !!activeServer,
    refetchInterval: 10000,
    retry: false,
  })

  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ["console-history", activeServer],
    queryFn: () => api.get<{ history: ConsoleHistoryEntry[] }>(`/api/v1/admin/moderation/console/${activeServer}/history`),
    enabled: !!activeServer,
    refetchInterval: 8000,
  })

  const sendMutation = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/moderation/console/${activeServer}/command`, { command }),
    onSuccess: () => {
      setCommand("")
      queryClient.invalidateQueries({ queryKey: ["console-history", activeServer] })
    },
    onError: (e: ApiError) => toast.error(e.message),
  })

  if (!perms) return null

  if (!allowedServers.length) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-muted-foreground">
          Нет доступа ни к одной консоли — право выдаётся отдельно на каждый сервер
          («Модерирование → Консоль Сервера 1/2» в правах пользователей ND).
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      {perms.can_manage_settings && settings && (
        <Card className="border-warning/40">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-sm">Настройки подключения Pterodactyl</CardTitle>
            <Button size="sm" onClick={() => saveSettings.mutate()} disabled={saveSettings.isPending}>Сохранить</Button>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2"><label className="mb-1 block text-xs text-muted-foreground">URL панели</label><Input value={settings.panel_url} placeholder="https://panel.example" onChange={(e) => setSettings({ ...settings, panel_url: e.target.value })} /></div>
            <div className="sm:col-span-2"><label className="mb-1 block text-xs text-muted-foreground">Client API ключ</label><Input type="password" value={settings.api_key} placeholder="Новый ключ или оставьте маску" onChange={(e) => setSettings({ ...settings, api_key: e.target.value })} /></div>
            <div><label className="mb-1 block text-xs text-muted-foreground">UUID ASPER1</label><Input value={settings.server1_uuid} onChange={(e) => setSettings({ ...settings, server1_uuid: e.target.value })} /></div>
            <div><label className="mb-1 block text-xs text-muted-foreground">UUID ASPER2</label><Input value={settings.server2_uuid} onChange={(e) => setSettings({ ...settings, server2_uuid: e.target.value })} /></div>
          </CardContent>
        </Card>
      )}
      <Tabs value={activeServer ?? allowedServers[0]} onValueChange={setActiveServer}>
        <TabsList>
          {allowedServers.map((s) => <TabsTrigger key={s} value={s}>{s}</TabsTrigger>)}
        </TabsList>
      </Tabs>

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="flex items-center gap-1.5 text-sm"><Terminal className="size-4" /> Консоль {activeServer}</CardTitle>
          {status?.state && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="outline">{STATE_LABEL[status.state] ?? status.state}</Badge>
              {status.cpu_percent != null && <span>CPU: {status.cpu_percent.toFixed(0)}%</span>}
              {status.memory_bytes != null && <span>RAM: {fmtBytes(status.memory_bytes)} / {fmtBytes(status.memory_limit_bytes)}</span>}
            </div>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && command.trim() && !sendMutation.isPending && sendMutation.mutate()}
              placeholder="Команда консоли…"
              className="font-mono"
            />
            <Button onClick={() => sendMutation.mutate()} disabled={!command.trim() || sendMutation.isPending}>
              <Send className="size-3.5" /> Отправить
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Live-вывод консоли здесь не отображается — только журнал отправленных команд ниже.
            Полный вывод смотрите в самой панели Pterodactyl.
          </p>

          <div className="rounded-md border">
            <div className="flex items-center justify-between border-b bg-muted/30 px-3 py-1.5 text-xs font-medium text-muted-foreground">
              <span>Журнал команд</span>
              <Button size="icon-xs" variant="ghost" onClick={() => queryClient.invalidateQueries({ queryKey: ["console-history", activeServer] })}>
                <RefreshCw className="size-3.5" />
              </Button>
            </div>
            <div className="max-h-80 overflow-y-auto">
              {historyLoading && <div className="p-3 text-center text-xs text-muted-foreground">Загрузка…</div>}
              {!historyLoading && !history?.history.length && (
                <div className="p-3 text-center text-xs text-muted-foreground">Команд ещё не отправляли</div>
              )}
              {history?.history.map((h) => (
                <div key={h.id} className={`flex items-start gap-2 border-b px-3 py-1.5 text-xs last:border-0 ${h.failed ? "bg-destructive/5" : ""}`}>
                  <span className="shrink-0 text-muted-foreground">{fmtDate(h.created_at)}</span>
                  <span className="shrink-0 font-medium text-info">{h.admin_name}</span>
                  <code className="min-w-0 flex-1 truncate font-mono">{h.command}</code>
                  {h.failed && <Badge variant="destructive" className="shrink-0 text-[10px]" title={h.error ?? undefined}>ошибка</Badge>}
                </div>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
