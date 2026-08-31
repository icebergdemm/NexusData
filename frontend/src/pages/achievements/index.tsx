/** Страница «Ачивки»: список определений с графическим конструктором
 *  (см. AchievementEditor) и вкладка прогресса конкретного игрока с ручной
 *  выдачей/сбросом.
 *
 *  Выдача и сброс долетают до игрока не мгновенно: плагин читает состояние с
 *  сайта при следующем входе игрока на сервер (см. докстринг
 *  routes/achievements.py) — об этом честно написано прямо в интерфейсе.
 */
import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Trophy, Search, Plus, Pencil, Copy, Trash2, Power, Undo2 } from "lucide-react"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { PageHeader } from "@/components/layout/PageHeader"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { PlatformIcon } from "@/components/layout/PlatformIcon"
import { api } from "@/lib/api"
import { formatBrowserDate } from "@/lib/format-date"
import { AchievementEditor } from "./AchievementEditor"
import { TriggerSummary } from "./trigger-builder"
import { SearchPicker, type Option } from "./pickers"
import {
  REWARD_TYPE_LABELS,
  type AchievementRow, type AchievementsListResponse, type AchievementsReference,
  type PlayerProgressRow, type RewardSpec,
} from "./types"

function rewardLabel(r: RewardSpec): string {
  switch (r.type) {
    case "item": return `${r.identifier}${(r.amount ?? 1) > 1 ? ` ×${r.amount}` : ""}`
    case "stat": return `${r.stat_type} ${r.value > 0 ? "+" : ""}${r.value}`
    case "skill": return `${r.skill} ${r.amount > 0 ? "+" : ""}${r.amount}`
    case "talent": return r.talent_id
    default: return REWARD_TYPE_LABELS[(r as RewardSpec).type]
  }
}

