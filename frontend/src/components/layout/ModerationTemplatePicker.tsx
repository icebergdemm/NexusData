import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { FileText } from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger,
} from "@/components/ui/popover"

export type ModerationTemplateType = "message" | "warning" | "kick" | "ban"

export interface ModerationTemplate {
  id: number
  action_type: ModerationTemplateType
  action_label: string
  name: string
  content: string
  created_at: string | null
  updated_at: string | null
}

interface ModerationTemplatePickerProps {
  type: ModerationTemplateType
  onSelect: (content: string) => void
}

export function ModerationTemplatePicker({ type, onSelect }: ModerationTemplatePickerProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const { data, isLoading, isError } = useQuery({
    queryKey: ["moderation-templates"],
    queryFn: () => api.get<{ templates: ModerationTemplate[] }>("/api/v1/admin/moderation-templates"),
    staleTime: 60_000,
  })

  const templates = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase()
    return (data?.templates ?? []).filter((template) => (
      template.action_type === type
      && (!needle || `${template.name} ${template.content}`.toLocaleLowerCase().includes(needle))
    ))
  }, [data?.templates, search, type])

  if (isLoading) {
    return <Button type="button" size="sm" variant="outline" className="gap-1.5" disabled><FileText className="size-3.5" /> Шаблоны…</Button>
  }

  if (isError) {
    return <Button type="button" size="sm" variant="outline" className="gap-1.5" disabled title="Не удалось загрузить шаблоны"><FileText className="size-3.5" /> Шаблоны</Button>
  }

  return (
    <Popover open={open} onOpenChange={(next) => { setOpen(next); if (!next) setSearch("") }}>
      <PopoverTrigger asChild>
        <Button type="button" size="sm" variant="outline" className="gap-1.5">
          <FileText className="size-3.5" /> Выбрать шаблон
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(92vw,22rem)]">
        <PopoverHeader>
          <PopoverTitle>Выбрать шаблон</PopoverTitle>
          <PopoverDescription>Текст подставится в поле. Действие всё равно нужно подтвердить отдельно.</PopoverDescription>
        </PopoverHeader>
        <Input
          autoFocus
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Найти шаблон…"
          className="mt-3"
        />
          <div className="mt-2 max-h-64 space-y-1 overflow-y-auto">
          {templates.map((template) => (
            <button
              key={template.id}
              type="button"
              className="block w-full rounded-md border px-3 py-2 text-left transition hover:bg-accent"
              onClick={() => { onSelect(template.content); setOpen(false); setSearch("") }}
            >
              <div className="text-sm font-medium">{template.name}</div>
              <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{template.content}</div>
            </button>
          ))}
          {!templates.length && <div className="py-3 text-center text-xs text-muted-foreground">Для этого действия шаблонов пока нет.</div>}
        </div>
      </PopoverContent>
    </Popover>
  )
}
