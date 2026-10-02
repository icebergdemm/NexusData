import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { AlertTriangle } from "lucide-react"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { ServerRestartSettings } from "./server-restarts"
import { LobbyAdvertisementSettings } from "./lobby-advertisements"
import { ExternalWorkersPanel } from "./external-workers"

// Статистика (онлайн за период, топ игроков) отсюда убрана — она дублировала
// страницу «Аналитика» неоптимизированной версией расчётов (полный скан
// player_sessions без индекса + вложенные Python-циклы прямо в теле
// async-обработчика, БЕЗ run_in_threadpool) и была основной причиной, по
// которой открытие этой страницы на несколько секунд подвешивало весь сайт
// целиком. См. routes/admin.py::api_admin_administration.
interface AdministrationResponse {
  editable_cfg: Record<string, string | number | boolean>
}

interface CommunicationRetentionResponse { retention_days: number; default_days: number; max_days: number }

const CFG_FIELDS: { key: string; label: string; type?: "checkbox" | "password" }[] = [
  { key: "discord_client_id", label: "Discord Client ID" },
  { key: "discord_client_secret", label: "Discord Client Secret", type: "password" },
  { key: "discord_redirect_uri", label: "Discord Redirect URI" },
  { key: "discord_trap_webhook_url", label: "Discord Trap Webhook", type: "password" },
  { key: "sub_projects_webhook_url", label: "Sub-projects Webhook", type: "password" },
  { key: "sub_projects_discord_channel_id", label: "Sub-projects Discord Channel ID" },
  { key: "public_site_url", label: "Публичный URL сайта (для картинок в эмбедах Discord)" },
  { key: "allowed_ips", label: "Allowed IPs (через запятую)" },
  { key: "cors_origins", label: "CORS Origins (через запятую)" },
  { key: "rate_limit_requests", label: "Rate limit: запросов" },
  { key: "rate_limit_window", label: "Rate limit: окно (сек)" },
  { key: "sftp_server_1_name", label: "SFTP 1: имя" },
  { key: "sftp_server_1_host", label: "SFTP 1: хост" },
  { key: "sftp_server_1_port", label: "SFTP 1: порт" },
  { key: "sftp_server_1_username", label: "SFTP 1: пользователь" },
  { key: "sftp_server_1_password", label: "SFTP 1: пароль", type: "password" },
  { key: "sftp_server_1_remote_dir", label: "SFTP 1: удалённая папка" },
  { key: "sftp_server_2_name", label: "SFTP 2: имя" },
  { key: "sftp_server_2_host", label: "SFTP 2: хост" },
  { key: "sftp_server_2_port", label: "SFTP 2: порт" },
  { key: "sftp_server_2_username", label: "SFTP 2: пользователь" },
  { key: "sftp_server_2_password", label: "SFTP 2: пароль", type: "password" },
  { key: "sftp_server_2_remote_dir", label: "SFTP 2: удалённая папка" },
]

// Отдельно от CFG_FIELDS: эти поля бэкенд отдаёт и принимает только для
// суперпользователей (routes/admin.py: ONLYOFFICE_CFG_FIELDS) — один из них
// секрет, общий с самим Document Server. Рендерим их отдельным блоком, а не
// подмешиваем в общий грид, чтобы не показывать пустые поля не-суперюзерам
// (бэкенд просто не пришлёт эти ключи в editable_cfg для них).
const SUPERUSER_CFG_FIELDS: { key: string; label: string; type?: "password" }[] = [
  { key: "onlyoffice_document_server_url", label: "ONLYOFFICE: URL Document Server" },
  { key: "onlyoffice_callback_base_url", label: "ONLYOFFICE: базовый URL NexusData (для колбэков)" },
  { key: "onlyoffice_jwt_secret", label: "ONLYOFFICE: JWT-секрет", type: "password" },
  { key: "pterodactyl_panel_url", label: "Pterodactyl: URL панели" },
  { key: "pterodactyl_api_key", label: "Pterodactyl: Client API ключ", type: "password" },
  { key: "sub_projects_test_server_uuid", label: "Sub-проекты: UUID тестового сервера (Сервер 2 / ASPER2)" },
]

