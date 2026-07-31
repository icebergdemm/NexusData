import { useState } from "react"
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Info, ListChecks, Ship, XCircle,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import type { Finding, Severity, SubReport } from "./types"

const SEVERITY_LABEL: Record<Severity, string> = {
  error: "Нарушение",
  warning: "Предупреждение",
  info: "К сведению",
}

const CATEGORY_LABEL: Record<string, string> = {
  general: "Общее",
  geometry: "Геометрия и обшивка",
  power: "Энергосистема",
  engines: "Двигатели",
  pumps: "Насосы",
  navigation: "Навигация",
  life_support: "Жизнеобеспечение",
  equipment: "Оборудование",
  items: "Предметы",
  class: "Класс и наполнение",
  formulas: "Формулы",
  custom: "Пользовательские правила",
}

const METRIC_UNITS: Record<string, string> = {
  length_m: "м", height_m: "м", raw_area: "м²", hull_area: "м²",
  tonnage: "т", max_speed: "км/ч",
}

const METRIC_LABEL: Record<string, string> = {
  length_m: "Длина", height_m: "Высота", raw_area: "Габаритная площадь",
  hull_area: "Площадь отсеков", factor: "Коэффициент заполнения", volume: "Условный объём",
  tonnage: "Тоннаж", max_speed: "Макс. скорость", optimal_ballast: "Оптимальный балласт",
  power_load: "Загрузка энергосистемы",
}

function SeverityIcon({ severity }: { severity: Severity }) {
  if (severity === "error") return <XCircle className="size-4 shrink-0 text-destructive" />
  if (severity === "warning") return <AlertTriangle className="size-4 shrink-0 text-warning" />
  return <Info className="size-4 shrink-0 text-info" />
}

function VerdictBanner({ report }: { report: SubReport }) {
  const errors = report.total_errors ?? report.errors
  const warnings = report.total_warnings ?? report.warnings
  const ok = errors === 0
  return (
    <div
      className={`flex flex-wrap items-center gap-3 rounded-lg border p-4 ${
        ok ? "border-success/40 bg-success/10" : "border-destructive/40 bg-destructive/10"
      }`}
    >
      {ok ? <CheckCircle2 className="size-6 text-success" /> : <XCircle className="size-6 text-destructive" />}
      <div className="min-w-0 flex-1">
        <div className="font-semibold">
          {ok ? "Корабль соответствует ГОСТу" : "Корабль не соответствует ГОСТу"}
        </div>
        <div className="text-sm text-muted-foreground">
          {report.summary ?? `«${report.name}»`}
        </div>
      </div>
      <div className="flex gap-2">
        <Badge variant={errors ? "destructive" : "secondary"}>Нарушений: {errors}</Badge>
        <Badge variant="outline" className={warnings ? "border-warning/50 text-warning" : ""}>
          Предупреждений: {warnings}
        </Badge>
      </div>
    </div>
  )
}

