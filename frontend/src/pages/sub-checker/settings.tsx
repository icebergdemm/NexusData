import { useMemo, useState } from "react"
import { Plus, RotateCcw, Save, Search, Trash2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import type {
  CheckerConfig, CustomRuleSpec, ForbiddenItem, RuleSpec, Severity,
} from "./types"

const SEVERITIES: { value: Severity; label: string }[] = [
  { value: "error", label: "Нарушение" },
  { value: "warning", label: "Предупреждение" },
  { value: "info", label: "К сведению" },
]

const EQUIPMENT_LABELS: Record<string, string> = {
  reactors: "Реакторы", engines: "Двигатели", pumps: "Насосы",
  batteries: "Батареи и суперконденсаторы", oxygen_generators: "Генераторы кислорода",
  power: "Энергосистема",
}

const FIELD_LABELS: Record<string, string> = {
  base_power_default: "Базовая мощность (по умолчанию)",
  base_power_by_type: "Базовая мощность по типу",
  min_scale_expr: "Формула минимального масштаба",
  min_fuel_consumption_rate: "Минимальный расход топлива",
  min_scale: "Минимальный масштаб",
  max_force_default: "Предел силы (по умолчанию)",
  max_force_by_type: "Предел силы по типу",
  power_factor_default: "Коэффициент потребления (по умолчанию)",
  power_factor_by_type: "Коэффициент потребления по типу",
  min_power_expr: "Формула минимального потребления",
  allowed_types: "Разрешённые типы",
  base_flow_default: "Базовый поток (по умолчанию)",
  base_flow_by_type: "Базовый поток по типу",
  ballast_tag: "Метка балластного насоса",
  base_capacity_default: "Базовая ёмкость (по умолчанию)",
  base_capacity_by_type: "Базовая ёмкость по типу",
  scale_tolerance: "Допуск по масштабу",
  tiers: "Ступени производства O₂",
  max_power_distributors: "Максимум распределительных щитков",
  power_load_min: "Минимальная загрузка энергосистемы",
  power_load_max: "Максимальная загрузка энергосистемы",
  consumption_excluded_tags: "Компоненты вне суммы потребления",
  label: "Название",
  max_output: "Производство до",
  min_power: "Минимальное потребление",
  check_drones: "Проверять пристыкованные суда",
  count_items_globally: "Считать наполнение по всему кораблю",
}

function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? key
}

