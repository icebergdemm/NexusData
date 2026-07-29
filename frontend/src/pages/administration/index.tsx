import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Line } from "react-chartjs-2"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { AlertTriangle, Users, Clock3, Timer, Gauge } from "lucide-react"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { baseChartOptions, lineDatasetDefaults, CHART_COLORS } from "@/lib/chart-theme"

interface AdministrationResponse {
  period: string
  unique_players: number; total_sessions: number; avg_session_mins: number; peak_concurrent: number
  chart_labels: string[]; peak_values: number[]; avg_values: number[]; min_values: number[]
  top_players: { player_id: number; player_last_time_name: string | null; forced_display_name: string | null; total_secs: number; sessions: number }[]
  editable_cfg: Record<string, string | number | boolean>
  twinks: unknown[]; twink_page: number; twink_pages: number; twink_total: number
}

const CFG_FIELDS: { key: string; label: string; type?: "checkbox" }[] = [
  { key: "discord_client_id", label: "Discord Client ID" },
  { key: "discord_client_secret", label: "Discord Client Secret" },
  { key: "discord_redirect_uri", label: "Discord Redirect URI" },
  { key: "discord_trap_webhook_url", label: "Discord Trap Webhook" },
  { key: "sub_projects_webhook_url", label: "Sub-projects Webhook" },
  { key: "sub_projects_discord_channel_id", label: "Sub-projects Discord Channel ID" },
  { key: "allowed_ips", label: "Allowed IPs (через запятую)" },
  { key: "cors_origins", label: "CORS Origins (через запятую)" },
  { key: "rate_limit_requests", label: "Rate limit: запросов" },
  { key: "rate_limit_window", label: "Rate limit: окно (сек)" },
  { key: "sftp_server_1_name", label: "SFTP 1: имя" },
  { key: "sftp_server_1_host", label: "SFTP 1: хост" },
  { key: "sftp_server_1_port", label: "SFTP 1: порт" },
  { key: "sftp_server_1_username", label: "SFTP 1: пользователь" },
  { key: "sftp_server_1_password", label: "SFTP 1: пароль" },
  { key: "sftp_server_1_remote_dir", label: "SFTP 1: удалённая папка" },
  { key: "sftp_server_2_name", label: "SFTP 2: имя" },
  { key: "sftp_server_2_host", label: "SFTP 2: хост" },
  { key: "sftp_server_2_port", label: "SFTP 2: порт" },
  { key: "sftp_server_2_username", label: "SFTP 2: пользователь" },
  { key: "sftp_server_2_password", label: "SFTP 2: пароль" },
  { key: "sftp_server_2_remote_dir", label: "SFTP 2: удалённая папка" },
]

// Отдельно от CFG_FIELDS: эти поля бэкенд отдаёт и принимает только для
// суперпользователей (routes/admin.py: ONLYOFFICE_CFG_FIELDS) — один из них
// секрет, общий с самим Document Server. Рендерим их отдельным блоком, а не
// подмешиваем в общий грид, чтобы не показывать пустые поля не-суперюзерам
// (бэкенд просто не пришлёт эти ключи в editable_cfg для них).
const SUPERUSER_CFG_FIELDS: { key: string; label: string }[] = [
  { key: "onlyoffice_document_server_url", label: "ONLYOFFICE: URL Document Server" },
  { key: "onlyoffice_callback_base_url", label: "ONLYOFFICE: базовый URL NexusData (для колбэков)" },
  { key: "onlyoffice_jwt_secret", label: "ONLYOFFICE: JWT-секрет" },
  { key: "pterodactyl_panel_url", label: "Pterodactyl: URL панели" },
  { key: "pterodactyl_api_key", label: "Pterodactyl: Client API ключ" },
  { key: "sub_projects_test_server_uuid", label: "Sub-проекты: UUID тестового сервера (Сервер 2 / ASPER2)" },
]

/** Настройки консольной команды `update` (обновление кода из git).
 *  Тоже только для суперпользователей: подменив каталог, remote или ветку,
 *  можно заставить сервер выполнить чужой код. */