function MetricsGrid({ report }: { report: SubReport }) {
  const entries = Object.entries(report.metrics)
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-md border p-2.5">
          <div className="text-xs text-muted-foreground">{METRIC_LABEL[key] ?? key}</div>
          <div className="font-medium">
            {value == null ? <span className="text-muted-foreground">н/д</span> : (
              <>{value}{METRIC_UNITS[key] ? ` ${METRIC_UNITS[key]}` : ""}</>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function FindingsList({ findings }: { findings: Finding[] }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  if (!findings.length) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-success/40 bg-success/5 p-3 text-sm">
        <CheckCircle2 className="size-4 text-success" /> Замечаний нет.
      </div>
    )
  }

  const byCategory = findings.reduce<Record<string, Finding[]>>((acc, f) => {
    (acc[f.category] ||= []).push(f)
    return acc
  }, {})

  return (
    <div className="space-y-2">
      {Object.entries(byCategory).map(([category, list]) => {
        const isCollapsed = collapsed[category]
        const errors = list.filter((f) => f.severity === "error").length
        return (
          <div key={category} className="overflow-hidden rounded-md border">
            <button
              type="button"
              className="flex w-full items-center gap-2 bg-muted/40 px-3 py-2 text-left text-sm font-medium hover:bg-muted/70"
              onClick={() => setCollapsed((c) => ({ ...c, [category]: !c[category] }))}
            >
              {isCollapsed ? <ChevronRight className="size-4" /> : <ChevronDown className="size-4" />}
              {CATEGORY_LABEL[category] ?? category}
              <Badge variant={errors ? "destructive" : "secondary"} className="ml-auto">{list.length}</Badge>
            </button>
            {!isCollapsed && (
              <div className="divide-y">
                {list.map((f, i) => (
                  <div key={`${f.rule_id}-${f.subject}-${i}`} className="flex items-start gap-2 px-3 py-2 text-sm">
                    <SeverityIcon severity={f.severity} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-medium">{f.title}</span>
                        {f.subject && <code className="rounded bg-muted px-1 text-xs">{f.subject}</code>}
                      </div>
                      {f.detail && <div className="mt-0.5 break-words text-muted-foreground">{f.detail}</div>}
                    </div>
                    <Badge variant="outline" className="shrink-0 text-[10px]">{SEVERITY_LABEL[f.severity]}</Badge>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ClassSection({ report }: { report: SubReport }) {
  const cls = report.class
  if (!cls) {
    return (
      <div className="rounded-md border border-warning/40 bg-warning/5 p-3 text-sm">
        Класс корабля не определён — проверьте тоннаж и настройки классов.
      </div>
    )
  }
  const range = cls.max_tonnage == null
    ? `от ${cls.min_tonnage}` : `${cls.min_tonnage}–${cls.max_tonnage}`

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Ship className="size-4 text-primary" />
        <span className="font-semibold">Класс: {cls.name}</span>
        <Badge variant="secondary">тоннаж {cls.tonnage != null ? Math.round(cls.tonnage) : "н/д"} т ({range})</Badge>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {Object.entries(cls.requirements ?? {}).map(([key, value]) => (
          <div key={key} className="rounded-md border p-2.5 text-sm">
            <div className="text-xs text-muted-foreground">
              {{ production: "Требования к производству", weapons: "Вооружение",
                 medical: "Медицинский отсек", electrics: "Требования к электрике" }[key] ?? key}
            </div>
            <div>{value}</div>
          </div>
        ))}
      </div>

      {cls.groups.map((group) => (
        <div key={group.key} className="overflow-hidden rounded-md border">
          <div className="flex items-center gap-2 bg-muted/40 px-3 py-2 text-sm font-medium">
            {group.ok ? <CheckCircle2 className="size-4 text-success" /> : <XCircle className="size-4 text-destructive" />}
            {group.label}
            <Badge variant="outline" className="ml-auto">
              {group.items.filter((i) => i.ok).length} / {group.items.length}
            </Badge>
          </div>
          {group.items.length > 0 && (
            <div className="grid gap-x-4 gap-y-1 p-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
              {group.items.map((item) => (
                <div key={item.identifier} className="flex items-center gap-1.5">
                  {item.ok
                    ? <CheckCircle2 className="size-3.5 shrink-0 text-success" />
                    : <XCircle className="size-3.5 shrink-0 text-destructive" />}
                  <span className={item.ok ? "" : "text-destructive"}>{item.label}</span>
                  <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground">
                    {item.actual}
                    {item.required != null && ` / ${item.required}`}
                    {item.max != null && ` (макс. ${item.max})`}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function EquipmentSection({ report }: { report: SubReport }) {
  const blocks: { label: string; rows: Record<string, unknown>[] }[] = [
    { label: "Реакторы", rows: report.equipment.reactors },
    { label: "Двигатели", rows: report.equipment.engines },
    { label: "Насосы", rows: report.equipment.pumps },
    { label: "Накопители", rows: report.equipment.batteries },
    { label: "Генераторы кислорода", rows: report.equipment.oxygen_generators },
    { label: "Терминалы навигации", rows: report.equipment.steering },
  ].filter((b) => b.rows.length > 0)

  return (
    <div className="space-y-3">
      {blocks.map((block) => (
        <div key={block.label} className="overflow-x-auto rounded-md border">
          <div className="bg-muted/40 px-3 py-2 text-sm font-medium">{block.label} ({block.rows.length})</div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-t text-left text-xs text-muted-foreground">
                {Object.keys(block.rows[0]).map((k) => <th key={k} className="px-3 py-1.5 font-normal">{k}</th>)}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, i) => (
                <tr key={i} className="border-t">
                  {Object.keys(block.rows[0]).map((k) => (
                    <td key={k} className="px-3 py-1.5 font-mono text-xs">
                      {typeof row[k] === "boolean" ? (row[k] ? "да" : "нет") : String(row[k] ?? "-")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <div className="text-sm text-muted-foreground">
        Распределительных щитков: {report.equipment.power_distributors} ·
        {" "}Предметов на корабле: {report.raw.item_count} ·
        {" "}Версия игры: {report.raw.game_version ?? "н/д"} ·
        {" "}«Снабжение вручную»: {report.raw.manually_outfitted ? "да" : "нет"}
      </div>
    </div>
  )
}

function ManualChecks({ report }: { report: SubReport }) {
  if (!report.manual_checks?.length) return null
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        <ListChecks className="size-4" /> Проверяется инспектором вручную
      </div>
      <div className="grid gap-1.5 sm:grid-cols-2">
        {report.manual_checks.map((c) => (
          <div key={c.id} className="flex items-start gap-2 rounded-md border p-2 text-sm">
            <span className="mt-0.5 shrink-0 rounded bg-muted px-1 font-mono text-[10px] text-muted-foreground">
              {c.gost || "ГОСТ"}
            </span>
            <span>{c.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

type ReportTab = "findings" | "class" | "metrics" | "equipment" | "manual"

const TABS: { key: ReportTab; label: string }[] = [
  { key: "findings", label: "Замечания" },
  { key: "class", label: "Класс и наполнение" },
  { key: "metrics", label: "Характеристики" },
  { key: "equipment", label: "Оборудование" },
  { key: "manual", label: "Ручная проверка" },
]

/** Полный отчёт. Используется и на странице чекера, и во всплывающем окне
 * автопроверки при загрузке новой версии — поэтому свёрстан самодостаточно. */
export function SubCheckReport({ report, compact = false }: { report: SubReport; compact?: boolean }) {
  const [tab, setTab] = useState<ReportTab>("findings")

  return (
    <div className="space-y-4">
      <VerdictBanner report={report} />

      <div className="flex flex-wrap gap-1 border-b">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-3 py-1.5 text-sm transition-colors ${
              tab === t.key ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
            {t.key === "findings" && report.findings.length > 0 && (
              <span className="ml-1.5 text-xs text-muted-foreground">{report.findings.length}</span>
            )}
          </button>
        ))}
      </div>

      <div className={compact ? "max-h-[55vh] overflow-y-auto pr-1" : ""}>
        {tab === "findings" && <FindingsList findings={report.findings} />}
        {tab === "class" && <ClassSection report={report} />}
        {tab === "metrics" && <MetricsGrid report={report} />}
        {tab === "equipment" && <EquipmentSection report={report} />}
        {tab === "manual" && <ManualChecks report={report} />}
      </div>

      {!!report.drones?.length && (
        <div className="space-y-3">
          <div className="text-sm font-semibold">Пристыкованные суда ({report.drones.length})</div>
          {report.drones.map((drone, i) => (
            <Card key={i} className="py-0">
              <CardContent className="space-y-3 p-4">
                <VerdictBanner report={drone} />
                <FindingsList findings={drone.findings} />
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

/** Всплывающее окно с отчётом — автопроверка при загрузке версии и кнопка
 * «Проверить корабль» показывают именно его. */
export function SubCheckDialog({ report, open, onOpenChange, title }: {
  report: SubReport | null
  open: boolean
  onOpenChange: (open: boolean) => void
  title?: string
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{title ?? "Проверка корабля по ГОСТу"}</DialogTitle>
          <DialogDescription>
            {report ? `«${report.name}»${report.filename ? ` · ${report.filename}` : ""}` : "Отчёт недоступен"}
          </DialogDescription>
        </DialogHeader>
        {report
          ? <SubCheckReport report={report} compact />
          : <div className="py-8 text-center text-sm text-muted-foreground">Проверку выполнить не удалось.</div>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Закрыть</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
