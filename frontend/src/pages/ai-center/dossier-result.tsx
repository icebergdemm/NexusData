import { ExternalLink } from "lucide-react"
import { Link } from "react-router-dom"
import { Badge } from "@/components/ui/badge"
import type { AIDossierContext, AIDossierEvidence, AIDossierResult } from "@/pages/players/detail-types"

const triageLabel = { none: "Нет активных сигналов", review: "Нужна проверка", urgent: "Срочная проверка" } as const
const triageClass = {
  none: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  review: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  urgent: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
} as const
const severityLabel = { low: "низкий", medium: "средний", high: "высокий" } as const
const severityClass = {
  low: "secondary",
  medium: "outline",
  high: "destructive",
} as const
const sourceLabel: Record<string, string> = {
  anomaly: "Аномалия Sauron", anticheat: "Античит", log: "Лог", punishment: "Наказание",
  note: "Заметка", label: "Метка", player: "Профиль игрока",
}

function EvidenceLinks({ items }: { items: AIDossierEvidence[] }) {
  if (!items.length) return null
  return <div className="flex flex-wrap gap-2">
    {items.map((item, index) => item.href ? (
      <Link key={`${item.kind}-${item.id}-${index}`} className="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs text-primary underline" to={item.href}>
        <ExternalLink className="size-3" /> {sourceLabel[item.kind] || item.kind} #{item.id}
      </Link>
    ) : <span key={`${item.kind}-${item.id}-${index}`} className="rounded border px-2 py-1 text-xs text-muted-foreground">{sourceLabel[item.kind] || item.kind} #{item.id}</span>)}
  </div>
}

function ContextRow({ item }: { item: AIDossierContext }) {
  const content = <>{sourceLabel[item.kind] || item.kind}: {item.text}{item.source_id != null ? ` (#${item.source_id})` : ""}</>
  return item.href ? <Link className="block text-primary underline" to={item.href}>{content}</Link> : <div>{content}</div>
}

export function DossierResultView({ result }: { result: AIDossierResult }) {
  const level = result.triage?.level || "review"
  return <div className="space-y-4">
    <div className={`rounded-md border p-4 ${triageClass[level]}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{result.triage?.title || triageLabel[level]}</span>
        <Badge variant={level === "urgent" ? "destructive" : level === "review" ? "outline" : "secondary"}>{level}</Badge>
      </div>
      <p className="mt-2 text-sm">{result.summary}</p>
      <p className="mt-2 text-xs opacity-80">{result.triage?.reason} · Полнота источников: {result.confidence}.</p>
    </div>

    <section>
      <h3 className="mb-2 text-sm font-medium">Находки для проверки</h3>
      {result.findings.length ? <div className="space-y-3">{result.findings.map((finding, index) => <article key={`${finding.category}-${index}`} className="rounded-md border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{finding.title}</span>
          <Badge variant={severityClass[finding.severity]}>{severityLabel[finding.severity]}</Badge>
        </div>
        <p className="mt-1 text-sm">{finding.details}</p>
        <p className="mt-2 text-xs text-muted-foreground">Почему: {finding.reason}</p>
        <p className="mt-1 text-xs text-muted-foreground">Следующий шаг: {finding.next_step}</p>
        <div className="mt-2"><EvidenceLinks items={finding.evidence} /></div>
      </article>)}</div> : <p className="text-sm text-muted-foreground">Активных сигналов для проверки не найдено. Игровые stats и сессии намеренно не показаны.</p>}
    </section>

    {result.context.length > 0 && <details className="rounded-md border p-3">
      <summary className="cursor-pointer text-sm font-medium">Контекст модерации ({result.context.length})</summary>
      <div className="mt-2 space-y-2 text-sm text-muted-foreground">{result.context.map((item, index) => <ContextRow key={`${item.kind}-${item.source_id}-${index}`} item={item} />)}</div>
    </details>}

    {result.caveats.length > 0 && <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
      <div className="mb-1 font-medium">Ограничения</div>
      <ul className="list-disc space-y-1 pl-5">{result.caveats.map((item, index) => <li key={index}>{item}</li>)}</ul>
    </div>}
    <p className="text-xs text-muted-foreground">Находки и приоритет рассчитаны серверными правилами; Qwen формулирует только пояснение{result.summary_source === "qwen" ? " (ответ модели принят)" : " (rules-only)"}.</p>
  </div>
}