const DEEPGRAM_CFG_FIELDS: { key: string; label: string; type?: "password"; placeholder?: string }[] = [
  { key: "deepgram_api_key", label: "Deepgram API Key", type: "password" },
  { key: "deepgram_model", label: "Модель", placeholder: "nova-3" },
  { key: "deepgram_language", label: "Язык", placeholder: "ru" },
]

/** Настройки консольной команды `update` (обновление кода из git).
 *  Тоже только для суперпользователей: подменив каталог, remote или ветку,
 *  можно заставить сервер выполнить чужой код. */
const GIT_UPDATE_FIELDS: { key: string; label: string; placeholder?: string; hint?: string; secret?: boolean }[] = [
  {
    key: "git_update_repo_dir",
    label: "Каталог репозитория на сервере",
    placeholder: "пусто — каталог приложения",
    hint: "Путь в файловой системе, где уже лежит код: команда «update» делает git pull, а не clone. Адрес репозитория (https://…) сюда вписывать не нужно — он берётся из remote.",
  },
  {
    key: "git_update_repo_url",
    label: "Адрес репозитория",
    placeholder: "https://github.com/owner/repo.git",
    hint: "Нужен только для команды «update bootstrap» — развернуть код там, где рабочей копии ещё нет. Когда она есть, адрес берётся из remote и это поле не используется.",
  },
  { key: "git_update_remote", label: "Remote", placeholder: "origin" },
  { key: "git_update_branch", label: "Ветка", placeholder: "пусто — текущая" },
  {
    key: "git_update_token",
    label: "Токен доступа (приватный репозиторий)",
    placeholder: "пусто — репозиторий публичный или доступ по ssh-ключу",
    hint: "GitHub Personal Access Token с правом на чтение репозитория. Уходит одним заголовком Authorization только на хост remote'а и не записывается ни в remote URL, ни в .git/config. Сохранённый токен наружу не отдаётся — поле показывает заглушку, замените её, чтобы задать новый.",
    secret: true,
  },
  {
    key: "git_update_token_username",
    label: "Пользователь для токена",
    placeholder: "x-access-token",
    hint: "Для GitHub годится любое непустое значение, для GitLab — oauth2.",
  },
  {
    key: "git_update_frontend_dist_branch",
    label: "Ветка с готовой сборкой фронтенда",
    placeholder: "пусто — собирать локально через npm",
    hint: "Обычно frontend-dist. Задайте, если на сервере нет Node.js: тогда «update» скачивает готовый dist из этой ветки и npm не нужен вообще. Ветку наполняет workflow «Build frontend dist» на GitHub при каждом изменении frontend/.",
  },
  {
    key: "git_update_npm_path",
    label: "npm: путь к исполняемому файлу",
    placeholder: "пусто — искать в PATH и частых местах установки",
    hint: "Нужен только при локальной сборке (когда ветка выше не задана), если она падает с «Команда не найдена: npm» — типично для контейнера без интерактивного login-shell, где Node.js стоит через nvm: PATH процесса приложения его не видит. Что уже проверено, показывает «update status».",
  },
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
  const [cfg, setCfg] = useState<Record<string, string>>({})
  const [retentionDays, setRetentionDays] = useState(90)
  const queryClient = useQueryClient()
  const { data: me } = useCurrentAdmin()

  const { data } = useQuery({
    queryKey: ["administration"],
    queryFn: () => api.get<AdministrationResponse>("/api/v1/admin/administration"),
  })
  const { data: communicationConfig } = useQuery({
    queryKey: ["communications-config"],
    queryFn: () => api.get<CommunicationRetentionResponse>("/api/v1/admin/communications/config"),
  })
  useEffect(() => {
    if (communicationConfig) setRetentionDays(communicationConfig.retention_days)
  }, [communicationConfig])
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
  const saveCommunicationConfig = useMutation({
    mutationFn: () => api.patch("/api/v1/admin/communications/config", { retention_days: retentionDays }),
    onSuccess: () => { toast.success("Срок хранения коммуникаций сохранён"); queryClient.invalidateQueries({ queryKey: ["communications-config"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Конфигурация (.env)</CardTitle></CardHeader>
        <CardContent className="space-y-4 py-4">
          <div className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Секреты (пароли SFTP, Discord client secret и т.п.) не отображаются — показывается заглушка. Чтобы сменить секрет, впишите новое значение; чтобы стереть — очистите поле. Доступ только для суперпользователей.</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {CFG_FIELDS.map((f) => (
              <div key={f.key}>
                <Label className="text-xs text-muted-foreground">{f.label}</Label>
                <Input className="mt-1" type={f.type === "password" ? "password" : "text"} autoComplete={f.type === "password" ? "new-password" : undefined} value={cfg[f.key] ?? ""} onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))} />
              </div>
            ))}
          </div>
          <Button onClick={() => saveConfig.mutate()}>Сохранить конфигурацию</Button>
        </CardContent>
      </Card>

      <Card className="overflow-hidden py-0">
        <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Общение игроков</CardTitle></CardHeader>
        <CardContent className="space-y-3 py-4">
          <p className="text-xs text-muted-foreground">Текст чата и расшифровки голоса хранятся в MySQL. Аудио не сохраняется. Срок применяется фоновым очистителем.</p>
          <div className="max-w-xs"><Label className="text-xs text-muted-foreground">Срок хранения, дней</Label><Input className="mt-1" type="number" min={1} max={communicationConfig?.max_days ?? 3650} value={retentionDays} onChange={(e) => setRetentionDays(Number(e.target.value))} /></div>
          <Button onClick={() => saveCommunicationConfig.mutate()} disabled={saveCommunicationConfig.isPending}>Сохранить срок хранения</Button>
        </CardContent>
      </Card>

      {me?.is_superuser && <ServerRestartSettings />}
      {me?.is_superuser && <LobbyAdvertisementSettings />}
      {me?.is_superuser && <ExternalWorkersPanel />}

      {me?.is_superuser && (
        <Card className="overflow-hidden py-0">
          <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="text-sm font-semibold">Распознавание голоса (Deepgram)</CardTitle></CardHeader>
          <CardContent className="space-y-4 py-4">
            <div className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Ключ хранится в `.env` NexusData и не показывается в браузере. Игровой плагин забирает его через защищённый server API endpoint. После изменения перезапустите серверный плагин.</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {DEEPGRAM_CFG_FIELDS.map((f) => (
                <div key={f.key}>
                  <Label className="text-xs text-muted-foreground">{f.label}</Label>
                  <Input className="mt-1" type={f.type === "password" ? "password" : "text"} autoComplete={f.type === "password" ? "new-password" : undefined} placeholder={f.placeholder} value={cfg[f.key] ?? ""} onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))} />
                </div>
              ))}
            </div>
            <Button onClick={() => saveConfig.mutate()} disabled={saveConfig.isPending}>Сохранить настройки Deepgram</Button>
          </CardContent>
        </Card>
      )}

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
                  <Input className="mt-1" type={f.type === "password" ? "password" : "text"} autoComplete={f.type === "password" ? "new-password" : undefined} value={cfg[f.key] ?? ""} onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))} />
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
                    type={f.secret ? "password" : "text"}
                    autoComplete={f.secret ? "new-password" : undefined}
                    placeholder={f.placeholder}
                    value={cfg[f.key] ?? ""}
                    onChange={(e) => setCfg((c) => ({ ...c, [f.key]: e.target.value }))}
                  />
                  {f.hint && <span className="mt-1 block text-xs text-muted-foreground">{f.hint}</span>}
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
              <div>update bootstrap  — развернуть код по адресу репозитория,</div>
              <div>&nbsp;                   если рабочей копии на сервере ещё нет</div>
              <div>update status     — каталог, ветка, ревизия и проверка доступа</div>
            </div>
            <Button onClick={() => saveConfig.mutate()}>Сохранить конфигурацию</Button>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