/** Список строк (теги, разрешённые типы) — по значению на строку. */
function StringListEditor({ value, onChange, disabled }: {
  value: string[]; onChange: (v: string[]) => void; disabled: boolean
}) {
  return (
    <div className="space-y-1.5">
      {value.map((item, i) => (
        <div key={i} className="flex gap-1.5">
          <Input
            value={item} disabled={disabled}
            onChange={(e) => onChange(value.map((v, j) => (j === i ? e.target.value : v)))}
          />
          <Button size="icon" variant="ghost" disabled={disabled}
            onClick={() => onChange(value.filter((_, j) => j !== i))}>
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => onChange([...value, ""])}>
        <Plus className="size-3.5" /> Добавить
      </Button>
    </div>
  )
}

/** Словарь «тип оборудования → число». */
function NumberMapEditor({ value, onChange, disabled }: {
  value: Record<string, number>; onChange: (v: Record<string, number>) => void; disabled: boolean
}) {
  const entries = Object.entries(value ?? {})
  const setKey = (oldKey: string, newKey: string) => {
    const next: Record<string, number> = {}
    for (const [k, v] of entries) next[k === oldKey ? newKey : k] = v
    onChange(next)
  }
  return (
    <div className="space-y-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-1.5">
          <Input className="flex-1" value={k} disabled={disabled} onChange={(e) => setKey(k, e.target.value)} placeholder="identifier" />
          <Input className="w-32" type="number" step="any" value={v} disabled={disabled}
            onChange={(e) => onChange({ ...value, [k]: Number(e.target.value) })} />
          <Button size="icon" variant="ghost" disabled={disabled} onClick={() => {
            const next = { ...value }; delete next[k]; onChange(next)
          }}>
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}
      <Button size="sm" variant="outline" disabled={disabled}
        onClick={() => onChange({ ...value, "": 0 })}>
        <Plus className="size-3.5" /> Добавить тип
      </Button>
    </div>
  )
}

/** Таблица объектов одинаковой формы (ступени генератора кислорода). */
function ObjectListEditor({ value, onChange, disabled }: {
  value: Record<string, number>[]; onChange: (v: Record<string, number>[]) => void; disabled: boolean
}) {
  const keys = value.length ? Object.keys(value[0]) : []
  return (
    <div className="space-y-1.5">
      {value.map((row, i) => (
        <div key={i} className="flex flex-wrap items-end gap-1.5">
          {keys.map((k) => (
            <div key={k} className="space-y-1">
              <Label className="text-[11px] text-muted-foreground">{fieldLabel(k)}</Label>
              <Input className="w-36" type="number" step="any" value={row[k]} disabled={disabled}
                onChange={(e) => onChange(value.map((r, j) => (j === i ? { ...r, [k]: Number(e.target.value) } : r)))} />
            </div>
          ))}
          <Button size="icon" variant="ghost" disabled={disabled}
            onClick={() => onChange(value.filter((_, j) => j !== i))}>
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}
      <Button size="sm" variant="outline" disabled={disabled || !keys.length}
        onClick={() => onChange([...value, Object.fromEntries(keys.map((k) => [k, 0])) as Record<string, number>])}>
        <Plus className="size-3.5" /> Добавить ступень
      </Button>
    </div>
  )
}

/** Универсальный редактор одного поля блока оборудования: форма значения
 * определяет вид элемента управления, поэтому новые поля в конфигурации
 * появляются здесь сами, без правки этого файла. */
function ValueEditor({ name, value, onChange, disabled }: {
  name: string; value: unknown; onChange: (v: unknown) => void; disabled: boolean
}) {
  if (typeof value === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <Checkbox checked={value} disabled={disabled} onCheckedChange={(c) => onChange(!!c)} />
        {fieldLabel(name)}
      </label>
    )
  }
  if (typeof value === "number") {
    return (
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">{fieldLabel(name)}</Label>
        <Input type="number" step="any" value={value} disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))} />
      </div>
    )
  }
  if (typeof value === "string") {
    const isFormula = name.endsWith("_expr")
    return (
      <div className={`space-y-1 ${isFormula ? "sm:col-span-2" : ""}`}>
        <Label className="text-xs text-muted-foreground">{fieldLabel(name)}</Label>
        <Input className={isFormula ? "font-mono text-xs" : ""} value={value} disabled={disabled}
          onChange={(e) => onChange(e.target.value)} />
      </div>
    )
  }
  if (Array.isArray(value)) {
    if (value.length && typeof value[0] === "object") {
      return (
        <div className="space-y-1 sm:col-span-2">
          <Label className="text-xs text-muted-foreground">{fieldLabel(name)}</Label>
          <ObjectListEditor value={value as Record<string, number>[]} onChange={onChange} disabled={disabled} />
        </div>
      )
    }
    return (
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">{fieldLabel(name)}</Label>
        <StringListEditor value={value as string[]} onChange={onChange} disabled={disabled} />
      </div>
    )
  }
  if (value && typeof value === "object") {
    return (
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">{fieldLabel(name)}</Label>
        <NumberMapEditor value={value as Record<string, number>} onChange={onChange} disabled={disabled} />
      </div>
    )
  }
  return null
}

function RulesSection({ rules, onChange, disabled }: {
  rules: RuleSpec[]; onChange: (r: RuleSpec[]) => void; disabled: boolean
}) {
  const patch = (id: string, changes: Partial<RuleSpec>) =>
    onChange(rules.map((r) => (r.id === id ? { ...r, ...changes } : r)))

  return (
    <div className="space-y-1.5">
      {rules.map((rule) => (
        <div key={rule.id} className="flex flex-wrap items-start gap-3 rounded-md border p-3">
          <Checkbox className="mt-1" checked={rule.enabled} disabled={disabled}
            onCheckedChange={(c) => patch(rule.id, { enabled: !!c })} />
          <div className="min-w-0 flex-1">
            <Input className="h-8 font-medium" value={rule.label} disabled={disabled}
              onChange={(e) => patch(rule.id, { label: e.target.value })} />
            {rule.description && (
              <div className="mt-1 text-xs text-muted-foreground">{rule.description}</div>
            )}
            <code className="mt-1 inline-block rounded bg-muted px-1 text-[10px] text-muted-foreground">{rule.id}</code>
          </div>
          <Select value={rule.severity} disabled={disabled}
            onValueChange={(v) => patch(rule.id, { severity: v as Severity })}>
            <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SEVERITIES.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      ))}
    </div>
  )
}

