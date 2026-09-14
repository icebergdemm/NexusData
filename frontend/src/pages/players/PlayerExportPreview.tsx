import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
  if (value === null) return "—"
  if (typeof value === "boolean") return value ? "Да" : "Нет"
  return String(value)
}

function PreviewValue({ label, value, depth = 0 }: { label: string; value: JsonValue; depth?: number }) {
  if (Array.isArray(value)) {
    return <ArrayPreview label={label} value={value} depth={depth} />
  }
  if (isObject(value)) {
    return (
      <div className={depth ? "space-y-2 border-l border-border pl-3" : "space-y-2"}>
        {Object.entries(value).map(([key, child]) => (
          <PreviewValue key={key} label={key} value={child} depth={depth + 1} />
        ))}
      </div>
    )
  }
  return (
    <div className="grid grid-cols-[minmax(8rem,0.45fr)_minmax(0,1fr)] gap-3 border-b border-border/60 py-1.5 text-xs last:border-0">
      <dt className="break-words text-muted-foreground">{label}</dt>
      <dd className="break-words font-mono">{scalarLabel(value)}</dd>
    </div>
  )
}

function ArrayPreview({ label, value, depth }: { label: string; value: JsonValue[]; depth: number }) {
  const [expanded, setExpanded] = useState(value.length <= 3)
  const lazy = value.length > 3

  return (
    <div className={depth ? "border-l border-border pl-3" : ""}>
      {lazy ? (
        <button
          type="button"
          className="flex w-full items-center gap-1 py-1 text-left text-xs font-medium hover:text-primary"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          <span>{label}</span>
          <Badge variant="secondary" className="ml-1">{value.length}</Badge>
        </button>
      ) : (
        <div className="py-1 text-xs font-medium">{label} <Badge variant="secondary">{value.length}</Badge></div>
      )}
      {expanded && (
        <div className="space-y-1 pl-3">
          {value.map((item, index) => (
            <PreviewValue key={`${label}-${index}`} label={`#${index + 1}`} value={item} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

function groupSummary(value: JsonValue): string {
  if (Array.isArray(value)) return `${value.length} записей`
  if (isObject(value)) return `${Object.keys(value).length} полей`
  return scalarLabel(value)
}

export function PlayerExportPreview({ data }: { data: PlayerPersonalFileExport }) {
  return (
    <div className="space-y-3">
      <Card className="border-primary/20 bg-primary/5">
        <CardHeader className="gap-1 py-4">
          <CardTitle className="text-sm">Личное дело игрока</CardTitle>
          <p className="text-xs text-muted-foreground">
            Схема {data.schema_version} · экспортировано {data.exported_at}
          </p>
        </CardHeader>
        <CardContent className="py-0 pb-4">
          <PreviewValue label="subject" value={data.subject} />
        </CardContent>
      </Card>

      {PLAYER_EXPORT_GROUPS.map((group) => {
        const value = data[group]
        if (value === undefined) return null
        return (
          <Card key={group}>
            <CardHeader className="gap-1 py-4">
              <div className="flex items-center justify-between gap-3">
                <CardTitle className="text-sm">{GROUP_LABELS[group]}</CardTitle>
                <Badge variant="outline">{groupSummary(value)}</Badge>
              </div>
              <p className="font-mono text-[10px] text-muted-foreground">{group}</p>
            </CardHeader>
            <CardContent className="py-0 pb-4">
              <PreviewValue label={group} value={value} />
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
