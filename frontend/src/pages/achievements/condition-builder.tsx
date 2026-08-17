/** Конструктор дерева условий триггера: И/ИЛИ/НЕ + листья «поле · оператор ·
 *  значение», рекурсивно на любую глубину — ровно та же структура, которую
 *  валидирует _validate_condition_node в services_achievements.py.
 *
 *  Значение подставляется виджетом по смыслу поля (см. FIELD_KINDS там же):
 *  предмет выбирается из каталога с иконками, профессия/талант/недуг — из
 *  выгрузки игрового контента, число — числовым полем. Свободный ввод
 *  остаётся там, где справочника нет (виды монстров, идентификаторы заданий,
 *  контент модов).
 */
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ChipsField, ItemField, SearchPicker, type Option } from "./pickers"
import {
  OP_LABELS, NUMERIC_OPS, blankLeaf, defaultValue, groupKey, isLeaf, isNot, kindOf,
  type AchievementsReference, type ConditionNode, type ConditionValue, type EventDef, type FieldKind,
} from "./types"

/** Справочник → опции для поля данного вида. Пустой массив = справочника
 *  нет, показываем свободный ввод. */
function optionsForKind(kind: FieldKind, reference: AchievementsReference): Option[] {
  switch (kind) {
    case "job":
      return reference.jobs.map((j) => ({ value: j.identifier, label: j.name, hint: j.identifier }))
    case "talent":
      return Object.entries(reference.talents ?? {}).flatMap(([job, list]) =>
        list.map((t) => ({ value: t.identifier, label: `${t.name} · ${job}`, hint: t.identifier })),
      )
    case "affliction":
      return reference.afflictions.map((a) => ({ value: a.identifier, label: a.name, hint: a.identifier }))
    case "skill":
      return reference.skills.map((s) => ({ value: s, label: reference.skill_labels?.[s] ?? s, hint: s }))
    default:
      return []
  }
}

/** Значение одного листа. Массив — только для op=in/not_in (см. валидатор). */
function ValueEditor({ field, op, value, onChange, eventDef, reference }: {
  field: string
  op: string
  value: ConditionValue
  onChange: (v: ConditionValue) => void
  eventDef?: EventDef
  reference: AchievementsReference
}) {
  const fieldDef = eventDef?.fields?.[field]
  const enumValues = fieldDef?.values
  const kind = kindOf(field, reference)
  const isBool = Array.isArray(enumValues) && enumValues.every((v) => typeof v === "boolean")

  // Списковые операторы: набор значений чипами (с подсказками из справочника,
  // если он для этого поля есть).
  if (op === "in" || op === "not_in") {
    const list = (Array.isArray(value) ? value : value === "" || value == null ? [] : [value]) as (string | number)[]
    const options: Option[] = enumValues && !isBool
      ? enumValues.map((v) => ({ value: String(v), label: String(v) }))
      : optionsForKind(kind, reference)
    return (
      <ChipsField
        values={list.map(String)}
        onChange={(next) => onChange(kind === "number" ? next.map((v) => Number(v) || 0) : next)}
        options={options.length ? options : undefined}
        placeholder={kind === "number" ? "Число + Enter" : "Значение + Enter"}
        mono={!options.length}
      />
    )
  }

  if (isBool) {
    return (
      <Select value={value === true ? "true" : "false"} onValueChange={(v) => onChange(v === "true")}>
        <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="true">да</SelectItem>
          <SelectItem value="false">нет</SelectItem>
        </SelectContent>
      </Select>
    )
  }

  if (enumValues?.length) {
    return (
      <Select value={String(value ?? "")} onValueChange={onChange}>
        <SelectTrigger className="h-9"><SelectValue placeholder="Выберите" /></SelectTrigger>
        <SelectContent>
          {enumValues.map((v) => <SelectItem key={String(v)} value={String(v)}>{String(v)}</SelectItem>)}
        </SelectContent>
      </Select>
    )
  }

  if (kind === "item") {
    return <ItemField value={String(value ?? "")} onChange={onChange} className="w-full" />
  }

  if (kind === "number") {
    return (
      <Input
        type="number"
        value={typeof value === "number" ? value : Number(value) || 0}
        onChange={(e) => onChange(Number(e.target.value) || 0)}
        className="h-9"
      />
    )
  }

  const options = optionsForKind(kind, reference)
  if (options.length) {
    return <SearchPicker options={options} value={String(value ?? "")} onPick={onChange} className="w-full" />
  }

  return (
    <Input
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Значение"
      className="h-9 font-mono text-xs"
    />
  )
}

