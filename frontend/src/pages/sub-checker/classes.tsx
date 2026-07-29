import { useRef, useState } from "react"
import { toast } from "sonner"
import { ChevronDown, ChevronRight, Plus, RotateCcw, Save, Search, Trash2, Upload } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import type { ClassGroupSpec, ClassItemSpec, ShipClassSpec } from "./types"

const REQUIREMENT_LABELS: Record<string, string> = {
  production: "Требования к производству",
  weapons: "Вооружение",
  medical: "Медицинский отсек",
  electrics: "Требования к электрике",
}

function ItemsEditor({ items, onChange, disabled, suggestions }: {
  items: ClassItemSpec[]
  onChange: (items: ClassItemSpec[]) => void
  disabled: boolean
  suggestions: string[]
}) {
  const [search, setSearch] = useState("")
  const term = search.trim().toLowerCase()
  const visible = items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => !term
      || item.identifier.toLowerCase().includes(term)
      || (item.label ?? "").toLowerCase().includes(term))

  const patch = (index: number, changes: Partial<ClassItemSpec>) =>
    onChange(items.map((it, i) => (i === index ? { ...it, ...changes } : it)))

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative max-w-xs flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="h-8 pl-8" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск позиции" />
        </div>
        <Badge variant="secondary">{items.filter((i) => i.enabled).length} из {items.length} активны</Badge>
        <div className="flex-1" />
        <Button size="sm" variant="outline" disabled={disabled}
          onClick={() => onChange([...items, { identifier: "", label: "", min: 1, max: null, enabled: true, note: "" }])}>
          <Plus className="size-3.5" /> Позиция
        </Button>
      </div>

      <div className="space-y-1">
        {visible.map(({ item, index }) => (
          <div key={index} className="flex flex-wrap items-center gap-1.5 rounded-md border p-1.5">
            <Checkbox checked={item.enabled} disabled={disabled}
              onCheckedChange={(c) => patch(index, { enabled: !!c })} />
            <Input className="h-8 w-52 font-mono text-xs" list="sub-checker-identifiers"
              value={item.identifier} disabled={disabled} placeholder="identifier"
              onChange={(e) => patch(index, { identifier: e.target.value })} />
            <Input className="h-8 min-w-[160px] flex-1" value={item.label} disabled={disabled}
              placeholder="Название" onChange={(e) => patch(index, { label: e.target.value })} />
            <div className="flex items-center gap-1">
              <Label className="text-[11px] text-muted-foreground">мин</Label>
              <Input className="h-8 w-16" type="number" value={item.min ?? ""} disabled={disabled}
                onChange={(e) => patch(index, { min: e.target.value === "" ? null : Number(e.target.value) })} />
              <Label className="text-[11px] text-muted-foreground">макс</Label>
              <Input className="h-8 w-16" type="number" value={item.max ?? ""} disabled={disabled}
                onChange={(e) => patch(index, { max: e.target.value === "" ? null : Number(e.target.value) })} />
            </div>
            {item.note && <span className="text-[11px] text-warning" title={item.note}>⚠</span>}
            <Button size="icon" variant="ghost" className="size-8" disabled={disabled}
              onClick={() => onChange(items.filter((_, i) => i !== index))}>
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
        {!visible.length && <div className="py-4 text-center text-sm text-muted-foreground">Позиций нет</div>}
      </div>

      <datalist id="sub-checker-identifiers">
        {suggestions.map((s) => <option key={s} value={s} />)}
      </datalist>
    </div>
  )
}

function GroupBlock({ group, onChange, onRemove, disabled, suggestions }: {
  group: ClassGroupSpec
  onChange: (g: ClassGroupSpec) => void
  onRemove: () => void
  disabled: boolean
  suggestions: string[]
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="overflow-hidden rounded-md border">
      <div className="flex items-center gap-2 bg-muted/40 px-3 py-2">
        <button type="button" onClick={() => setOpen((o) => !o)} className="flex flex-1 items-center gap-2 text-left text-sm font-medium">
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          {group.label}
          <Badge variant="outline" className="ml-1">{group.items.length}</Badge>
        </button>
        <Checkbox checked={group.enabled} disabled={disabled}
          onCheckedChange={(c) => onChange({ ...group, enabled: !!c })} />
        <span className="text-xs text-muted-foreground">проверять</span>
        <Button size="icon" variant="ghost" className="size-8" disabled={disabled} onClick={onRemove}>
          <Trash2 className="size-4" />
        </Button>
      </div>
      {open && (
        <div className="space-y-2 p-3">
          <div className="flex gap-2">
            <Input className="h-8 w-40 font-mono text-xs" value={group.key} disabled={disabled}
              placeholder="key" onChange={(e) => onChange({ ...group, key: e.target.value })} />
            <Input className="h-8 flex-1" value={group.label} disabled={disabled}
              placeholder="Название группы" onChange={(e) => onChange({ ...group, label: e.target.value })} />
          </div>
          <ItemsEditor items={group.items} disabled={disabled} suggestions={suggestions}
            onChange={(items) => onChange({ ...group, items })} />
        </div>
      )}
    </div>
  )
}

function ClassBlock({ spec, onChange, onRemove, disabled, suggestions }: {
  spec: ShipClassSpec
  onChange: (s: ShipClassSpec) => void
  onRemove: () => void
  disabled: boolean
  suggestions: string[]
}) {
  const [open, setOpen] = useState(false)
  return (
    <Card className="py-0">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2 space-y-0 py-3">
        <button type="button" onClick={() => setOpen((o) => !o)} className="flex items-center gap-2 text-left">
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          <CardTitle className="text-sm">{spec.name || spec.key}</CardTitle>
        </button>
        <Badge variant="secondary">
          {spec.min_tonnage ?? 0} – {spec.max_tonnage ?? "∞"} т
        </Badge>
        <div className="flex-1" />
        <Checkbox checked={spec.enabled} disabled={disabled}
          onCheckedChange={(c) => onChange({ ...spec, enabled: !!c })} />
        <span className="text-xs text-muted-foreground">активен</span>
        <Button size="icon" variant="ghost" className="size-8" disabled={disabled} onClick={onRemove}>
          <Trash2 className="size-4" />
        </Button>
      </CardHeader>
      {open && (
        <CardContent className="space-y-3 pb-4">
          <div className="grid gap-2 sm:grid-cols-4">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Ключ</Label>
              <Input className="font-mono text-xs" value={spec.key} disabled={disabled}
                onChange={(e) => onChange({ ...spec, key: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Название</Label>
              <Input value={spec.name} disabled={disabled} onChange={(e) => onChange({ ...spec, name: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Тоннаж от</Label>
              <Input type="number" value={spec.min_tonnage ?? ""} disabled={disabled}
                onChange={(e) => onChange({ ...spec, min_tonnage: e.target.value === "" ? null : Number(e.target.value) })} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Тоннаж до (пусто = без предела)</Label>
              <Input type="number" value={spec.max_tonnage ?? ""} disabled={disabled}
                onChange={(e) => onChange({ ...spec, max_tonnage: e.target.value === "" ? null : Number(e.target.value) })} />
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            {Object.entries(spec.requirements ?? {}).map(([key, value]) => (
              <div key={key} className="space-y-1">
                <Label className="text-xs text-muted-foreground">{REQUIREMENT_LABELS[key] ?? key}</Label>
                <Textarea className="min-h-[68px] text-sm" value={value} disabled={disabled}
                  onChange={(e) => onChange({ ...spec, requirements: { ...spec.requirements, [key]: e.target.value } })} />
              </div>
            ))}
          </div>

          <div className="space-y-2">
            {spec.item_groups.map((group, i) => (
              <GroupBlock
                key={i} group={group} disabled={disabled} suggestions={suggestions}
                onChange={(g) => onChange({ ...spec, item_groups: spec.item_groups.map((x, j) => (j === i ? g : x)) })}
                onRemove={() => onChange({ ...spec, item_groups: spec.item_groups.filter((_, j) => j !== i) })}
              />
            ))}
            <Button size="sm" variant="outline" disabled={disabled}
              onClick={() => onChange({
                ...spec,
                item_groups: [...spec.item_groups, { key: `group_${spec.item_groups.length + 1}`, label: "Новая группа", enabled: true, items: [] }],
              })}>
              <Plus className="size-3.5" /> Группа наполнения
            </Button>
          </div>
        </CardContent>
      )}
    </Card>
  )
}

export function CheckerClasses({ classes, onChange, canEdit, onSave, onReset, saving, dirty }: {
  classes: ShipClassSpec[]
  onChange: (c: ShipClassSpec[]) => void
  canEdit: boolean
  onSave: () => void
  onReset: () => void
  saving: boolean
  dirty: boolean
}) {
  const [suggestions, setSuggestions] = useState<string[]>([])
  const fileInput = useRef<HTMLInputElement>(null)
  const disabled = !canEdit

  const loadIdentifiers = async (file: File) => {
    const form = new FormData()
    form.set("sub_file", file)
    try {
      const res = await api.postForm<{ identifiers: { identifier: string; count: number }[] }>(
        "/api/v1/admin/sub-checker/identifiers", form)
      setSuggestions(res.identifiers.map((i) => i.identifier))
      toast.success(`Подсказки загружены: ${res.identifiers.length} идентификаторов`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Не удалось разобрать файл")
    }
  }

  return (
    <div className="space-y-4">
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-md border bg-background/95 p-3 backdrop-blur">
        {!canEdit && <Badge variant="outline">Только просмотр — нет права на изменение классов</Badge>}
        {dirty && canEdit && <Badge variant="secondary">Есть несохранённые изменения</Badge>}
        <div className="flex-1" />
        <input
          ref={fileInput} type="file" accept=".sub" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) loadIdentifiers(f); e.target.value = "" }}
        />
        <Button size="sm" variant="outline" disabled={disabled} onClick={() => fileInput.current?.click()}>
          <Upload className="size-3.5" /> Подсказки из .sub
        </Button>
        <Button size="sm" variant="outline" disabled={disabled || saving} onClick={onReset}>
          <RotateCcw className="size-3.5" /> Сбросить к ГОСТу
        </Button>
        <Button size="sm" disabled={disabled || saving || !dirty} onClick={onSave}>
          <Save className="size-3.5" /> Сохранить
        </Button>
      </div>

      <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
        Класс определяется по тоннажу: границы проверяются как «от включительно, до исключительно».
        Наполнение считается по всему кораблю целиком — в .sub-файле предметы лежат единым списком,
        и понять, что конкретный шкаф относится к арсеналу, а не к складу, из файла нельзя.
        Кнопка «Подсказки из .sub» подставляет идентификаторы из реального корабля в автодополнение полей.
      </div>

      {classes.map((spec, i) => (
        <ClassBlock
          key={i} spec={spec} disabled={disabled} suggestions={suggestions}
          onChange={(s) => onChange(classes.map((x, j) => (j === i ? s : x)))}
          onRemove={() => onChange(classes.filter((_, j) => j !== i))}
        />
      ))}

      <Button variant="outline" disabled={disabled}
        onClick={() => onChange([...classes, {
          key: `class_${classes.length + 1}`, name: "Новый класс",
          min_tonnage: 0, max_tonnage: null, enabled: true,
          requirements: { production: "", weapons: "", medical: "", electrics: "" },
          item_groups: [],
        }])}>
        <Plus className="size-4" /> Добавить класс
      </Button>
    </div>
  )
}
