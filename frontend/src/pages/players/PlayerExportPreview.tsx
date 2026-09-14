import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import {
  PLAYER_EXPORT_GROUPS,
  type JsonValue,
  type PlayerExportGroup,
  type PlayerPersonalFileExport,
} from "./player-export"

const GROUP_LABELS: Record<PlayerExportGroup, string> = {
  profile: "Профиль",
  activity: "Игровая активность",
  history: "История",
  moderation: "Модерация",
  tickets: "Тикеты и архив переписки",
  rewards: "Награды",
  ai_assessment: "AI-сводка",
}

function isObject(value: JsonValue): value is { [key: string]: JsonValue } {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function scalarLabel(value: JsonValue): string {
  if (value === null) return "null"
  if (typeof value === "string") return JSON.stringify(value)
  return String(value)
}

function valueSummary(value: JsonValue): string {
  if (Array.isArray(value)) return `${value.length} записей`
  if (isObject(value)) return `${Object.keys(value).length} полей`
  return scalarLabel(value)
}

function PreviewValue({
  label,
  value,
  depth = 0,
  hint,
}: {
  label: string
  value: JsonValue
  depth?: number
  hint?: string
}) {
  const indent = { paddingLeft: `${depth * 1.25}rem` }

  if (Array.isArray(value)) {
    return <ArrayPreview label={label} value={value} depth={depth} hint={hint} />
  }
  if (isObject(value)) {
    return (
      <div>
        <div style={indent} className="flex min-w-0 items-center gap-1.5 py-0.5">
          <span className="text-info">{`"${label}":`}</span>
          {hint && <span className="truncate text-muted-foreground">// {hint}</span>}
        </div>
        <div>
          {Object.entries(value).map(([key, child]) => (
            <PreviewValue key={key} label={key} value={child} depth={depth + 1} />
          ))}
        </div>
      </div>
    )
  }
  return (
    <div style={indent} className="grid grid-cols-[minmax(8rem,0.42fr)_minmax(0,1fr)] gap-2 py-0.5">
      <span className="text-info">{`"${label}":`}</span>
      <span className="break-words text-foreground">{scalarLabel(value)}</span>
    </div>
  )
}

function ArrayPreview({ label, value, depth, hint }: { label: string; value: JsonValue[]; depth: number; hint?: string }) {
  const [expanded, setExpanded] = useState(value.length <= 3)
  const indent = { paddingLeft: `${depth * 1.25}rem` }
  const summary = hint ?? `${value.length} записей`

  return (
    <div>
      <button
        type="button"
        style={indent}
        className="flex w-full min-w-0 items-center gap-1.5 py-0.5 text-left hover:text-primary"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        {expanded ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}
        <span className="text-info">{`"${label}":`}</span>
        <span className="text-muted-foreground">[ {summary} ]</span>
      </button>
      {expanded && (
        <div>
          {value.length === 0 ? (
            <div style={{ paddingLeft: `${(depth + 1) * 1.25}rem` }} className="py-0.5 text-muted-foreground">[ пусто ]</div>
          ) : value.map((item, index) => (
            <PreviewValue key={`${label}-${index}`} label={`#${index + 1}`} value={item} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

export function PlayerExportPreview({ data }: { data: PlayerPersonalFileExport }) {
  return (
    <div className="rounded-md border bg-muted/10 px-3 py-2 font-mono text-[11px] leading-5">
      <div className="text-muted-foreground">&#123;</div>
      <PreviewValue label="dossier_type" value={data.dossier_type} depth={1} />
      <PreviewValue label="schema_version" value={data.schema_version} depth={1} />
      <PreviewValue label="exported_at" value={data.exported_at} depth={1} />
      <PreviewValue label="subject" value={data.subject} depth={1} hint="Объект экспорта" />
      {PLAYER_EXPORT_GROUPS.map((group) => {
        const value = data[group]
        if (value === undefined) return null
        return (
          <PreviewValue
            key={group}
            label={group}
            value={value}
            depth={1}
            hint={`${GROUP_LABELS[group]} · ${valueSummary(value)}`}
          />
        )
      })}
      <div className="text-muted-foreground">&#125;</div>
    </div>
  )
}
