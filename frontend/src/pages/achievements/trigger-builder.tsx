/** Конструктор триггера: событие → режим подсчёта → модификаторы → дерево
 *  условий. Собирает ровно тот объект trigger, который валидирует
 *  validate_trigger в services_achievements.py и исполняет плагин.
 *
 *  Три режима подсчёта в JSON — взаимоисключающие поля (обычный счётчик /
 *  distinct_by / sum_field); здесь это один переключатель, поэтому собрать
 *  заведомо невалидную комбинацию через конструктор невозможно.
 */
import { AlertTriangle, Plus } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { SearchPicker, type Option } from "./pickers"
import { ConditionEditor } from "./condition-builder"
import {
  blankLeaf, countModeOf,
  type AchievementsReference, type ConditionNode, type CountMode, type TriggerSpec,
} from "./types"

/** Дублирует список из validate_trigger (services_achievements.py): те же
 *  события, для которых сервер выдаёт предупреждение о накрутке — показываем
 *  его сразу в конструкторе, до сохранения. */
const FREQUENT_EVENTS = new Set([
  "item_used", "door_used", "chat_message", "players_nearby", "hull_status",
  "weapon_fired", "melee_attack", "depth_reached", "container_opened",
])

const MODE_LABELS: Record<CountMode, string> = {
  events: "Считать срабатывания",
  distinct: "Считать разные значения поля",
  sum: "Суммировать числовое поле",
}

const MODE_HINTS: Record<CountMode, string> = {
  events: "+1 за каждое подходящее событие (убил монстра, выполнил задание…).",
  distinct: "Прогресс = число РАЗНЫХ значений поля: например, разных видов монстров или разных предметов.",
  sum: "Прогресс = сумма значений числового поля: пройденное расстояние, суммарный урон, заработанные деньги.",
}

/** Опциональное число: пустая строка = модификатор выключен (в trigger он
 *  тогда не попадёт вовсе — см. cleanTrigger). */
