/** Конструктор ачивки целиком: основное → триггер → награды → JSON.
 *
 *  Источник истины — структура формы, а не текст JSON (как в конструкторе
 *  кукол, respawn-dolls/DollEditor.tsx): вкладки правят объект, вкладка JSON
 *  показывает и принимает ровно его же. При невалидном тексте правки просто
 *  не применяются, вместо того чтобы затирать собранную ачивку.
 */
import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { Textarea } from "@/components/ui/textarea"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { api } from "@/lib/api"
import { PlatformIconField } from "@/components/layout/PlatformIcon"
import { IS_V2 } from "@/lib/ui-version"
import { ChipsField, type Option } from "./pickers"
import { TriggerBuilder } from "./trigger-builder"
import { RewardsBuilder } from "./rewards-builder"
import {
  EMPTY_FORM, cleanTrigger, formToPayload, rowToForm,
  type AchievementFormState, type AchievementRow, type AchievementsReference,
  type RewardSpec, type TriggerSpec,
} from "./types"

const ICON_PRESETS = ["🏆", "🥇", "⭐", "💀", "🔧", "⚡", "🩺", "🐙", "🚢", "🔥", "💰", "🧭", "🧪", "🎯"]

export function AchievementEditor({ open, onOpenChange, editing, reference, allKeys, onSaved }: {
  open: boolean
  onOpenChange: (o: boolean) => void
  /** null — создание новой ачивки. */
  editing: AchievementRow | null
  reference: AchievementsReference
  /** Ключи существующих ачивок — для поля «требует» (без самой себя). */
  allKeys: { key: string; title: string }[]
  onSaved: () => void
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<AchievementFormState>(() => (editing ? rowToForm(editing) : EMPTY_FORM))
  const [allowKeyChange, setAllowKeyChange] = useState(false)
  const [jsonText, setJsonText] = useState("")
  const [jsonError, setJsonError] = useState<string | null>(null)

  const set = (patch: Partial<AchievementFormState>) => setForm((f) => ({ ...f, ...patch }))
  const keyChanged = !!editing && form.key.trim().toLowerCase() !== editing.key

  const save = useMutation({
    mutationFn: () => {
      const payload = formToPayload(form)
      return editing
        ? api.patch<{ warnings?: string[] }>(`/api/v1/admin/achievements/${editing.key}`, {
          ...payload, ...(keyChanged ? { allow_key_change: true } : {}),
        })
        : api.post<{ warnings?: string[] }>("/api/v1/admin/achievements", payload)
    },
    onSuccess: (res) => {
      toast.success(editing ? "Ачивка сохранена" : "Ачивка создана")
      ;(res?.warnings ?? []).forEach((w) => toast.warning(w))
      queryClient.invalidateQueries({ queryKey: ["achievements"] })
      onSaved()
      onOpenChange(false)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const requireOptions: Option[] = allKeys
    .filter((a) => a.key !== (editing?.key ?? ""))
    .map((a) => ({ value: a.key, label: a.title, hint: a.key }))
  const serverOptions: Option[] = (reference.servers ?? []).map((s) => ({ value: s, label: s }))

  const canSave = !!form.key.trim() && !!form.title.trim() && !!form.trigger.event
    && !jsonError && (!keyChanged || allowKeyChange)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? `Изменить ачивку «${editing.title}»` : "Новая ачивка"}</DialogTitle>
          <DialogDescription>
            Событие в игре → условия → сколько раз → награда. Определение целиком уезжает на игровые
            серверы, прогресс плагин считает сам и отчитывается сюда.
          </DialogDescription>
        </DialogHeader>

        <Tabs
          defaultValue="main"
          onValueChange={(v) => {
            // JSON собираем при переходе на вкладку, а не на каждый ввод.
            if (v === "json") {
              setJsonText(JSON.stringify({ trigger: cleanTrigger(form.trigger), rewards: form.rewards }, null, 2))
              setJsonError(null)
            }
          }}
        >
          <TabsList className="w-full">
            <TabsTrigger value="main" className="flex-1">Основное</TabsTrigger>
            <TabsTrigger value="trigger" className="flex-1">Триггер</TabsTrigger>
            <TabsTrigger value="rewards" className="flex-1">
              Награды{form.rewards.length ? ` · ${form.rewards.length}` : ""}
            </TabsTrigger>
            <TabsTrigger value="json" className="flex-1">JSON</TabsTrigger>
          </TabsList>

          <TabsContent value="main" className="mt-3 space-y-3">
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
              <div>
                <Label className="mb-1 block text-xs">Ключ (key) *</Label>
                <Input
                  value={form.key}
                  onChange={(e) => set({ key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })}
                  placeholder="kill_5_crawlers"
                  className="h-9 font-mono"
                />
                <p className="mt-0.5 text-[10px] text-muted-foreground">2..64 символов, только a-z, 0-9 и _</p>
              </div>
              <div>
                <Label className="mb-1 block text-xs">Название *</Label>
                <Input
                  value={form.title}
                  onChange={(e) => set({ title: e.target.value })}
                  placeholder="Охотник на кроулеров"
                  className="h-9"
                />
              </div>
            </div>

            {keyChanged && (
              <div className="rounded-md border border-destructive/50 bg-destructive/5 p-2">
                <p className="flex items-start gap-1 text-xs text-destructive">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                  Смена ключа «{editing?.key}» → «{form.key}» рвёт прогресс игроков: он записан по старому
                  ключу и после переименования не найдётся.
                </p>
                <label className="mt-1.5 flex items-center gap-2 text-xs">
                  <Checkbox checked={allowKeyChange} onCheckedChange={(c) => setAllowKeyChange(c === true)} />
                  Понимаю, всё равно сменить ключ
                </label>
              </div>
            )}

            <div>
              <Label className="mb-1 block text-xs">Описание</Label>
              <Input
                value={form.description}
                onChange={(e) => set({ description: e.target.value })}
                placeholder="Убейте 5 кроулеров"
                className="h-9"
              />
            </div>

            <div className="grid gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
              <div>
                <Label className="mb-1 block text-xs">Иконка</Label>
                <div className="flex items-center gap-1.5">
                  <PlatformIconField value={form.icon} onChange={(icon) => set({ icon })} className={IS_V2 ? "w-full" : "h-9 w-16 text-center text-lg"} placeholder="🏆" />
                  {!IS_V2 && (
                    <div className="flex flex-wrap gap-0.5">
                      {ICON_PRESETS.map((i) => (
                        <button
                          key={i}
                          type="button"
                          onClick={() => set({ icon: i })}
                          className={`rounded px-1 text-lg transition hover:bg-accent ${form.icon === i ? "bg-primary/10" : ""}`}
                        >
                          {i}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              <div>
                <Label className="mb-1 block text-xs">Категория</Label>
                <Input
                  value={form.category}
                  onChange={(e) => set({ category: e.target.value })}
                  placeholder="combat"
                  className="h-9"
                />
              </div>
              <div>
                <Label className="mb-1 block text-xs">Область счётчика</Label>
                <Select value={form.scope} onValueChange={(v) => set({ scope: v as "persistent" | "round" })}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="persistent">копится всегда</SelectItem>
                    <SelectItem value="round">сбрасывается каждый раунд</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label className="mb-1 block text-xs">Серверы (пусто = все)</Label>
                <ChipsField
                  values={form.servers}
                  onChange={(servers) => set({ servers })}
                  options={serverOptions.length ? serverOptions : undefined}
                  placeholder="Добавить сервер"
                  mono
                />
              </div>
              <div>
                <Label className="mb-1 block text-xs">Требует другие ачивки</Label>
                <ChipsField
                  values={form.requires}
                  onChange={(requires) => set({ requires })}
                  options={requireOptions}
                  placeholder="Добавить предпосылку"
                />
                <p className="mt-0.5 text-[10px] text-muted-foreground">
                  Пока они не разблокированы, эта не считается вовсе
                </p>
              </div>
            </div>

            <div className="grid gap-2 sm:grid-cols-3">
              <div>
                <Label className="mb-1 block text-xs">Цепочка</Label>
                <Input
                  value={form.chainGroup}
                  onChange={(e) => set({ chainGroup: e.target.value })}
                  placeholder="crawler_hunter"
                  className="h-9"
                />
              </div>
              <div>
                <Label className="mb-1 block text-xs">Порядок в цепочке</Label>
                <Input
                  type="number" value={form.chainOrder}
                  onChange={(e) => set({ chainOrder: e.target.value })}
                  className="h-9"
                />
              </div>
              <div>
                <Label className="mb-1 block text-xs">Порядок сортировки</Label>
                <Input
                  type="number" value={form.sortOrder}
                  onChange={(e) => set({ sortOrder: e.target.value })}
                  className="h-9"
                />
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-4 rounded-md border p-2">
              <label className="flex items-center gap-1.5 text-xs">
                <Checkbox checked={form.enabled} onCheckedChange={(c) => set({ enabled: c === true })} />
                Активна (уезжает на серверы)
              </label>
              <label className="flex items-center gap-1.5 text-xs">
                <Checkbox checked={form.hidden} onCheckedChange={(c) => set({ hidden: c === true })} />
                Скрытая до разблокировки
              </label>
              <label className="flex items-center gap-1.5 text-xs">
                <Checkbox checked={form.notifyDiscord} onCheckedChange={(c) => set({ notifyDiscord: c === true })} />
                ЛС от бота при разблокировке
              </label>
            </div>
          </TabsContent>

          <TabsContent value="trigger" className="mt-3">
            <TriggerBuilder
              trigger={form.trigger}
              onChange={(trigger) => set({ trigger })}
              reference={reference}
            />
          </TabsContent>

          <TabsContent value="rewards" className="mt-3">
            <RewardsBuilder
              rewards={form.rewards}
              onChange={(rewards) => set({ rewards })}
              reference={reference}
              achievementKey={form.key}
            />
          </TabsContent>

          <TabsContent value="json" className="mt-3 space-y-2">
            <p className="text-xs text-muted-foreground">
              Ровно то, что уйдёт на игровой сервер (триггер и награды). Правки применяются к
              конструктору, если JSON корректен.
            </p>
            <Textarea
              rows={20}
              value={jsonText}
              spellCheck={false}
              className="font-mono text-xs"
              onChange={(e) => {
                setJsonText(e.target.value)
                try {
                  const parsed = JSON.parse(e.target.value || "{}") as {
                    trigger?: TriggerSpec
                    rewards?: RewardSpec[]
                  }
                  set({
                    trigger: { ...(parsed.trigger ?? {}), event: parsed.trigger?.event ?? form.trigger.event },
                    rewards: parsed.rewards ?? [],
                  })
                  setJsonError(null)
                } catch (err) {
                  setJsonError((err as Error).message)
                }
              }}
            />
            {jsonError && <p className="text-xs text-destructive">JSON некорректен: {jsonError}</p>}
          </TabsContent>
        </Tabs>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Отмена</Button>
          <Button onClick={() => save.mutate()} disabled={!canSave || save.isPending}>
            {editing ? "Сохранить" : "Создать"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
