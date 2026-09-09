import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Link } from "react-router-dom"
import { Bot, ExternalLink, HeartPulse, Play, RefreshCw, Search, Upload } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { api } from "@/lib/api"
import { useCurrentAdmin } from "@/lib/auth"
import { formatBrowserDate } from "@/lib/format-date"
import type { AIDossierResponse } from "@/pages/players/detail-types"
import { DossierResultView } from "./dossier-result"

type Config = {
  enabled: boolean; daily_enabled: boolean; test_mode: boolean; daily_limit: number
  schedule_hour: number; active_days: number; max_log_events: number
  configured: boolean; endpoint_host: string | null; tls_ca_configured: boolean; model: string
  prompt: string; ignored_ips: string[]; auto_ip_ignore_days: number; auto_ip_ignore_min_players: number
}
type ConnectionDraft = { url: string; apiKey: string; tlsCaFile: string }
type Overview = { config: Config; queue: Record<string, number>; last_run: Run | null; last_health_check: Run | null }
type Player = { player_id: number; name: string; last_login_time: string | null; status: string; generated_at: string | null }
type Run = { id: number; player_id: number | null; player_name: string | null; source: string; status: string; requested_by_admin_id: number | null; requested_at: string | null; started_at: string | null; finished_at: string | null; latency_ms: number | null; prompt_tokens: number | null; completion_tokens: number | null; model: string | null; error: string | null }

const statusLabel: Record<string, string> = { none: "Нет отчёта", queued: "В очереди", running: "В работе", succeeded: "Готово", failed: "Ошибка" }
const sourceLabel: Record<string, string> = { manual: "вручную", manual_batch: "полный запуск", daily: "ежедневно", test: "тест", health: "health-check" }
const kindLabel: Record<string, string> = { dossier: "досье", health: "health-check" }
const stageLabel: Record<string, string> = {
  claimed: "задача взята воркером", packet_built: "packet собран", request_sent: "запрос отправлен",
  response_received: "ответ получен", result_validated: "результат валиден", json_validated: "JSON валиден",
  state_built: "состояние собрано", validated: "изменения валидированы", applied: "применено",
  completed: "завершено", succeeded: "успешно", failed: "ошибка", retry: "повторная генерация",
}
const levelColor: Record<string, string> = { info: "text-muted-foreground", warning: "text-warning", error: "text-destructive" }
const fmt = (value?: string | null) => value ? formatBrowserDate(value) : "—"

type TaskLogEntry = { id: number; kind: string; ref: string; stage: string; level: string; message: string; created_at: string | null }

function TaskLogCard() {
  const [kindFilter, setKindFilter] = useState("")
  const { data } = useQuery({
    queryKey: ["ai-center-task-log", kindFilter],
    queryFn: () => api.get<{ entries: TaskLogEntry[] }>(`/api/v1/admin/ai-dossiers/task-log?limit=300${kindFilter ? `&kind=${kindFilter}` : ""}`),
    refetchInterval: 3000,
  })
  const entries = data?.entries ?? []
  // Группировка по задаче с сохранением порядка появления (новые сверху):
  // оператор видит весь прогресс конкретной задачи как один блок.
  const groups: Array<{ key: string; entries: TaskLogEntry[] }> = []
  const byKey = new Map<string, TaskLogEntry[]>()
  for (const entry of entries) {
    const key = `${entry.kind}:${entry.ref}`
    const bucket = byKey.get(key)
    if (bucket) bucket.push(entry)
    else { const fresh: TaskLogEntry[] = [entry]; byKey.set(key, fresh); groups.push({ key, entries: fresh }) }
  }
  return <Card><CardHeader>
    <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-sm">Журнал задач AI (по этапам)
      <select className="rounded border bg-background px-2 py-1 text-xs" value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
        <option value="">все виды задач</option>
        <option value="dossier">досье</option>
        <option value="health">health-check</option>
      </select>
    </CardTitle>
  </CardHeader><CardContent className="max-h-[28rem] space-y-3 overflow-auto text-xs">
    {!groups.length && <p className="text-sm text-muted-foreground">Пока нет записей — журнал заполняется, когда AI-задачи доходят до воркеров.</p>}
    {groups.slice(0, 25).map((group) => {
      const failed = group.entries.some((e) => e.level === "error")
      const running = group.entries.some((e) => e.stage === "claimed" || e.stage === "request_sent") && group.entries.every((e) => e.stage !== "succeeded" && e.stage !== "completed" && e.stage !== "applied" && e.stage !== "failed")
      return <div key={group.key} className={`rounded-lg border p-2 ${failed ? "border-destructive/30 bg-destructive/5" : running ? "border-primary/30 bg-primary/5" : ""}`}>
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{kindLabel[group.entries[0].kind] || group.entries[0].kind}</Badge>
          <span className="font-mono text-[10px]">{group.entries[0].ref}</span>
          <Badge variant={failed ? "destructive" : running ? "default" : "outline"} className="text-[9px]">{failed ? "ошибка" : running ? "в работе" : "завершено"}</Badge>
        </div>
        <ol className="space-y-0.5">
          {group.entries.slice().reverse().map((entry) => <li key={entry.id} className="flex flex-wrap items-baseline gap-2 text-[11px]">
            <span className="font-mono text-muted-foreground">{entry.created_at?.slice(11, 19)}</span>
            <span className="font-medium">{stageLabel[entry.stage] || entry.stage}</span>
            <span className={levelColor[entry.level] || "text-muted-foreground"}>{entry.message}</span>
          </li>)}
        </ol>
      </div>
    })}
  </CardContent></Card>
}