const GIT_UPDATE_FIELDS: { key: string; label: string; placeholder?: string; hint?: string }[] = [
  { key: "git_update_repo_dir", label: "Каталог репозитория", placeholder: "пусто — каталог приложения" },
  { key: "git_update_remote", label: "Remote", placeholder: "origin" },
  { key: "git_update_branch", label: "Ветка", placeholder: "пусто — текущая" },
]

const GIT_UPDATE_TOGGLES: { key: string; label: string; hint: string }[] = [
  {
    key: "git_update_build_frontend",
    label: "Пересобирать фронтенд",
    hint: "Только если в обновлении менялось что-то во frontend/. Без сборки сайт останется на старой версии — dist/ в репозитории нет.",
  },
  {
    key: "git_update_restart_backend",
    label: "Перезапускать backend после обновления",
    hint: "Python-код перечитывается только при рестарте. Рестарт рвёт открытые запросы, поэтому по умолчанию выключено — разово это делает команда «update restart».",
  },
]

export function AdministrationPage() {
  const [period, setPeriod] = useState("day")
  const [cfg, setCfg] = useState<Record<string, string>>({})
  const queryClient = useQueryClient()
  const { data: me } = useCurrentAdmin()

  const { data } = useQuery({
    queryKey: ["administration", period],
    queryFn: () => api.get<AdministrationResponse>(`/api/v1/admin/administration?period=${period}`),
  })

  useEffect(() => {
    if (data?.editable_cfg) {
      setCfg(Object.fromEntries(Object.entries(data.editable_cfg).map(([k, v]) => [k, String(v ?? "")])))
    }
  }, [data])

  const saveConfig = useMutation({
    mutationFn: () => api.post("/api/v1/admin/administration/config", cfg),
    onSuccess: () => { toast.success("Конфигурация сохранена. Перезапустите сервер для применения части настроек."); queryClient.invalidateQueries({ queryKey: ["administration"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div className="space-y-4">
      <Tabs value={period} onValueChange={setPeriod}>
        <TabsList>
          <TabsTrigger value="day">День</TabsTrigger>
          <TabsTrigger value="week">Неделя</TabsTrigger>
          <TabsTrigger value="month">Месяц</TabsTrigger>
          <TabsTrigger value="all">Всё время</TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="grid grid-cols-4 gap-4">
        <StatTile label="Уникальных игроков" value={data?.unique_players ?? "-"} icon={Users} accent="info" />
        <StatTile label="Сессий" value={data?.total_sessions ?? "-"} icon={Clock3} accent="primary" />
        <StatTile label="Ср. сессия (мин)" value={data?.avg_session_mins ?? "-"} icon={Timer} accent="success" />
        <StatTile label="Пик одновременно" value={data?.peak_concurrent ?? "-"} icon={Gauge} accent="warning" />
      </div>

      <Card className="overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Онлайн за период</CardTitle></CardHeader>
        <CardContent className="pt-4" style={{ height: 276 }}>
          <Line
            options={baseChartOptions}
            data={{
              labels: data?.chart_labels ?? [],
              datasets: [
                { label: "Пик", data: data?.peak_values ?? [], borderColor: CHART_COLORS[1], ...lineDatasetDefaults },
                { label: "Среднее", data: data?.avg_values ?? [], borderColor: CHART_COLORS[0], backgroundColor: CHART_COLORS[0] + "26", fill: true, ...lineDatasetDefaults },
              ],
            }}
          />
        </CardContent>
      </Card>

      <Card className="overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Топ активных игроков</CardTitle></CardHeader>
        <CardContent className="py-4">
          <Table>
            <TableHeader><TableRow><TableHead>Игрок</TableHead><TableHead>Часов</TableHead><TableHead>Сессий</TableHead></TableRow></TableHeader>
            <TableBody>
              {data?.top_players.map((p) => (
                <TableRow key={p.player_id}>
                  <TableCell
                    className="font-medium"
                    title={p.forced_display_name && p.player_last_time_name ? `Настоящий ник: ${p.player_last_time_name}` : undefined}
                  >
                    {p.forced_display_name || p.player_last_time_name || `#${p.player_id}`}
                  </TableCell>
                  <TableCell>{(p.total_secs / 3600).toFixed(1)}</TableCell>
                  <TableCell className="text-muted-foreground">{p.sessions}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card className="overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Конфигурация (.env)</CardTitle></CardHeader>
        <CardContent className="space-y-4 py-4">
          <div className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Здесь отображаются секреты в открытом виде (пароли SFTP, Discord client secret). Доступ только для суперпользователей.</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {CFG_FIELDS.map((f) => (
              <div key={f.key}>
                <Label className="text-xs text-muted-foreground">{f.label}</Label>
                <Input className="mt-1" value={cfg[f.key] ?? ""} onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))} />
              </div>
            ))}
          </div>
          <Button onClick={() => saveConfig.mutate()}>Сохранить конфигурацию</Button>
        </CardContent>
      </Card>

      {me?.is_superuser && (
        <Card className="overflow-hidden py-0">
          <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">ONLYOFFICE / Pterodactyl</CardTitle></CardHeader>
          <CardContent className="space-y-4 py-4">
            <div className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Настройки интеграции с Document Server и Pterodactyl (включая API-ключи). Доступ только для суперпользователей — раздел не отображается остальным.</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {SUPERUSER_CFG_FIELDS.map((f) => (
                <div key={f.key}>
                  <Label className="text-xs text-muted-foreground">{f.label}</Label>
                  <Input className="mt-1" value={cfg[f.key] ?? ""} onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))} />
                </div>
              ))}
            </div>
            <Button onClick={() => saveConfig.mutate()}>Сохранить конфигурацию</Button>
          </CardContent>
        </Card>
      )}

      {me?.is_superuser && (
        <Card className="overflow-hidden py-0">
          <CardHeader className="border-b bg-muted/30 py-3">
            <CardTitle className="text-sm font-semibold">Обновление кода из git</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 py-4">
            <div className="flex items-start gap-2.5 rounded-lg border border-info/30 bg-info/10 p-3 text-sm text-info">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                Настройки консольной команды <code className="font-mono">update</code>: она делает{" "}
                <code className="font-mono">git pull --ff-only</code> и, при изменениях во frontend/, пересобирает сайт.
                Саму команду набирают в консоли процесса (вкладка «Консоль» в Pterodactyl) — из веба обновление
                не запускается намеренно.
              </span>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {GIT_UPDATE_FIELDS.map((f) => (
                <div key={f.key}>
                  <Label className="text-xs text-muted-foreground">{f.label}</Label>
                  <Input
                    className="mt-1"
                    placeholder={f.placeholder}
                    value={cfg[f.key] ?? ""}
                    onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))}
                  />
                </div>
              ))}
            </div>
            <div className="space-y-2">
              {GIT_UPDATE_TOGGLES.map((f) => (
                <label key={f.key} className="flex items-start gap-2.5 text-sm">
                  <Checkbox
                    className="mt-0.5"
                    checked={cfg[f.key] === "true" || cfg[f.key] === "True"}
                    onCheckedChange={(v) => setCfg((c) => ({ ...c, [f.key]: v ? "true" : "false" }))}
                  />
                  <span>
                    {f.label}
                    <span className="block text-xs text-muted-foreground">{f.hint}</span>
                  </span>
                </label>
              ))}
            </div>
            <div className="rounded-lg border bg-muted/30 p-3 font-mono text-xs">
              <div>update            — забрать код и пересобрать при необходимости</div>
              <div>update restart    — то же и сразу перезапустить backend</div>
              <div>update norestart  — то же без перезапуска</div>
              <div>update status     — каталог, ветка и текущая ревизия</div>
            </div>
            <Button onClick={() => saveConfig.mutate()}>Сохранить конфигурацию</Button>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

const ACCENT_CLASSES: Record<string, string> = {
  primary: "bg-primary/15 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  info: "bg-info/15 text-info",
}

function StatTile({ label, value, icon: Icon, accent }: { label: string; value: number | string; icon: typeof Users; accent: string }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${ACCENT_CLASSES[accent]}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <div className="text-2xl font-bold leading-none">{value}</div>
          <div className="mt-1 text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  )
}
