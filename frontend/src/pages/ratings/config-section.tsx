import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Settings2, RotateCcw, Save, PlusCircle, MinusCircle } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { ConfirmDialog } from "@/components/layout/ConfirmDialog"
import { api } from "@/lib/api"

const ROLE_LABELS: Record<string, string> = {
  captain: "Капитан", mechanic: "Механик", medic: "Врач", security: "СБ", engineer: "Инженер",
}

interface RatingConfig {
  K: number
  POWER: number
  DECAY_THRESHOLD_DAYS: number
  DECAY_RATE_PER_WEEK: number
  DAILY_EROSION: number
  INACTIVE_EXTRA_EROSION: number
  DECAY_FACTOR_FLOOR: number
  WEIGHT_MATRIX: Record<string, Record<string, number>>
  PENALTY_MAP: Record<string, Record<string, number>>
}
interface ConfigResponse { config: RatingConfig; defaults: RatingConfig; roles: string[] }

const SCALAR_FIELDS: { key: keyof RatingConfig; label: string; hint: string; step: string }[] = [
  { key: "K", label: "K (масштаб)", hint: "Rating = K * points^POWER", step: "0.01" },
  { key: "POWER", label: "POWER (степень)", hint: "< 1 сжимает верх шкалы", step: "0.01" },
  { key: "DECAY_THRESHOLD_DAYS", label: "Порог неактивности, дней", hint: "После скольки дней без игры включается доп. эрозия", step: "1" },
  { key: "DECAY_RATE_PER_WEEK", label: "Затухание в неделю", hint: "Легаси-поле, не используется формулой v3", step: "0.01" },
  { key: "DAILY_EROSION", label: "Базовая эрозия в день", hint: "Применяется ко всем ежедневно", step: "0.0001" },
  { key: "INACTIVE_EXTRA_EROSION", label: "Доп. эрозия неактивным", hint: "Сверх базовой, после порога неактивности", step: "0.0001" },
  { key: "DECAY_FACTOR_FLOOR", label: "Пол коэффициента затухания", hint: "decay_factor не опускается ниже этого значения", step: "0.01" },
]

function cloneConfig(cfg: RatingConfig): RatingConfig {
  return JSON.parse(JSON.stringify(cfg))
}

/** Superuser-only panel: full rating-formula configuration + manual rating
 * adjustment. Hidden entirely for non-superusers (gated by the parent). */
