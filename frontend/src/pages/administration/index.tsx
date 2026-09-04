import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { AlertTriangle, Sparkles } from "lucide-react"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"

// Статистика (онлайн за период, топ игроков) отсюда убрана — она дублировала
// страницу «Аналитика» неоптимизированной версией расчётов (полный скан
// player_sessions без индекса + вложенные Python-циклы прямо в теле
// async-обработчика, БЕЗ run_in_threadpool) и была основной причиной, по
// которой открытие этой страницы на несколько секунд подвешивало весь сайт
// целиком. См. routes/admin.py::api_admin_administration.
interface AdministrationResponse {
  editable_cfg: Record<string, string | number | boolean>
}

interface AIDossierConfig {
  enabled: boolean
  daily_enabled: boolean
  test_mode: boolean
  daily_limit: number
  configured: boolean
  model: string
}

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
  const [aiCfg, setAiCfg] = useState<AIDossierConfig | null>(null)
  const queryClient = useQueryClient()
  const { data: me } = useCurrentAdmin()

  const { data } = useQuery({
    queryKey: ["administration"],
    queryFn: () => api.get<AdministrationResponse>("/api/v1/admin/administration"),
  })
  const { data: loadedAiCfg } = useQuery({
    queryKey: ["ai-dossier-config"],
    queryFn: () => api.get<AIDossierConfig>("/api/v1/admin/ai-dossiers/config"),
    enabled: !!me?.is_superuser,
  })

  useEffect(() => { if (loadedAiCfg) setAiCfg(loadedAiCfg) }, [loadedAiCfg])

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

  const saveAiConfig = useMutation({
    mutationFn: () => api.patch("/api/v1/admin/ai-dossiers/config", {
      enabled: aiCfg?.enabled,
      daily_enabled: aiCfg?.daily_enabled,
      test_mode: aiCfg?.test_mode,
      daily_limit: aiCfg?.daily_limit,
    }),
    onSuccess: () => { toast.success("Настройки ИИ-досье сохранены"); queryClient.invalidateQueries({ queryKey: ["ai-dossier-config"] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const runAiTest = useMutation({
    mutationFn: () => api.post<{ selected: number; queued: number }>("/api/v1/admin/ai-dossiers/test-run", {}),
    onSuccess: (result: { selected: number; queued: number }) => toast.success(`Тестовая очередь: ${result.queued} из ${result.selected}`),
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

      {me?.is_superuser && (
        <Card className="overflow-hidden py-0">
          <CardHeader className="border-b bg-muted/30 py-3"><CardTitle className="flex items-center gap-2 text-sm font-semibold"><Sparkles className="size-4" /> AI-досье</CardTitle></CardHeader>
          <CardContent className="space-y-4 py-4">
            <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
              Сводка справочная и ничего сама не банит. Prompt хранится в Git и через браузер не редактируется — хотя бы одна поверхность для случайного prompt injection останется вне админки.
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex items-start gap-2.5 text-sm"><Checkbox checked={!!aiCfg?.enabled} onCheckedChange={(v) => setAiCfg((c) => c && ({ ...c, enabled: !!v }))} /><span>Разрешить AI-досье<span className="block text-xs text-muted-foreground">Ручной запуск доступен при наличии права просмотра игроков.</span></span></label>
              <label className="flex items-start gap-2.5 text-sm"><Checkbox checked={!!aiCfg?.daily_enabled} onCheckedChange={(v) => setAiCfg((c) => c && ({ ...c, daily_enabled: !!v }))} /><span>Ежедневное обновление<span className="block text-xs text-muted-foreground">Активные профили, тихий час по настройке сервера.</span></span></label>
              <label className="flex items-start gap-2.5 text-sm"><Checkbox checked={!!aiCfg?.test_mode} onCheckedChange={(v) => setAiCfg((c) => c && ({ ...c, test_mode: !!v }))} /><span>Тестовый режим<span className="block text-xs text-muted-foreground">Не меняет формат данных; нужен для контролируемого прогона.</span></span></label>
              <div><Label className="text-xs text-muted-foreground">Лимит профилей за сутки (1–10)</Label><Input className="mt-1" type="number" min={1} max={10} value={aiCfg?.daily_limit ?? 10} onChange={(e) => setAiCfg((c) => c && ({ ...c, daily_limit: Number(e.target.value) }))} /></div>
            </div>
            <div className="flex flex-wrap items-center gap-2"><Button disabled={!aiCfg || saveAiConfig.isPending} onClick={() => saveAiConfig.mutate()}>Сохранить настройки</Button><Button variant="outline" disabled={runAiTest.isPending} onClick={() => runAiTest.mutate()}><Sparkles className="size-3.5" /> Запустить тест первых 10</Button><span className="text-xs text-muted-foreground">{aiCfg?.configured ? `Подключено: ${aiCfg.model}` : "Endpoint/ключ не настроены"}</span></div>
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
