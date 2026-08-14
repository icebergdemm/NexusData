import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Trophy, Search } from "lucide-react"
import { PageHeader } from "@/components/layout/PageHeader"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"
import { JsonField } from "./json-field"
import {
  type AchievementRow, type AchievementsListResponse, type AchievementFormState, type PlayerProgressRow,
  EMPTY_FORM, rowToForm, formToPayload,
} from "./types"

function AchievementForm({ form, onChange, reference }: {
  form: AchievementFormState
  onChange: (f: AchievementFormState) => void
  reference?: AchievementsListResponse["reference"]
}) {
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <Label className="mb-1.5 block text-xs">key *</Label>
          <Input value={form.key} onChange={(e) => onChange({ ...form, key: e.target.value })} placeholder="kill_5_crawlers" className="font-mono" />
        </div>
        <div>
          <Label className="mb-1.5 block text-xs">Иконка (эмодзи)</Label>
          <Input value={form.icon} onChange={(e) => onChange({ ...form, icon: e.target.value })} placeholder="🏆" />
        </div>
        <div>
          <Label className="mb-1.5 block text-xs">Категория</Label>
          <Input value={form.category} onChange={(e) => onChange({ ...form, category: e.target.value })} placeholder="combat" />
        </div>
      </div>
      <div>
        <Label className="mb-1.5 block text-xs">Название *</Label>
        <Input value={form.title} onChange={(e) => onChange({ ...form, title: e.target.value })} placeholder="Охотник на крабов" />
      </div>
      <div>
        <Label className="mb-1.5 block text-xs">Описание</Label>
        <Input value={form.description} onChange={(e) => onChange({ ...form, description: e.target.value })} placeholder="Убейте 5 крабbusters" />
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <Label className="mb-1.5 block text-xs">Область счётчика</Label>
          <Select value={form.scope} onValueChange={(v) => onChange({ ...form, scope: v as "persistent" | "round" })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="persistent">persistent — копится всегда</SelectItem>
              <SelectItem value="round">round — сбрасывается каждый раунд</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label className="mb-1.5 block text-xs">Серверы (через запятую, пусто = все)</Label>
          <Input value={form.serversCsv} onChange={(e) => onChange({ ...form, serversCsv: e.target.value })} placeholder="ASPER1, ASPER2" />
        </div>
        <div>
          <Label className="mb-1.5 block text-xs">Цепочка (chain_group)</Label>
          <Input value={form.chainGroup} onChange={(e) => onChange({ ...form, chainGroup: e.target.value })} placeholder="crawler_hunter" />
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <Label className="mb-1.5 block text-xs">Требует ачивки (ключи через запятую)</Label>
          <Input value={form.requiresCsv} onChange={(e) => onChange({ ...form, requiresCsv: e.target.value })} placeholder="kill_5_crawlers" className="font-mono" />
        </div>
        <div>
          <Label className="mb-1.5 block text-xs">Порядок в цепочке</Label>
          <Input type="number" value={form.chainOrder} onChange={(e) => onChange({ ...form, chainOrder: e.target.value })} />
        </div>
        <div>
          <Label className="mb-1.5 block text-xs">Порядок сортировки</Label>
          <Input type="number" value={form.sortOrder} onChange={(e) => onChange({ ...form, sortOrder: e.target.value })} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4 rounded-md border p-2">
        <label className="flex items-center gap-1.5 text-xs">
          <Checkbox checked={form.enabled} onCheckedChange={(c) => onChange({ ...form, enabled: c === true })} /> Активна
        </label>
        <label className="flex items-center gap-1.5 text-xs">
          <Checkbox checked={form.hidden} onCheckedChange={(c) => onChange({ ...form, hidden: c === true })} /> Скрытая (до разблокировки)
        </label>
        <label className="flex items-center gap-1.5 text-xs">
          <Checkbox checked={form.notifyDiscord} onCheckedChange={(c) => onChange({ ...form, notifyDiscord: c === true })} /> ЛС от бота при разблокировке
        </label>
      </div>

      <div>
        <Label className="mb-1.5 block text-xs">
          Триггер — событие + условия + count/distinct_by (JSON)
        </Label>
        <JsonField value={form.triggerJson} onChange={(v) => onChange({ ...form, triggerJson: v })} rows={8} />
        <p className="mt-1 text-[11px] text-muted-foreground">
          Режимы подсчёта (взаимоисключающие): по умолчанию +1 за событие ·{" "}
          <code>distinct_by</code> — число разных значений поля · <code>sum_field</code> — сумма числового поля.
          Модификаторы: <code>cooldown_seconds</code> (минимум между засчётами),{" "}
          <code>max_per_round</code>, <code>window_seconds</code> (N событий за окно),{" "}
          <code>reset_on_death</code>.
        </p>
        {reference && (
          <details className="mt-1">
            <summary className="cursor-pointer text-[11px] text-muted-foreground">
              Доступные события ({Object.keys(reference.events).length}) и поля условий
            </summary>
            <div className="mt-1 space-y-1 rounded-md border p-2">
              {Object.entries(reference.events).map(([key, ev]) => (
                <div key={key} className="text-[11px]">
                  <code className="font-semibold">{key}</code>
                  <span className="text-muted-foreground"> — {ev.label}</span>
                  <div className="text-muted-foreground">
                    поля: {Object.keys(ev.fields).map((f) => <code key={f} className="mr-1">{f}</code>)}
                  </div>
                </div>
              ))}
              <div className="pt-1 text-[11px] text-muted-foreground">
                Операторы условий: {reference.condition_ops.join(", ")}.
              </div>
            </div>
          </details>
        )}
      </div>

      <div>
        <Label className="mb-1.5 block text-xs">Награды — список объектов (JSON)</Label>
        <JsonField value={form.rewardsJson} onChange={(v) => onChange({ ...form, rewardsJson: v })} rows={6} />
        <p className="mt-1 text-[11px] text-muted-foreground">
          Типы: <code>item</code> (identifier, amount, quality?, condition?), <code>stat</code> (stat_type, identifier, value, remove_on_death?),{" "}
          <code>skill</code> (skill, amount), <code>talent</code> (talent_id). Пример: <code>[{"{"}"type":"item","identifier":"revolver","amount":1{"}"}]</code>
        </p>
      </div>
    </div>
  )
}