function OptionalNumber({ label, value, onChange, placeholder, hint, disabled, min = 0 }: {
  label: string
  value: number | undefined
  onChange: (v: number | undefined) => void
  placeholder?: string
  hint?: string
  disabled?: boolean
  min?: number
}) {
  return (
    <div>
      <Label className="mb-1 block text-[11px]">{label}</Label>
      <Input
        type="number" min={min}
        value={value ?? ""}
        disabled={disabled}
        placeholder={placeholder ?? "выкл."}
        onChange={(e) => onChange(e.target.value === "" ? undefined : Math.max(min, Number(e.target.value) || 0))}
        className="h-9"
      />
      {hint && <p className="mt-0.5 text-[10px] leading-tight text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function TriggerBuilder({ trigger, onChange, reference }: {
  trigger: TriggerSpec
  onChange: (t: TriggerSpec) => void
  reference: AchievementsReference
}) {
  const eventDef = reference.events?.[trigger.event]
  const mode = countModeOf(trigger)
  const set = (patch: Partial<TriggerSpec>) => onChange({ ...trigger, ...patch })

  const eventOptions: Option[] = Object.entries(reference.events ?? {}).map(([key, ev]) => ({
    value: key,
    // Метки событий длинные (с пояснением в скобках) — в списке режем до сути.
    label: ev.label.split(" (")[0],
    hint: key,
  }))

  const fieldOptions: Option[] = Object.entries(eventDef?.fields ?? {}).map(([f, def]) => ({
    value: f, label: def.label, hint: f,
  }))
  const numericFieldOptions = fieldOptions.filter((o) => reference.field_kinds?.[o.value] === "number")

  const setMode = (next: CountMode) => {
    const base: TriggerSpec = { ...trigger, distinct_by: undefined, sum_field: undefined }
    if (next === "distinct") {
      base.distinct_by = trigger.distinct_by ?? fieldOptions[0]?.value ?? ""
      base.window_seconds = undefined   // валидатор запрещает окно вместе с distinct_by/sum_field
    }
    if (next === "sum") {
      base.sum_field = trigger.sum_field ?? numericFieldOptions[0]?.value ?? fieldOptions[0]?.value ?? ""
      base.window_seconds = undefined
    }
    onChange(base)
  }

  const setEvent = (event: string) => {
    // Поля у другого события другие — условия и поля подсчёта от прежнего
    // события были бы «неизвестными полями» и всплыли предупреждениями.
    onChange({ ...trigger, event, conditions: undefined, distinct_by: undefined, sum_field: undefined })
  }

  const frequentWarning = FREQUENT_EVENTS.has(trigger.event)
    && !trigger.cooldown_seconds && !trigger.window_seconds && !trigger.distinct_by

  const countLabel = mode === "sum" ? "Нужно набрать (сумма)"
    : mode === "distinct" ? "Нужно разных значений"
      : "Нужно срабатываний"

  return (
    <div className="space-y-4">
      <div className="grid gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div>
          <Label className="mb-1 block text-xs">Событие *</Label>
          <SearchPicker options={eventOptions} value={trigger.event} onPick={setEvent} className="w-full" />
          {eventDef && <p className="mt-1 text-[11px] leading-tight text-muted-foreground">{eventDef.label}</p>}
        </div>
        <div>
          <Label className="mb-1 block text-xs">{countLabel}</Label>
          <Input
            type="number" min={1}
            value={trigger.count ?? 1}
            onChange={(e) => set({ count: Math.max(1, Number(e.target.value) || 1) })}
            className="h-9"
          />
        </div>
      </div>

      <div>
        <Label className="mb-1.5 block text-xs">Как считается прогресс</Label>
        <div className="grid gap-1.5 sm:grid-cols-3">
          {(Object.keys(MODE_LABELS) as CountMode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`rounded-md border p-2 text-left text-xs transition ${
                mode === m ? "border-primary bg-primary/10" : "hover:bg-accent"
              }`}
            >
              <div className="font-semibold">{MODE_LABELS[m]}</div>
              <div className="mt-0.5 text-[10px] leading-tight text-muted-foreground">{MODE_HINTS[m]}</div>
            </button>
          ))}
        </div>

        {mode === "distinct" && (
          <div className="mt-2">
            <Label className="mb-1 block text-[11px]">Поле, разные значения которого считаем</Label>
            <SearchPicker
              options={fieldOptions}
              value={trigger.distinct_by ?? ""}
              onPick={(v) => set({ distinct_by: v })}
              className="w-full sm:w-80"
            />
          </div>
        )}
        {mode === "sum" && (
          <div className="mt-2">
            <Label className="mb-1 block text-[11px]">Числовое поле, значения которого суммируем</Label>
            <SearchPicker
              options={numericFieldOptions.length ? numericFieldOptions : fieldOptions}
              value={trigger.sum_field ?? ""}
              onPick={(v) => set({ sum_field: v })}
              className="w-full sm:w-80"
            />
            {!numericFieldOptions.length && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                У этого события нет заведомо числовых полей — сервер предупредит при сохранении.
              </p>
            )}
          </div>
        )}
      </div>

      <div>
        <Label className="mb-1.5 block text-xs">Ограничители накрутки</Label>
        <div className="grid gap-2 rounded-md border p-2 sm:grid-cols-4">
          <OptionalNumber
            label="Кулдаун, сек"
            value={trigger.cooldown_seconds}
            onChange={(v) => set({ cooldown_seconds: v })}
            hint="Минимум между засчётами"
          />
          <OptionalNumber
            label="Максимум за раунд"
            value={trigger.max_per_round}
            onChange={(v) => set({ max_per_round: v })}
            min={1}
            hint="Больше этого за раунд не начислится"
          />
          <OptionalNumber
            label="Окно, сек"
            value={trigger.window_seconds}
            onChange={(v) => set({ window_seconds: v })}
            min={1}
            disabled={mode !== "events"}
            hint={mode === "events" ? "N срабатываний подряд за это время" : "Только для режима «считать срабатывания»"}
          />
          <label className="flex items-end gap-2 pb-1 text-xs">
            <Checkbox
              checked={!!trigger.reset_on_death}
              onCheckedChange={(c) => set({ reset_on_death: c === true })}
            />
            Сбрасывать прогресс при смерти
          </label>
        </div>
        {frequentWarning && (
          <p className="mt-1 flex items-start gap-1 text-[11px] text-warning">
            <AlertTriangle className="mt-0.5 size-3 shrink-0" />
            Событие срабатывает часто — без кулдауна или окна ачивку можно накрутить за секунды.
          </p>
        )}
      </div>

      <div>
        <div className="mb-1.5 flex flex-wrap items-center justify-between gap-1">
          <Label className="text-xs">Условия — когда событие засчитывается</Label>
          {!trigger.conditions && (
            <div className="flex gap-1">
              <Button
                type="button" variant="outline" size="sm" className="h-7 text-xs"
                onClick={() => set({ conditions: blankLeaf(eventDef, reference) })}
              >
                <Plus className="mr-1 size-3" /> Условие
              </Button>
              <Button
                type="button" variant="outline" size="sm" className="h-7 text-xs"
                onClick={() => set({ conditions: { all: [blankLeaf(eventDef, reference)] } })}
              >
                <Plus className="mr-1 size-3" /> Группа
              </Button>
            </div>
          )}
          {trigger.conditions && "field" in trigger.conditions && (
            <Button
              type="button" variant="ghost" size="sm" className="h-7 text-xs"
              title="Чтобы добавить второе условие, оберните текущее в группу"
              onClick={() => set({ conditions: { all: [trigger.conditions as ConditionNode] } })}
            >
              обернуть в группу
            </Button>
          )}
        </div>
        {trigger.conditions ? (
          <ConditionEditor
            node={trigger.conditions}
            onChange={(n) => set({ conditions: n })}
            onRemove={() => set({ conditions: undefined })}
            eventDef={eventDef}
            reference={reference}
          />
        ) : (
          <p className="rounded-md border border-dashed py-4 text-center text-xs text-muted-foreground">
            Условий нет — засчитывается любое событие «{eventDef?.label.split(" (")[0] ?? trigger.event}»
          </p>
        )}
      </div>

      <div className="rounded-md border bg-muted/30 p-2 text-xs">
        <span className="font-semibold">Итог: </span>
        <TriggerSummary trigger={trigger} reference={reference} />
      </div>
    </div>
  )
}