function Status({ value }: { value: string }) {
  return <Badge variant={value === "succeeded" ? "default" : value === "failed" ? "destructive" : "secondary"}>{statusLabel[value] || value}</Badge>
}

function RawResponsePanel({ value }: { value: string }) {
  return <Card><CardHeader><CardTitle className="text-sm">Сырой ответ Qwen</CardTitle></CardHeader><CardContent><p className="mb-2 text-xs text-warning">Только для суперпользователя. Ответ может содержать данные игрока; не копируйте его за пределы модераторского контура.</p><pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded border bg-muted/30 p-3 font-mono text-xs">{value}</pre></CardContent></Card>
}

export function AICenterPage() {
  const { data: me, isLoading: isAdminLoading } = useCurrentAdmin()
  const queryClient = useQueryClient()
  const isSuperuser = !!me?.is_superuser
  const [search, setSearch] = useState("")
  const [selected, setSelected] = useState<Player | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [draft, setDraft] = useState<Config | null>(null)
  const [connection, setConnection] = useState<ConnectionDraft | null>(null)
  const debouncedSearch = useDebouncedValue(search, 350)
  const { data: overview, isLoading: overviewLoading, refetch: refetchOverview } = useQuery({
    queryKey: ["ai-center-overview"], queryFn: () => api.get<Overview>("/api/v1/admin/ai-dossiers/overview"), enabled: isSuperuser, refetchInterval: 5000,
  })
  const { data: players } = useQuery({
    queryKey: ["ai-center-players", debouncedSearch],
    queryFn: () => api.get<{ players: Player[] }>(`/api/v1/admin/ai-dossiers/players?q=${encodeURIComponent(debouncedSearch)}`),
    enabled: isSuperuser,
  })
  const { data: dossier } = useQuery({
    queryKey: ["ai-center-dossier", selected?.player_id],
    queryFn: () => api.get<AIDossierResponse>(`/api/v1/admin/players/${selected!.player_id}/ai-dossier`),
    enabled: !!selected, refetchInterval: selected ? 5000 : false,
  })
  const { data: runs } = useQuery({
    queryKey: ["ai-center-runs"], queryFn: () => api.get<{ runs: Run[] }>("/api/v1/admin/ai-dossiers/runs?limit=30"), enabled: isSuperuser, refetchInterval: 5000,
  })
  const { data: config } = useQuery({
    queryKey: ["ai-center-config"], queryFn: () => api.get<Config>("/api/v1/admin/ai-dossiers/config"), enabled: isSuperuser,
  })
  const { data: administration } = useQuery({
    queryKey: ["ai-center-administration-config"],
    queryFn: () => api.get<{ editable_cfg: Record<string, string | number | boolean> }>("/api/v1/admin/administration"),
    enabled: isSuperuser,
  })
  useEffect(() => { if (config) setDraft(config) }, [config])
  useEffect(() => {
    const cfg = administration?.editable_cfg
    if (cfg) setConnection({
      url: String(cfg.ai_dossier_url ?? ""),
      apiKey: String(cfg.ai_dossier_api_key ?? ""),
      tlsCaFile: String(cfg.ai_dossier_tls_ca_file ?? ""),
    })
  }, [administration])

  const requestDossier = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/players/${selected!.player_id}/ai-dossier`, { force: true }),
    onSuccess: () => { setConfirmOpen(false); toast.success("Задача поставлена в очередь"); queryClient.invalidateQueries({ queryKey: ["ai-center-overview"] }); queryClient.invalidateQueries({ queryKey: ["ai-center-dossier"] }); queryClient.invalidateQueries({ queryKey: ["ai-center-runs"] }) },
    onError: (error: Error) => toast.error(error.message),
  })
  const healthCheck = useMutation({
    mutationFn: () => api.post<{ status: string }>("/api/v1/admin/ai-dossiers/health-check", {}),
    onSuccess: (result: { status: string }) => { toast.success(`Health-check: ${result.status}`); refetchOverview(); queryClient.invalidateQueries({ queryKey: ["ai-center-runs"] }) },
    onError: (error: Error) => toast.error(error.message),
  })
  const saveConfig = useMutation({
    mutationFn: () => api.patch("/api/v1/admin/ai-dossiers/config", draft && {
      enabled: draft.enabled, daily_enabled: draft.daily_enabled, test_mode: draft.test_mode,
      daily_limit: draft.daily_limit, schedule_hour: draft.schedule_hour,
      active_days: draft.active_days, max_log_events: draft.max_log_events, prompt: draft.prompt,
      ignored_ips: draft.ignored_ips,
    }),
    onSuccess: () => { toast.success("Операционные настройки сохранены"); queryClient.invalidateQueries({ queryKey: ["ai-center-config"] }); refetchOverview() },
    onError: (error: Error) => toast.error(error.message),
  })
  const saveConnection = useMutation({
    mutationFn: () => {
      if (!connection) throw new Error("Настройки подключения ещё не загружены")
      const payload: Record<string, string> = {
        ai_dossier_url: connection.url,
        ai_dossier_tls_ca_file: connection.tlsCaFile,
      }
      // The administration API returns a mask for an existing key. Never
      // send that mask back as a real credential.
      if (connection.apiKey && connection.apiKey !== "••••••••") payload.ai_dossier_api_key = connection.apiKey
      return api.post("/api/v1/admin/administration/config", payload)
    },
    onSuccess: () => { toast.success("Подключение сохранено. Выполните update restart для применения."); queryClient.invalidateQueries({ queryKey: ["ai-center-administration-config"] }); refetchOverview() },
    onError: (error: Error) => toast.error(error.message),
  })
  const uploadCa = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append("file", file)
      return api.postForm<{ path: string }>("/api/v1/admin/ai-dossiers/connection-ca", form)
    },
    onSuccess: (result) => {
      setConnection((current) => current ? { ...current, tlsCaFile: result.path } : current)
      toast.success("CA-файл загружен. Сохраните подключение и выполните update restart.")
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const batchTest = useMutation({
    mutationFn: () => api.post<{ selected: number; queued: number }>("/api/v1/admin/ai-dossiers/test-run", {}),
    onSuccess: (result) => { toast.success(`В очередь поставлено ${result.queued} из ${result.selected}`); refetchOverview() },
    onError: (error: Error) => toast.error(error.message),
  })
  const activeBatch = useMutation({
    mutationFn: () => api.post<{ selected: number; queued: number; skipped: number }>("/api/v1/admin/ai-dossiers/run-active-now", {}),
    onSuccess: (result) => { toast.success(`В очередь поставлено ${result.queued} из ${result.selected}`); refetchOverview(); queryClient.invalidateQueries({ queryKey: ["ai-center-runs"] }) },
    onError: (error: Error) => toast.error(error.message),
  })

  if (isAdminLoading) return <div className="text-sm text-muted-foreground">Загрузка…</div>
  if (!isSuperuser) return <Card><CardContent className="py-10 text-center text-muted-foreground">Доступ к AI-центру есть только у суперпользователей.</CardContent></Card>

  return <div className="space-y-4">
    <PageHeader title="AI-центр" description="Qwen/KoboldCpp: проверка подключения, контролируемые досье и журнал запусков" icon={Bot} actions={<Button variant="outline" size="sm" onClick={() => refetchOverview()}><RefreshCw className="mr-1.5 size-3.5" /> Обновить</Button>} />
    <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">ИИ-сводка справочная. Решение принимает модератор, а не языковая модель, которой дали доступ к логам и немного уверенности в себе.</div>
    {isSuperuser && dossier?.run?.raw_response && <RawResponsePanel value={dossier.run.raw_response} />}
    {isSuperuser && dossier?.status === "failed" && !dossier.run?.raw_response && <Card><CardHeader><CardTitle className="text-sm">Сырой ответ Qwen</CardTitle></CardHeader><CardContent className="text-sm text-muted-foreground">Для этой ошибки raw-ответ недоступен: запуск мог завершиться до ответа модели или был создан до включения диагностического сохранения. Повторите генерацию, чтобы получить тело ответа, если KoboldCpp его вернёт.</CardContent></Card>}

    <div className="grid gap-4 lg:grid-cols-3">
      <Card><CardHeader><CardTitle className="text-sm">Qwen</CardTitle></CardHeader><CardContent className="space-y-2 text-sm">
        {overviewLoading ? <p>Загрузка…</p> : overview && <><div className="flex items-center gap-2"><Status value={overview.config.configured ? "succeeded" : "failed"} /> {overview.config.configured ? "endpoint настроен" : "endpoint/ключ не настроены"}</div><div>Хост: <span className="font-mono">{overview.config.endpoint_host || "—"}</span></div><div>Модель: <span className="font-mono">{overview.config.model}</span></div><div>TLS CA: {overview.config.tls_ca_configured ? "подключён" : "не задан"}</div>{isSuperuser && <Button size="sm" variant="outline" onClick={() => healthCheck.mutate()} disabled={healthCheck.isPending}><HeartPulse className="mr-1.5 size-3.5" /> Проверить подключение</Button>}</>}
      </CardContent></Card>
      <Card><CardHeader><CardTitle className="text-sm">Очередь</CardTitle></CardHeader><CardContent className="grid grid-cols-2 gap-2 text-sm">{Object.entries(overview?.queue || {}).map(([key, count]) => <div key={key}><span className="text-muted-foreground">{statusLabel[key] || key}: </span>{count}</div>)}<div className="col-span-2 border-t pt-2">Последний запуск: {fmt(overview?.last_run?.finished_at)}<br />Health-check: {overview?.last_health_check ? `${statusLabel[overview.last_health_check.status] || overview.last_health_check.status}, ${fmt(overview.last_health_check.finished_at)}` : "ещё не выполнялся"}</div></CardContent></Card>
    </div>

    {isSuperuser && draft && <Card><CardHeader><CardTitle className="text-sm">Операционные настройки AI-досье</CardTitle></CardHeader><CardContent className="space-y-4"><div className="grid gap-3 sm:grid-cols-3"><label className="flex gap-2 text-sm"><Checkbox checked={draft.enabled} onCheckedChange={(v) => setDraft({ ...draft, enabled: !!v })} /> Включено</label><label className="flex gap-2 text-sm"><Checkbox checked={draft.daily_enabled} onCheckedChange={(v) => setDraft({ ...draft, daily_enabled: !!v })} /> Ежедневный запуск</label><label className="flex gap-2 text-sm"><Checkbox checked={draft.test_mode} onCheckedChange={(v) => setDraft({ ...draft, test_mode: !!v })} /> Тестовый режим</label><div><Label className="text-xs">Час запуска (0–23)</Label><Input type="number" min={0} max={23} value={draft.schedule_hour} onChange={(e) => setDraft({ ...draft, schedule_hour: Number(e.target.value) })} /></div><div><Label className="text-xs">Профилей за сутки (1–10)</Label><Input type="number" min={1} max={10} value={draft.daily_limit} onChange={(e) => setDraft({ ...draft, daily_limit: Number(e.target.value) })} /></div><div><Label className="text-xs">Активность, дней</Label><Input type="number" min={1} max={365} value={draft.active_days} onChange={(e) => setDraft({ ...draft, active_days: Number(e.target.value) })} /></div><div><Label className="text-xs">Лимит логов</Label><Input type="number" min={1} max={500} value={draft.max_log_events} onChange={(e) => setDraft({ ...draft, max_log_events: Number(e.target.value) })} /></div></div><div><Label className="text-xs">System prompt Qwen</Label><Textarea className="mt-1 min-h-48 font-mono text-xs" value={draft.prompt} onChange={(e) => setDraft({ ...draft, prompt: e.target.value })} /><p className="mt-1 text-xs text-muted-foreground">Редактируется только суперпользователем, хранится в app_settings, максимум 20 000 символов. Endpoint, ключ, CA и параметры модели остаются в окружении.</p></div><div><Label className="text-xs">IP/подсети, исключённые из анализа</Label><Textarea className="mt-1 min-h-24 font-mono text-xs" value={draft.ignored_ips.join("\n")} onChange={(e) => setDraft({ ...draft, ignored_ips: e.target.value.split(/[\n,]+/).map((value) => value.trim()).filter(Boolean) })} placeholder="203.0.113.7\n198.51.100.0/24" /><p className="mt-1 text-xs text-muted-foreground">Эти IP останутся в профилях и поиске, но не участвуют в dossier/Sauron. Автоматически исключаются IP, встречающиеся у {draft.auto_ip_ignore_min_players}+ игроков за последние {draft.auto_ip_ignore_days} дней.</p></div><div className="flex flex-wrap gap-2"><Button onClick={() => saveConfig.mutate()} disabled={saveConfig.isPending}>Сохранить</Button><Button variant="outline" onClick={() => batchTest.mutate()} disabled={batchTest.isPending}>Тест первых 10</Button><Button variant="outline" onClick={() => activeBatch.mutate()} disabled={activeBatch.isPending}>Запустить для всех за период</Button></div></CardContent></Card>}
    {isSuperuser && connection && <Card><CardHeader><CardTitle className="text-sm">Подключение к Qwen</CardTitle></CardHeader><CardContent className="space-y-4"><div className="rounded border border-warning/30 bg-warning/10 p-3 text-sm text-warning">Здесь задаётся адрес моста и секрет. Ключ показывается маской; пустое поле сохраняет уже установленный ключ. После сохранения выполните <code className="font-mono">update restart</code> в консоли Pterodactyl — без рестарта уже запущенный Python продолжит жить в прошлом.</div><div className="grid gap-3 sm:grid-cols-3"><div><Label className="text-xs">OpenAI-compatible endpoint</Label><Input className="mt-1 font-mono text-xs" value={connection.url} onChange={(e) => setConnection({ ...connection, url: e.target.value })} placeholder="https://host:8000/v1/chat/completions" /></div><div><Label className="text-xs">API key</Label><Input className="mt-1 font-mono text-xs" type="password" autoComplete="new-password" value={connection.apiKey} onChange={(e) => setConnection({ ...connection, apiKey: e.target.value })} placeholder="Введите ключ KoboldCpp" /></div><div><Label className="text-xs">CA-файл в контейнере</Label><Input className="mt-1 font-mono text-xs" value={connection.tlsCaFile} onChange={(e) => setConnection({ ...connection, tlsCaFile: e.target.value })} placeholder="/home/container/.../koboldcpp-ca.pem" /></div></div><div className="flex flex-wrap items-end gap-2"><div><Label className="text-xs">Или загрузить публичный CA</Label><Input className="mt-1 w-72" type="file" accept=".pem,.crt,.cer,application/x-pem-file" onChange={(e) => { const file = e.target.files?.[0]; if (file) uploadCa.mutate(file); e.currentTarget.value = "" }} disabled={uploadCa.isPending} /></div><Button variant="outline" onClick={() => saveConnection.mutate()} disabled={saveConnection.isPending}><Upload className="mr-1.5 size-3.5" /> Сохранить подключение</Button></div><p className="text-xs text-muted-foreground">Загрузится только один публичный PEM-сертификат, максимум 64 КБ. Приватные ключи отклоняются. После загрузки сохраните подключение: путь подставится автоматически. TLS-проверка не отключается.</p></CardContent></Card>}

    <div className="grid gap-4 lg:grid-cols-[minmax(260px,0.8fr)_minmax(0,2fr)]">
      <Card><CardHeader><CardTitle className="text-sm">Игрок для проверки</CardTitle></CardHeader><CardContent className="space-y-3"><div className="relative"><Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" /><Input className="pl-8" placeholder="Имя или SteamID" value={search} onChange={(e) => setSearch(e.target.value)} /></div><div className="max-h-80 space-y-1 overflow-auto">{players?.players.map((player) => <button key={player.player_id} onClick={() => setSelected(player)} className={`w-full rounded border p-2 text-left text-sm hover:bg-accent ${selected?.player_id === player.player_id ? "border-primary bg-accent" : ""}`}><div className="flex items-center justify-between gap-2"><span className="truncate">{player.name}</span><Status value={player.status} /></div><div className="text-xs text-muted-foreground">#{player.player_id} · вход: {fmt(player.last_login_time)}</div></button>)}{players && !players.players.length && <p className="text-sm text-muted-foreground">Ничего не найдено.</p>}</div>{isSuperuser && selected && <Button className="w-full" onClick={() => setConfirmOpen(true)} disabled={dossier?.status === "queued" || dossier?.status === "running"}><Play className="mr-1.5 size-3.5" /> Запустить для {selected.name}</Button>}</CardContent></Card>
      <Card><CardHeader><CardTitle className="text-sm">Результат {selected && <span className="font-normal text-muted-foreground">— {selected.name}</span>}</CardTitle></CardHeader><CardContent className="space-y-4">{!selected && <p className="text-sm text-muted-foreground">Выберите реального игрока слева. Синтетические профили оставим для health-check.</p>}{selected && !dossier && <p className="text-sm text-muted-foreground">Загрузка…</p>}{dossier && <><div className="flex flex-wrap items-center gap-2"><Status value={dossier.status} /> <span className="text-xs text-muted-foreground">{fmt(dossier.generated_at)} · {dossier.model || "модель не указана"}</span>{dossier.run && <span className="text-xs text-muted-foreground">{dossier.run.latency_ms ?? "—"} мс · {dossier.run.prompt_tokens ?? "—"}+{dossier.run.completion_tokens ?? "—"} токенов</span>}<Link className="text-xs text-primary underline" to={`/players/${selected?.player_id}`}>Открыть профиль <ExternalLink className="inline size-3" /></Link></div>{dossier.status === "failed" && dossier.error && <div className="rounded border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive">Qwen не ответил: {dossier.error}. Показан последний валидный результат или серверный rules-only результат.</div>}{dossier.stale && <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-sm">Старый формат досье устарел и скрыт до перегенерации. История запусков сохранена.</div>}{dossier.result && <DossierResultView result={dossier.result} />}{!dossier.result && dossier.status !== "failed" && <p className="text-sm text-muted-foreground">Задача {statusLabel[dossier.status] || dossier.status}.</p>}</>}</CardContent></Card>
    </div>

    <TaskLogCard />

    <Card><CardHeader><CardTitle className="text-sm">Последние AI-запуски</CardTitle></CardHeader><CardContent className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b text-xs text-muted-foreground"><th className="p-2">Время</th><th className="p-2">Источник</th><th className="p-2">Игрок</th><th className="p-2">Инициатор</th><th className="p-2">Статус</th><th className="p-2">Latency / токены</th><th className="p-2">Ошибка</th></tr></thead><tbody>{runs?.runs.map((run) => <tr key={run.id} className="border-b last:border-0"><td className="p-2 whitespace-nowrap">{fmt(run.requested_at)}</td><td className="p-2">{sourceLabel[run.source] || run.source}</td><td className="p-2">{run.player_id ? <Link className="text-primary underline" to={`/players/${run.player_id}`}>{run.player_name || `#${run.player_id}`}</Link> : "—"}</td><td className="p-2">{run.requested_by_admin_id ? `#${run.requested_by_admin_id}` : "система"}</td><td className="p-2"><Status value={run.status} /></td><td className="p-2 whitespace-nowrap">{run.latency_ms ?? "—"} мс / {run.prompt_tokens ?? "—"}+{run.completion_tokens ?? "—"}</td><td className="max-w-xs truncate p-2 text-xs text-destructive">{run.error || "—"}</td></tr>)}</tbody></table>{!runs?.runs.length && <p className="p-3 text-sm text-muted-foreground">Запусков пока нет.</p>}</CardContent></Card>

    <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Отправить moderation packet в Qwen?</AlertDialogTitle><AlertDialogDescription>В модель уйдут только компактные находки и контекст для пояснения. Игровые stats, сессии и сырые IP в packet не входят; приоритет и evidence рассчитывает сервер.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Отмена</AlertDialogCancel><AlertDialogAction onClick={() => requestDossier.mutate()} disabled={requestDossier.isPending}>Подтвердить запуск</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