export function RatingConfigSection() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<RatingConfig | null>(null)
  const [resetOpen, setResetOpen] = useState(false)

  const [adjSteamId, setAdjSteamId] = useState("")
  const [adjRole, setAdjRole] = useState("all")
  const [adjDelta, setAdjDelta] = useState("")
  const [adjReason, setAdjReason] = useState("")

  const { data, isLoading } = useQuery({
    queryKey: ["ratings-config"],
    queryFn: () => api.get<ConfigResponse>("/api/v1/admin/ratings/config"),
  })

  useEffect(() => {
    if (data && !draft) setDraft(cloneConfig(data.config))
  }, [data, draft])

  const save = useMutation({
    mutationFn: (cfg: RatingConfig) => api.put<{ config: RatingConfig }>("/api/v1/admin/ratings/config", cfg),
    onSuccess: (r) => {
      toast.success("Настройки рейтинга сохранены")
      setDraft(cloneConfig(r.config))
      queryClient.invalidateQueries({ queryKey: ["ratings-config"] })
      queryClient.invalidateQueries({ queryKey: ["ratings"] })
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Не удалось сохранить"),
  })

  const reset = useMutation({
    mutationFn: () => api.post<{ config: RatingConfig }>("/api/v1/admin/ratings/config/reset"),
    onSuccess: (r) => {
      toast.success("Настройки сброшены на значения по умолчанию")
      setDraft(cloneConfig(r.config))
      queryClient.invalidateQueries({ queryKey: ["ratings-config"] })
      queryClient.invalidateQueries({ queryKey: ["ratings"] })
    },
  })

  const adjust = useMutation({
    mutationFn: () => api.post("/api/v1/admin/ratings/adjust", {
      steam_id: adjSteamId.trim(),
      role: adjRole,
      points_delta: Number(adjDelta),
      reason: adjReason.trim(),
    }),
    onSuccess: () => {
      toast.success("Рейтинг изменён")
      setAdjDelta("")
      setAdjReason("")
      queryClient.invalidateQueries({ queryKey: ["ratings"] })
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Не удалось изменить рейтинг"),
  })

  if (isLoading || !draft) {
    return <Card className="mb-4"><CardContent className="p-4 text-sm text-muted-foreground">Загрузка настроек рейтинга...</CardContent></Card>
  }

  const roles = data?.roles ?? Object.keys(draft.WEIGHT_MATRIX)
  const actionKeys = Array.from(new Set(roles.flatMap((r) => Object.keys(draft.WEIGHT_MATRIX[r] || {}))))
  const eventKeys = Object.keys(draft.PENALTY_MAP)

  const setScalar = (key: keyof RatingConfig, value: string) => {
    const n = Number(value)
    setDraft((d) => (d ? { ...d, [key]: Number.isFinite(n) ? n : d[key] } : d))
  }
  const setWeight = (role: string, action: string, value: string) => {
    const n = Number(value)
    if (!Number.isFinite(n)) return
    setDraft((d) => d ? {
      ...d,
      WEIGHT_MATRIX: { ...d.WEIGHT_MATRIX, [role]: { ...d.WEIGHT_MATRIX[role], [action]: n } },
    } : d)
  }
  const setPenalty = (event: string, role: string, value: string) => {
    const n = Number(value)
    if (!Number.isFinite(n)) return
    setDraft((d) => d ? {
      ...d,
      PENALTY_MAP: { ...d.PENALTY_MAP, [event]: { ...d.PENALTY_MAP[event], [role]: n } },
    } : d)
  }

  return (
    <>
      <Card className="mb-4">
        <CardHeader className="flex flex-row items-center justify-between border-b py-3">
          <CardTitle className="flex items-center gap-2 text-sm font-semibold">
            <Settings2 className="h-4 w-4" /> Настройка расчёта рейтинга (только для суперпользователей)
          </CardTitle>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setResetOpen(true)}>
              <RotateCcw className="h-4 w-4" /> Сбросить
            </Button>
            <Button size="sm" onClick={() => save.mutate(draft)} disabled={save.isPending}>
              <Save className="h-4 w-4" /> Сохранить
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-6 pt-4">
          <div>
            <h4 className="mb-2 text-sm font-medium text-muted-foreground">Формула и затухание</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {SCALAR_FIELDS.map((f) => (
                <div key={f.key} className="space-y-1">
                  <Label className="text-xs">{f.label}</Label>
                  <Input
                    type="number"
                    step={f.step}
                    value={draft[f.key] as number}
                    onChange={(e) => setScalar(f.key, e.target.value)}
                  />
                  <p className="text-[11px] text-muted-foreground">{f.hint}</p>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h4 className="mb-2 text-sm font-medium text-muted-foreground">Веса действий по ролям</h4>
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Действие</TableHead>
                    {roles.map((r) => <TableHead key={r}>{ROLE_LABELS[r] ?? r}</TableHead>)}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {actionKeys.map((action) => (
                    <TableRow key={action}>
                      <TableCell className="whitespace-nowrap font-mono text-xs">{action}</TableCell>
                      {roles.map((r) => (
                        <TableCell key={r}>
                          {action in (draft.WEIGHT_MATRIX[r] || {}) ? (
                            <Input
                              type="number" step="0.1" className="h-8 w-24"
                              value={draft.WEIGHT_MATRIX[r][action]}
                              onChange={(e) => setWeight(r, action, e.target.value)}
                            />
                          ) : <span className="text-muted-foreground">—</span>}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>

          <div>
            <h4 className="mb-2 text-sm font-medium text-muted-foreground">Штрафы по событиям</h4>
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Событие</TableHead>
                    {roles.map((r) => <TableHead key={r}>{ROLE_LABELS[r] ?? r}</TableHead>)}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {eventKeys.map((event) => (
                    <TableRow key={event}>
                      <TableCell className="whitespace-nowrap font-mono text-xs">{event}</TableCell>
                      {roles.map((r) => (
                        <TableCell key={r}>
                          {r in (draft.PENALTY_MAP[event] || {}) ? (
                            <Input
                              type="number" step="1" className="h-8 w-24"
                              value={draft.PENALTY_MAP[event][r]}
                              onChange={(e) => setPenalty(event, r, e.target.value)}
                            />
                          ) : <span className="text-muted-foreground">—</span>}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardHeader className="border-b py-3">
          <CardTitle className="text-sm font-semibold">Ручное изменение рейтинга игрока</CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1">
              <Label className="text-xs">SteamID игрока</Label>
              <Input value={adjSteamId} onChange={(e) => setAdjSteamId(e.target.value)} placeholder="7656119..." />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Роль</Label>
              <Select value={adjRole} onValueChange={setAdjRole}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Все роли</SelectItem>
                  {roles.map((r) => <SelectItem key={r} value={r}>{ROLE_LABELS[r] ?? r}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Изменение очков (+/-)</Label>
              <Input type="number" step="1" value={adjDelta} onChange={(e) => setAdjDelta(e.target.value)} placeholder="например -500 или 1000" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Причина</Label>
              <Textarea className="min-h-9" value={adjReason} onChange={(e) => setAdjReason(e.target.value)} placeholder="необязательно" />
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <Button
              size="sm" variant="outline"
              disabled={!adjSteamId.trim() || !adjDelta || adjust.isPending}
              onClick={() => setAdjDelta((v) => (Number(v) > 0 ? String(-Number(v)) : v))}
            >
              <MinusCircle className="h-4 w-4" /> Пометить как вычитание
            </Button>
            <Button
              size="sm"
              disabled={!adjSteamId.trim() || !adjDelta || Number(adjDelta) === 0 || adjust.isPending}
              onClick={() => adjust.mutate()}
            >
              <PlusCircle className="h-4 w-4" /> Применить
            </Button>
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Сбросить настройки рейтинга?"
        description="Все веса, штрафы и параметры формулы вернутся к значениям по умолчанию."
        confirmLabel="Сбросить"
        onConfirm={() => { reset.mutate(); setResetOpen(false) }}
      />
    </>
  )
}