function AchievementCard({ row, canEdit, reference, onEdit, onChanged }: {
  row: AchievementRow
  canEdit: boolean
  reference: AchievementsReference
  onEdit: () => void
  onChanged: () => void
}) {
  const [deleteOpen, setDeleteOpen] = useState(false)

  const toggleEnabled = useMutation({
    mutationFn: () => api.patch(`/api/v1/admin/achievements/${row.key}`, { enabled: !row.enabled }),
    onSuccess: () => { toast.success(row.enabled ? "Ачивка отключена" : "Ачивка включена"); onChanged() },
    onError: (e: Error) => toast.error(e.message),
  })

  const clone = useMutation({
    mutationFn: () => api.post(`/api/v1/admin/achievements/${row.key}/clone`, {}),
    onSuccess: () => { toast.success("Создана копия (выключенная)"); onChanged() },
    onError: (e: Error) => toast.error(e.message),
  })

  const del = useMutation({
    mutationFn: () => api.delete(`/api/v1/admin/achievements/${row.key}`),
    onSuccess: () => { toast.success("Ачивка удалена"); onChanged() },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Card className={row.enabled ? "" : "opacity-70"}>
      <CardHeader className="flex flex-row items-start justify-between space-y-0 gap-2">
        <div className="flex min-w-0 items-start gap-2">
          <span className="flex size-7 items-center justify-center text-2xl leading-none"><PlatformIcon value={row.icon || "🏆"} fallback={Trophy} className="size-5" /></span>
          <div className="min-w-0">
            <div className="truncate font-semibold">{row.title}</div>
            <div className="truncate font-mono text-xs text-muted-foreground">{row.key}</div>
          </div>
        </div>
        {canEdit && (
          <div className="flex shrink-0 gap-1">
            <Button variant="ghost" size="icon-sm" title={row.enabled ? "Отключить" : "Включить"} onClick={() => toggleEnabled.mutate()}>
              <Power className={`size-4 ${row.enabled ? "text-success" : "text-muted-foreground"}`} />
            </Button>
            <Button variant="ghost" size="icon-sm" title="Копия" onClick={() => clone.mutate()}>
              <Copy className="size-4" />
            </Button>
            <Button variant="ghost" size="icon-sm" title="Изменить" onClick={onEdit}>
              <Pencil className="size-4" />
            </Button>
            <Button variant="ghost" size="icon-sm" title="Удалить" onClick={() => setDeleteOpen(true)}>
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-2">
        {row.description && <p className="text-sm text-muted-foreground">{row.description}</p>}
        <p className="text-xs"><TriggerSummary trigger={row.trigger} reference={reference} /></p>
        <div className="flex flex-wrap gap-1">
          <Badge variant={row.enabled ? "default" : "secondary"}>{row.enabled ? "активна" : "отключена"}</Badge>
          <Badge variant="outline">{row.scope === "round" ? "за раунд" : "накопительно"}</Badge>
          {row.hidden && <Badge variant="outline">скрытая</Badge>}
          {row.category && <Badge variant="outline">{row.category}</Badge>}
          {row.servers.length > 0 && <Badge variant="outline">{row.servers.join(", ")}</Badge>}
          {row.requires.length > 0 && <Badge variant="outline">после: {row.requires.join(", ")}</Badge>}
          {row.notify_discord && <Badge variant="outline">ЛС в Discord</Badge>}
        </div>
        {row.rewards.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-[11px] text-muted-foreground">Награды:</span>
            {row.rewards.map((r, i) => (
              <Badge key={i} variant="secondary" className="font-mono text-[10px]">{rewardLabel(r)}</Badge>
            ))}
          </div>
        )}
      </CardContent>
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Удалить ачивку?"
        description={`«${row.title}» (${row.key}) будет удалена безвозвратно вместе со всем прогрессом игроков по ней. Чтобы просто выключить её, сохранив прогресс, используйте кнопку питания.`}
        onConfirm={() => del.mutate()}
      />
    </Card>
  )
}

function PlayerProgressTab({ achievements, canGrant }: {
  achievements: AchievementRow[]
  canGrant: boolean
}) {
  const [steamid, setSteamid] = useState("")
  const [searched, setSearched] = useState<string | null>(null)
  const [grantKey, setGrantKey] = useState("")
  const queryClient = useQueryClient()

  const { data, isFetching } = useQuery({
    queryKey: ["achievements-player-progress", searched],
    queryFn: () => api.get<{ progress: PlayerProgressRow[] }>(`/api/v1/admin/achievements/players/${searched}`),
    enabled: !!searched,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["achievements-player-progress", searched] })

  const grant = useMutation({
    mutationFn: (key: string) =>
      api.post(`/api/v1/admin/achievements/players/${searched}/grant`, { achievement_key: key }),
    onSuccess: () => {
      toast.success("Выдано — игрок получит её в игре при следующем входе на сервер")
      setGrantKey("")
      invalidate()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const revoke = useMutation({
    mutationFn: (key: string) => api.delete(`/api/v1/admin/achievements/players/${searched}/${key}`),
    onSuccess: () => { toast.success("Прогресс сброшен"); invalidate() },
    onError: (e: Error) => toast.error(e.message),
  })

  const grantOptions: Option[] = achievements.map((a) => ({ value: a.key, label: a.title, hint: a.key }))
  const rows = data?.progress ?? []

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-end gap-2 pt-4">
          <div className="min-w-60 flex-1">
            <Label className="mb-1.5 block text-xs">SteamID64 игрока</Label>
            <Input
              value={steamid}
              onChange={(e) => setSteamid(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && steamid.trim()) setSearched(steamid.trim()) }}
              placeholder="76561198000000000"
              className="font-mono"
            />
          </div>
          <Button onClick={() => setSearched(steamid.trim())} disabled={!steamid.trim()}>
            <Search className="size-3.5" /> Найти
          </Button>
        </CardContent>
      </Card>

      {searched && canGrant && (
        <Card>
          <CardContent className="flex flex-wrap items-end gap-2 pt-4">
            <div className="min-w-60 flex-1">
              <Label className="mb-1.5 block text-xs">Выдать ачивку вручную</Label>
              <SearchPicker
                options={grantOptions}
                value={grantKey}
                onPick={setGrantKey}
                placeholder="Выберите ачивку"
                className="w-full"
              />
            </div>
            <Button disabled={!grantKey || grant.isPending} onClick={() => grant.mutate(grantKey)}>
              <Trophy className="size-3.5" /> Выдать
            </Button>
            <p className="w-full text-[11px] text-muted-foreground">
              Выдача пишет факт разблокировки и шлёт ЛС в Discord. Предметы, статы и таланты из наград она
              в игре НЕ выдаёт — для этого есть выдача наград на странице игрока.
            </p>
          </CardContent>
        </Card>
      )}

      {isFetching && <div className="py-8 text-center text-muted-foreground">Загрузка…</div>}

      {searched && !isFetching && (
        <div className="grid gap-3 sm:grid-cols-2">
          {rows.map((p) => (
            <Card key={p.achievement_key}>
              <CardHeader className="flex flex-row items-start justify-between space-y-0 gap-2">
                <div className="flex min-w-0 items-start gap-2">
                  <span className="flex size-6 items-center justify-center text-xl leading-none"><PlatformIcon value={p.icon || "🏆"} fallback={Trophy} /></span>
                  <div className="min-w-0">
                    <div className="truncate font-semibold">{p.title}</div>
                    <div className="truncate font-mono text-xs text-muted-foreground">{p.achievement_key}</div>
                  </div>
                </div>
                {p.unlocked
                  ? <Badge>разблокировано</Badge>
                  : <Badge variant="secondary">{p.progress}/{p.target}</Badge>}
              </CardHeader>
              <CardContent className="space-y-1.5">
                {!p.unlocked && p.target > 0 && (
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full bg-primary"
                      style={{ width: `${Math.min(100, Math.round((p.progress / p.target) * 100))}%` }}
                    />
                  </div>
                )}
                {p.unlocked_at && (
                  <div className="text-xs text-muted-foreground">
                    Разблокировано: {formatBrowserDate(p.unlocked_at)}
                  </div>
                )}
                {p.granted_manually && <Badge variant="outline">выдано вручную</Badge>}
                {p.distinct_values.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {p.distinct_values.map((v) => (
                      <Badge key={v} variant="outline" className="font-mono text-[10px]">{v}</Badge>
                    ))}
                  </div>
                )}
                {canGrant && (p.unlocked || p.progress > 0) && (
                  <Button
                    variant="outline" size="sm" className="h-7 text-xs"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(p.achievement_key)}
                  >
                    <Undo2 className="mr-1 size-3" /> Сбросить прогресс
                  </Button>
                )}
              </CardContent>
            </Card>
          ))}
          {rows.length === 0 && (
            <div className="col-span-full py-8 text-center text-muted-foreground">
              Прогресса пока нет — игрок не начал ни одной ачивки (или сайт ещё не получал отчётов с игрового сервера).
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function AchievementsPage() {
  const queryClient = useQueryClient()
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<AchievementRow | null>(null)
  const [query, setQuery] = useState("")
  const [category, setCategory] = useState("all")
  const [status, setStatus] = useState("all")

  const { data, isLoading } = useQuery({
    queryKey: ["achievements"],
    queryFn: () => api.get<AchievementsListResponse>("/api/v1/admin/achievements"),
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["achievements"] })

  const all = useMemo(() => data?.achievements ?? [], [data])
  const categories = useMemo(
    () => Array.from(new Set(all.map((a) => a.category).filter(Boolean))) as string[],
    [all],
  )

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return all.filter((a) => {
      if (category !== "all" && (a.category ?? "") !== category) return false
      if (status === "enabled" && !a.enabled) return false
      if (status === "disabled" && a.enabled) return false
      if (!needle) return true
      return a.key.includes(needle)
        || a.title.toLowerCase().includes(needle)
        || (a.description ?? "").toLowerCase().includes(needle)
        || String(a.trigger?.event ?? "").includes(needle)
    })
  }, [all, query, category, status])

  const grouped = useMemo(() => {
    const map = new Map<string, AchievementRow[]>()
    for (const a of filtered) {
      const key = a.chain_group || "__none__"
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(a)
    }
    for (const rows of map.values()) {
      rows.sort((a, b) => a.chain_order - b.chain_order || a.sort_order - b.sort_order || a.key.localeCompare(b.key))
    }
    // Цепочки — сверху: это связанные наборы, их читают целиком.
    return Array.from(map.entries()).sort(([a], [b]) =>
      a === "__none__" ? 1 : b === "__none__" ? -1 : a.localeCompare(b),
    )
  }, [filtered])

  const openEditor = (row: AchievementRow | null) => { setEditing(row); setEditorOpen(true) }

  return (
    <div>
      <PageHeader
        title="Ачивки"
        description="Конструктор: событие в игре → условия → награда, с цепочками и предпосылками"
        icon={Trophy}
        actions={data?.can_edit ? (
          <Button onClick={() => openEditor(null)}>
            <Plus className="size-4" /> Новая ачивка
          </Button>
        ) : undefined}
      />

      <Tabs defaultValue="list" className="space-y-4">
        <TabsList>
          <TabsTrigger value="list">Ачивки{all.length ? ` · ${all.length}` : ""}</TabsTrigger>
          <TabsTrigger value="player">Прогресс игрока</TabsTrigger>
        </TabsList>

        <TabsContent value="list" className="space-y-4">
          <Card>
            <CardContent className="flex flex-wrap items-end gap-2 pt-4">
              <div className="min-w-60 flex-1">
                <Label className="mb-1.5 block text-xs">Поиск</Label>
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Название, ключ, описание или событие"
                />
              </div>
              <div>
                <Label className="mb-1.5 block text-xs">Категория</Label>
                <Select value={category} onValueChange={setCategory}>
                  <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Все категории</SelectItem>
                    {categories.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="mb-1.5 block text-xs">Состояние</Label>
                <Select value={status} onValueChange={setStatus}>
                  <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Любое</SelectItem>
                    <SelectItem value="enabled">Активные</SelectItem>
                    <SelectItem value="disabled">Отключённые</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>

          {isLoading && (
            <div className="grid gap-3 sm:grid-cols-2">
              {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40 w-full" />)}
            </div>
          )}

          {data && grouped.map(([group, rows]) => (
            <div key={group} className="space-y-2">
              {group !== "__none__" && (
                <div className="text-sm font-semibold text-muted-foreground">
                  Цепочка: {group} <span className="font-normal">({rows.length})</span>
                </div>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                {rows.map((row) => (
                  <AchievementCard
                    key={row.key}
                    row={row}
                    canEdit={!!data.can_edit}
                    reference={data.reference}
                    onEdit={() => openEditor(row)}
                    onChanged={invalidate}
                  />
                ))}
              </div>
            </div>
          ))}

          {data && filtered.length === 0 && (
            <div className="py-8 text-center text-muted-foreground">
              {all.length === 0
                ? "Ачивок пока нет — создайте первую кнопкой «Новая ачивка»."
                : "Ничего не найдено по этим фильтрам."}
            </div>
          )}
        </TabsContent>

        <TabsContent value="player">
          <PlayerProgressTab achievements={all} canGrant={!!data?.can_grant} />
        </TabsContent>
      </Tabs>

      {editorOpen && data && (
        <AchievementEditor
          key={editing?.key ?? "__new__"}
          open={editorOpen}
          onOpenChange={setEditorOpen}
          editing={editing}
          reference={data.reference}
          allKeys={all.map((a) => ({ key: a.key, title: a.title }))}
          onSaved={invalidate}
        />
      )}
    </div>
  )
}