function AchievementCard({ row, canEdit, reference, onSaved }: {
  row: AchievementRow
  canEdit: boolean
  reference: AchievementsListResponse["reference"]
  onSaved: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<AchievementFormState>(() => rowToForm(row))
  const [deleteOpen, setDeleteOpen] = useState(false)

  const update = useMutation({
    mutationFn: () => {
      let payload: Record<string, unknown>
      try { payload = formToPayload(form) } catch (e) { throw new Error(`Некорректный JSON: ${(e as Error).message}`) }
      return api.patch(`/api/v1/admin/achievements/${row.key}`, payload)
    },
    onSuccess: (res: unknown) => {
      const warnings = (res as { warnings?: string[] })?.warnings ?? []
      toast.success(warnings.length ? `Сохранено с предупреждениями: ${warnings.join("; ")}` : "Сохранено")
      setEditing(false)
      onSaved()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => api.patch(`/api/v1/admin/achievements/${row.key}`, { enabled }),
    onSuccess: () => { toast.success("Обновлено"); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })

  const clone = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/achievements/${row.key}/clone`, {}),
    onSuccess: () => { toast.success("Клон создан"); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })

  const del = useMutation({
    mutationFn: () => api.delete(`/api/v1/admin/achievements/${row.key}`),
    onSuccess: () => { toast.success("Ачивка удалена"); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div>
          <div className="font-semibold">{row.icon ? `${row.icon} ` : ""}{row.title}</div>
          <div className="font-mono text-xs text-muted-foreground">{row.key}</div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {canEdit && (
            <>
              <Button size="sm" variant="outline" onClick={() => toggleEnabled.mutate(!row.enabled)}>
                {row.enabled ? "Отключить" : "Включить"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => clone.mutate()}>⧉ Клон</Button>
              <Button size="sm" variant="outline" onClick={() => (editing ? setEditing(false) : (setForm(rowToForm(row)), setEditing(true)))}>
                {editing ? "Отмена" : "✏️"}
              </Button>
              <Button size="sm" variant="destructive" onClick={() => setDeleteOpen(true)}>🗑</Button>
            </>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {editing ? (
          <div className="animate-in fade-in slide-in-from-top-2 space-y-2 duration-200">
            <AchievementForm form={form} onChange={setForm} reference={reference} />
            <Button size="sm" disabled={update.isPending} onClick={() => update.mutate()}>💾 Сохранить</Button>
          </div>
        ) : (
          <div className="space-y-1.5">
            {row.description && <p className="text-sm text-muted-foreground">{row.description}</p>}
            <div className="flex flex-wrap gap-1.5">
              <Badge variant={row.enabled ? "default" : "secondary"}>{row.enabled ? "активна" : "отключена"}</Badge>
              {row.hidden && <Badge variant="outline">скрытая</Badge>}
              <Badge variant="outline">{row.scope}</Badge>
              {row.category && <Badge variant="outline">{row.category}</Badge>}
              <Badge variant="outline" className="font-mono">
                {String((row.trigger as { event?: string })?.event ?? "?")} × {String((row.trigger as { count?: number })?.count ?? 1)}
              </Badge>
              <Badge variant="outline">{row.rewards.length} наград</Badge>
              {row.servers.length > 0 && <Badge variant="outline">{row.servers.join(", ")}</Badge>}
              {row.requires.length > 0 && <Badge variant="outline">после: {row.requires.join(", ")}</Badge>}
              {row.notify_discord && <Badge variant="outline">ЛС в Discord</Badge>}
            </div>
          </div>
        )}
      </CardContent>
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Удалить ачивку?"
        description={`«${row.title}» (${row.key}) будет удалена безвозвратно вместе со всем прогрессом игроков по ней.`}
        onConfirm={() => del.mutate()}
      />
    </Card>
  )
}

function PlayerProgressTab() {
  const [steamid, setSteamid] = useState("")
  const [searched, setSearched] = useState<string | null>(null)

  const { data, isFetching } = useQuery({
    queryKey: ["achievements-player-progress", searched],
    queryFn: () => api.get<{ progress: PlayerProgressRow[] }>(`/api/v1/admin/achievements/players/${searched}`),
    enabled: !!searched,
  })

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex items-end gap-2 pt-4">
          <div className="flex-1">
            <Label className="mb-1.5 block text-xs">SteamID64 игрока</Label>
            <Input value={steamid} onChange={(e) => setSteamid(e.target.value)} placeholder="76561198000000000" className="font-mono" />
          </div>
          <Button onClick={() => setSearched(steamid.trim())} disabled={!steamid.trim()}>
            <Search className="size-3.5" /> Найти
          </Button>
        </CardContent>
      </Card>

      {isFetching && <div className="py-8 text-center text-muted-foreground">Загрузка…</div>}

      {searched && !isFetching && (
        <div className="grid gap-3 sm:grid-cols-2">
          {data?.progress.map((p) => (
            <Card key={p.achievement_key}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0">
                <div>
                  <div className="font-semibold">{p.icon ? `${p.icon} ` : ""}{p.title}</div>
                  <div className="font-mono text-xs text-muted-foreground">{p.achievement_key}</div>
                </div>
                {p.unlocked
                  ? <Badge>✅ разблокировано</Badge>
                  : <Badge variant="secondary">{p.progress}/{p.target}</Badge>}
              </CardHeader>
              <CardContent>
                {p.unlocked_at && <div className="text-xs text-muted-foreground">Разблокировано: {new Date(p.unlocked_at).toLocaleString("ru-RU")}</div>}
                {p.granted_manually && <Badge variant="outline" className="mt-1">выдано вручную</Badge>}
                {p.distinct_values.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {p.distinct_values.map((v) => <Badge key={v} variant="outline" className="font-mono">{v}</Badge>)}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
          {(!data || data.progress.length === 0) && (
            <div className="col-span-full py-8 text-center text-muted-foreground">
              Прогресса пока нет — игрок ещё не начал ни одной ачивки (или сайт ещё не получал отчётов от игрового сервера).
            </div>
          )}
        </div>
      )}

      <p className="text-[11px] text-muted-foreground">
        Ручная выдача/сброс ачивки — на этой же странице, из карточки конкретной ачивки, недоступна напрямую здесь;
        обратитесь к разделу «Ачивки» и используйте API <code>/api/v1/admin/achievements/players/&#123;steamid&#125;/grant</code>{" "}
        (или дождитесь реализации формы выдачи — прогресс/сброс синхронизируются с игровым сервером при следующем входе игрока).
      </p>
    </div>
  )
}

export function AchievementsPage() {
  const [newForm, setNewForm] = useState<AchievementFormState>(EMPTY_FORM)
  const queryClient = useQueryClient()

  const { data } = useQuery({ queryKey: ["achievements"], queryFn: () => api.get<AchievementsListResponse>("/api/v1/admin/achievements") })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["achievements"] })

  const create = useMutation({
    mutationFn: () => {
      let payload: Record<string, unknown>
      try { payload = formToPayload(newForm) } catch (e) { throw new Error(`Некорректный JSON: ${(e as Error).message}`) }
      return api.post("/api/v1/admin/achievements", payload)
    },
    onSuccess: (res: unknown) => {
      const warnings = (res as { warnings?: string[] })?.warnings ?? []
      toast.success(warnings.length ? `Ачивка создана с предупреждениями: ${warnings.join("; ")}` : "Ачивка создана")
      setNewForm(EMPTY_FORM)
      invalidate()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const grouped = new Map<string, AchievementRow[]>()
  for (const a of data?.achievements ?? []) {
    const key = a.chain_group || "__none__"
    if (!grouped.has(key)) { grouped.set(key, []) }
    grouped.get(key)!.push(a)
  }

  return (
    <div>
      <PageHeader
        title="Ачивки"
        description="Конструктор: событие → условия → награда, с поддержкой цепочек и предпосылок"
        icon={Trophy}
      />
      <Tabs defaultValue="list" className="space-y-4">
        <TabsList>
          <TabsTrigger value="list">Список ачивок</TabsTrigger>
          <TabsTrigger value="player">Прогресс игрока</TabsTrigger>
        </TabsList>

        <TabsContent value="list" className="space-y-4">
          {data?.can_edit && (
            <Card>
              <CardHeader><CardTitle className="text-sm">Создать ачивку</CardTitle></CardHeader>
              <CardContent className="space-y-2">
                <AchievementForm form={newForm} onChange={setNewForm} reference={data.reference} />
                <Button disabled={!newForm.key.trim() || !newForm.title.trim() || create.isPending} onClick={() => create.mutate()}>
                  ＋ Создать
                </Button>
              </CardContent>
            </Card>
          )}

          {Array.from(grouped.entries()).map(([group, rows]) => (
            <div key={group} className="space-y-2">
              {group !== "__none__" && (
                <div className="flex items-center gap-2 text-sm font-semibold text-muted-foreground">
                  <span>Цепочка: {group}</span>
                </div>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                {rows
                  .slice()
                  .sort((a, b) => a.chain_order - b.chain_order || a.sort_order - b.sort_order)
                  .map((row) => (
                    <AchievementCard
                      key={row.key}
                      row={row}
                      canEdit={!!data?.can_edit}
                      reference={data!.reference}
                      onSaved={invalidate}
                    />
                  ))}
              </div>
            </div>
          ))}
          {data && data.achievements.length === 0 && (
            <div className="py-8 text-center text-muted-foreground">Ачивок пока нет — создайте первую выше.</div>
          )}
        </TabsContent>

        <TabsContent value="player">
          <PlayerProgressTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}