function LeafEditor({ node, onChange, eventDef, reference }: {
  node: { field: string; op: string; value: ConditionValue }
  onChange: (n: ConditionNode) => void
  eventDef?: EventDef
  reference: AchievementsReference
}) {
  const fields = Object.entries(eventDef?.fields ?? {})
  // Поле не из каталога события (пришло из JSON или мода) — не теряем его.
  const known = fields.some(([f]) => f === node.field)
  const fieldOptions: Option[] = [
    ...fields.map(([f, def]) => ({ value: f, label: def.label, hint: f })),
    ...(node.field && !known ? [{ value: node.field, label: `${node.field} (своё поле)`, hint: node.field }] : []),
  ]

  // Поле не числовое (и не перечисление) — сравнения по величине для него бессмысленны.
  const disableNumericOps = kindOf(node.field, reference) !== "number" && !eventDef?.fields?.[node.field]?.values
  const ops = reference.condition_ops

  return (
    <div className="grid flex-1 gap-1.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,150px)_minmax(0,1fr)]">
      <SearchPicker
        options={fieldOptions}
        value={node.field}
        placeholder="Поле события"
        className="w-full"
        onPick={(f) => onChange({ field: f, op: node.op, value: defaultValue(f, node.op, eventDef, reference) })}
      />
      <Select
        value={node.op}
        onValueChange={(op) => onChange({
          ...node, op,
          // in/not_in меняют тип значения на список и обратно.
          value: (op === "in" || op === "not_in") !== (node.op === "in" || node.op === "not_in")
            ? defaultValue(node.field, op, eventDef, reference)
            : node.value,
        })}
      >
        <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
        <SelectContent>
          {ops.map((op) => (
            <SelectItem key={op} value={op} disabled={disableNumericOps && NUMERIC_OPS.includes(op)}>
              {OP_LABELS[op] ?? op}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <ValueEditor
        field={node.field}
        op={node.op}
        value={node.value}
        onChange={(v) => onChange({ ...node, value: v })}
        eventDef={eventDef}
        reference={reference}
      />
    </div>
  )
}

export function ConditionEditor({ node, onChange, onRemove, eventDef, reference, depth = 0 }: {
  node: ConditionNode
  onChange: (n: ConditionNode) => void
  onRemove?: () => void
  eventDef?: EventDef
  reference: AchievementsReference
  depth?: number
}) {
  const removeBtn = onRemove && (
    <Button type="button" variant="ghost" size="icon-sm" title="Убрать" onClick={onRemove}>
      <Trash2 className="size-4 text-destructive" />
    </Button>
  )

  if (isLeaf(node)) {
    return (
      <div className="flex items-start gap-1 rounded-md border bg-background p-1.5">
        <LeafEditor node={node} onChange={onChange} eventDef={eventDef} reference={reference} />
        {removeBtn}
      </div>
    )
  }

  if (isNot(node)) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-1.5">
        <div className="mb-1 flex items-center justify-between">
          <span className="px-1 text-xs font-semibold text-destructive">НЕ (условие не должно выполняться)</span>
          <div className="flex items-center gap-1">
            <Button
              type="button" variant="ghost" size="sm" className="h-7 text-xs"
              title="Убрать отрицание, оставив вложенное условие"
              onClick={() => onChange(node.not)}
            >
              снять НЕ
            </Button>
            {removeBtn}
          </div>
        </div>
        <ConditionEditor
          node={node.not}
          onChange={(n) => onChange({ not: n })}
          eventDef={eventDef}
          reference={reference}
          depth={depth + 1}
        />
      </div>
    )
  }

  const key = groupKey(node)
  const children = (node as Record<"all" | "any", ConditionNode[]>)[key]
  const setChildren = (next: ConditionNode[]) => onChange({ [key]: next } as ConditionNode)

  return (
    <div className={`rounded-md border p-1.5 ${key === "all" ? "bg-primary/5" : "bg-warning/5"}`}>
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-1">
        <Select
          value={key}
          onValueChange={(v) => onChange({ [v]: children } as ConditionNode)}
        >
          <SelectTrigger className="h-7 w-48 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">И — все условия внутри</SelectItem>
            <SelectItem value="any">ИЛИ — хотя бы одно</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex items-center gap-1">
          <Button
            type="button" variant="outline" size="sm" className="h-7 text-xs"
            onClick={() => setChildren([...children, blankLeaf(eventDef, reference)])}
          >
            <Plus className="mr-1 size-3" /> Условие
          </Button>
          {depth < 3 && (
            <>
              <Button
                type="button" variant="outline" size="sm" className="h-7 text-xs"
                onClick={() => setChildren([...children, { all: [blankLeaf(eventDef, reference)] }])}
              >
                <Plus className="mr-1 size-3" /> Группа
              </Button>
              <Button
                type="button" variant="outline" size="sm" className="h-7 text-xs"
                onClick={() => setChildren([...children, { not: blankLeaf(eventDef, reference) }])}
              >
                <Plus className="mr-1 size-3" /> НЕ
              </Button>
            </>
          )}
          {removeBtn}
        </div>
      </div>
      <div className="space-y-1.5">
        {children.map((child, i) => (
          <ConditionEditor
            key={i}
            node={child}
            onChange={(n) => setChildren(children.map((c, j) => (j === i ? n : c)))}
            onRemove={() => setChildren(children.filter((_, j) => j !== i))}
            eventDef={eventDef}
            reference={reference}
            depth={depth + 1}
          />
        ))}
        {!children.length && (
          <p className="py-3 text-center text-xs text-muted-foreground">
            Группа пуста — добавьте условие, иначе сохранение отклонит её
          </p>
        )}
      </div>
    </div>
  )
}