function ForbiddenSection({ items, onChange, disabled }: {
  items: ForbiddenItem[]; onChange: (i: ForbiddenItem[]) => void; disabled: boolean
}) {
  const [search, setSearch] = useState("")
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return items.map((item, index) => ({ item, index }))
    return items
      .map((item, index) => ({ item, index }))
      .filter(({ item }) =>
        item.identifier.toLowerCase().includes(term) || (item.label ?? "").toLowerCase().includes(term))
  }, [items, search])

  const patch = (index: number, changes: Partial<ForbiddenItem>) =>
    onChange(items.map((it, i) => (i === index ? { ...it, ...changes } : it)))

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative max-w-xs flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-8" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по списку" />
        </div>
        <Badge variant="secondary">Включено: {items.filter((i) => i.enabled).length} из {items.length}</Badge>
        <div className="flex-1" />
        <Button size="sm" variant="outline" disabled={disabled}
          onClick={() => onChange([{ identifier: "", label: "", enabled: true, note: "" }, ...items])}>
          <Plus className="size-3.5" /> Добавить предмет
        </Button>
      </div>
      <div className="text-xs text-muted-foreground">
        Совпадение ищется по вхождению строки в идентификатор предмета: «dementonite» ловит весь дементонит сразу.
      </div>
      <div className="max-h-[520px] space-y-1.5 overflow-y-auto pr-1">
        {filtered.map(({ item, index }) => (
          <div key={index} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
            <Checkbox checked={item.enabled} disabled={disabled}
              onCheckedChange={(c) => patch(index, { enabled: !!c })} />
            <Input className="w-56 font-mono text-xs" value={item.identifier} disabled={disabled}
              placeholder="identifier" onChange={(e) => patch(index, { identifier: e.target.value })} />
            <Input className="min-w-[180px] flex-1" value={item.label} disabled={disabled}
              placeholder="Название" onChange={(e) => patch(index, { label: e.target.value })} />
            <Input className="min-w-[180px] flex-1" value={item.note ?? ""} disabled={disabled}
              placeholder="Примечание" onChange={(e) => patch(index, { note: e.target.value })} />
            <Button size="icon" variant="ghost" disabled={disabled}
              onClick={() => onChange(items.filter((_, i) => i !== index))}>
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
        {!filtered.length && <div className="py-6 text-center text-sm text-muted-foreground">Ничего не найдено</div>}
      </div>
    </div>
  )
}

function CustomRulesSection({ rules, onChange, disabled, context }: {
  rules: CustomRuleSpec[]; onChange: (r: CustomRuleSpec[]) => void; disabled: boolean; context: string[]
}) {
  const patch = (index: number, changes: Partial<CustomRuleSpec>) =>
    onChange(rules.map((r, i) => (i === index ? { ...r, ...changes } : r)))

  return (
    <div className="space-y-3">
      <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
        Правило — это условие, которое ДОЛЖНО выполняться. Если оно ложно, в отчёт попадает замечание.
        Доступные переменные: <span className="font-mono">{context.join(", ")}</span>.
        Функции: <span className="font-mono">sqrt, ceil, floor, abs, min, max, round, log, pow</span>.
        Пример: <span className="font-mono">tonnage &lt; 20000 and max_speed &lt;= 45</span>
      </div>
      {rules.map((rule, index) => (
        <div key={index} className="space-y-2 rounded-md border p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Checkbox checked={rule.enabled} disabled={disabled}
              onCheckedChange={(c) => patch(index, { enabled: !!c })} />
            <Input className="w-40 font-mono text-xs" value={rule.id} disabled={disabled}
              placeholder="id" onChange={(e) => patch(index, { id: e.target.value })} />
            <Input className="min-w-[200px] flex-1" value={rule.label} disabled={disabled}
              placeholder="Название правила" onChange={(e) => patch(index, { label: e.target.value })} />
            <Select value={rule.severity} disabled={disabled}
              onValueChange={(v) => patch(index, { severity: v as Severity })}>
              <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                {SEVERITIES.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button size="icon" variant="ghost" disabled={disabled}
              onClick={() => onChange(rules.filter((_, i) => i !== index))}>
              <Trash2 className="size-4" />
            </Button>
          </div>
          <Input className="font-mono text-xs" value={rule.expr} disabled={disabled}
            placeholder="Условие, например: power_load <= 1" onChange={(e) => patch(index, { expr: e.target.value })} />
          <Input value={rule.message ?? ""} disabled={disabled}
            placeholder="Текст замечания (необязательно)" onChange={(e) => patch(index, { message: e.target.value })} />
        </div>
      ))}
      <Button size="sm" variant="outline" disabled={disabled}
        onClick={() => onChange([...rules, {
          id: `custom_${rules.length + 1}`, label: "Новое правило", expr: "",
          severity: "warning", enabled: true, message: "",
        }])}>
        <Plus className="size-3.5" /> Добавить правило
      </Button>
    </div>
  )
}

export function CheckerSettings({ config, onChange, canEdit, formulaContext, onSave, onReset, saving, dirty }: {
  config: CheckerConfig
  onChange: (c: CheckerConfig) => void
  canEdit: boolean
  formulaContext: string[]
  onSave: () => void
  onReset: () => void
  saving: boolean
  dirty: boolean
}) {
  const [jsonMode, setJsonMode] = useState(false)
  const [jsonDraft, setJsonDraft] = useState("")
  const [jsonError, setJsonError] = useState<string | null>(null)
  const disabled = !canEdit

  const set = <K extends keyof CheckerConfig>(key: K, value: CheckerConfig[K]) =>
    onChange({ ...config, [key]: value })

  const openJson = () => {
    setJsonDraft(JSON.stringify(config, null, 2))
    setJsonError(null)
    setJsonMode(true)
  }

  const applyJson = () => {
    try {
      const parsed = JSON.parse(jsonDraft)
      onChange(parsed)
      setJsonError(null)
      setJsonMode(false)
    } catch (e) {
      setJsonError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="space-y-4">
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md border bg-background/95 p-3 backdrop-blur">
        {!canEdit && <Badge variant="outline">Только просмотр — нет права на изменение</Badge>}
        {dirty && canEdit && <Badge variant="secondary">Есть несохранённые изменения</Badge>}
        <div className="flex-1" />
        <Button size="sm" variant="outline" onClick={jsonMode ? () => setJsonMode(false) : openJson}>
          {jsonMode ? "Обычный вид" : "Расширенно (JSON)"}
        </Button>
        <Button size="sm" variant="outline" disabled={disabled || saving} onClick={onReset}>
          <RotateCcw className="size-3.5" /> Сбросить к ГОСТу
        </Button>
        <Button size="sm" disabled={disabled || saving || !dirty} onClick={onSave}>
          <Save className="size-3.5" /> Сохранить
        </Button>
      </div>

      {jsonMode ? (
        <Card>
          <CardHeader><CardTitle className="text-sm">Конфигурация целиком</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <Textarea className="h-[60vh] font-mono text-xs" value={jsonDraft} disabled={disabled}
              onChange={(e) => setJsonDraft(e.target.value)} />
            {jsonError && <div className="text-sm text-destructive">Некорректный JSON: {jsonError}</div>}
            <Button size="sm" disabled={disabled} onClick={applyJson}>Применить в редактор</Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader><CardTitle className="text-sm">Проверки</CardTitle></CardHeader>
            <CardContent>
              <RulesSection rules={config.rules} onChange={(r) => set("rules", r)} disabled={disabled} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Формулы и характеристики</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
                Формулы считаются по порядку и могут ссылаться на посчитанные ранее.
                Исходные величины: <span className="font-mono">{formulaContext.join(", ")}</span>
              </div>
              {Object.entries(config.formulas).map(([key, spec]) => (
                <div key={key} className="grid gap-2 rounded-md border p-3 sm:grid-cols-[220px_1fr]">
                  <div>
                    <Input className="h-8 font-medium" value={spec.label} disabled={disabled}
                      onChange={(e) => set("formulas", { ...config.formulas, [key]: { ...spec, label: e.target.value } })} />
                    <code className="mt-1 inline-block rounded bg-muted px-1 text-[10px] text-muted-foreground">{key}</code>
                  </div>
                  <div>
                    <Input className="font-mono text-xs" value={spec.expr} disabled={disabled}
                      onChange={(e) => set("formulas", { ...config.formulas, [key]: { ...spec, expr: e.target.value } })} />
                    {spec.help && <div className="mt-1 text-xs text-muted-foreground">{spec.help}</div>}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Пороги оборудования</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              {Object.entries(config.equipment).map(([block, fields]) => (
                <div key={block} className="rounded-md border p-3">
                  <div className="mb-2 font-medium">{EQUIPMENT_LABELS[block] ?? block}</div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {Object.entries(fields as Record<string, unknown>)
                      .filter(([k]) => k !== "label")
                      .map(([field, value]) => (
                        <ValueEditor
                          key={field} name={field} value={value} disabled={disabled}
                          onChange={(v) => set("equipment", {
                            ...config.equipment,
                            [block]: { ...(fields as Record<string, unknown>), [field]: v },
                          })}
                        />
                      ))}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Автозаполнение: разрешённые теги контейнеров</CardTitle></CardHeader>
            <CardContent>
              <StringListEditor
                value={config.autofill?.exempt_tags ?? []} disabled={disabled}
                onChange={(v) => set("autofill", { ...config.autofill, exempt_tags: v })}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Запрещённые предметы</CardTitle></CardHeader>
            <CardContent>
              <ForbiddenSection items={config.forbidden_items} disabled={disabled}
                onChange={(v) => set("forbidden_items", v)} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Пользовательские правила</CardTitle></CardHeader>
            <CardContent>
              <CustomRulesSection rules={config.custom_rules ?? []} disabled={disabled}
                context={formulaContext} onChange={(v) => set("custom_rules", v)} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Пункты ручной проверки</CardTitle></CardHeader>
            <CardContent className="space-y-1.5">
              {(config.manual_checks ?? []).map((check, index) => (
                <div key={index} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
                  <Checkbox checked={check.enabled} disabled={disabled}
                    onCheckedChange={(c) => set("manual_checks", config.manual_checks.map((m, i) =>
                      i === index ? { ...m, enabled: !!c } : m))} />
                  <Input className="w-24 font-mono text-xs" value={check.gost ?? ""} disabled={disabled}
                    placeholder="п. ГОСТ" onChange={(e) => set("manual_checks", config.manual_checks.map((m, i) =>
                      i === index ? { ...m, gost: e.target.value } : m))} />
                  <Input className="min-w-[240px] flex-1" value={check.label} disabled={disabled}
                    onChange={(e) => set("manual_checks", config.manual_checks.map((m, i) =>
                      i === index ? { ...m, label: e.target.value } : m))} />
                  <Button size="icon" variant="ghost" disabled={disabled}
                    onClick={() => set("manual_checks", config.manual_checks.filter((_, i) => i !== index))}>
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
              <Button size="sm" variant="outline" disabled={disabled}
                onClick={() => set("manual_checks", [...(config.manual_checks ?? []), {
                  id: `manual_${(config.manual_checks?.length ?? 0) + 1}`, label: "", enabled: true, gost: "",
                }])}>
                <Plus className="size-3.5" /> Добавить пункт
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Прочие настройки</CardTitle></CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              {Object.entries(config.options ?? {}).map(([key, value]) => (
                <ValueEditor key={key} name={key} value={value} disabled={disabled}
                  onChange={(v) => set("options", { ...config.options, [key]: v as boolean })} />
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