/** Человеческое описание триггера — та же строка используется в карточке
 *  ачивки в списке, чтобы не читать JSON. */
export function TriggerSummary({ trigger, reference }: {
  trigger: TriggerSpec
  reference: AchievementsReference
}) {
  const eventDef = reference.events?.[trigger.event]
  const eventLabel = eventDef?.label.split(" (")[0] ?? trigger.event
  const fieldLabel = (f?: string) => (f ? eventDef?.fields?.[f]?.label ?? f : "")
  const count = trigger.count ?? 1

  const what = trigger.sum_field
    ? `набрать ${count} по полю «${fieldLabel(trigger.sum_field)}»`
    : trigger.distinct_by
      ? `${count} разных значений поля «${fieldLabel(trigger.distinct_by)}»`
      : `${count} × событие «${eventLabel}»`

  const extras: string[] = []
  if (trigger.sum_field || trigger.distinct_by) extras.push(`событие «${eventLabel}»`)
  if (trigger.conditions) extras.push("с условиями")
  if (trigger.window_seconds) extras.push(`за ${trigger.window_seconds} с`)
  if (trigger.cooldown_seconds) extras.push(`кулдаун ${trigger.cooldown_seconds} с`)
  if (trigger.max_per_round) extras.push(`не более ${trigger.max_per_round} за раунд`)
  if (trigger.reset_on_death) extras.push("сброс при смерти")

  return <span className="text-muted-foreground">{what}{extras.length ? ` · ${extras.join(" · ")}` : ""}</span>
}
