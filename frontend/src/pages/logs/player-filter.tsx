/** Мульти-выбор игроков для фильтра логов — чипы + автодополнение по уже
 *  встречавшимся в логах именам (`/api/v1/admin/logs/players`).
 *
 *  Раньше был один текстовый инпут на ОДНО имя-подстроку — нельзя было
 *  посмотреть "что делали Вася И Петя" одним запросом. В быстром режиме
 *  имена сопоставляются с колонкой актора точно; отдельный related-режим
 *  дополнительно ищет упоминания имени в тексте события.
 */
import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { LoaderCircle, X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { IS_V2 } from "@/lib/ui-version"

export function PlayerNamesFilter({ names, onChange, server, scope, className }: {
  names: string[]
  onChange: (next: string[]) => void
  server: string
  scope: "actor" | "related"
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  const { data, isFetching } = useQuery({
    queryKey: ["logs-players", server],
    queryFn: () => api.get<{ players: string[] }>(`/api/v1/admin/logs/players?${server !== "all" ? `server=${server}` : ""}`),
    staleTime: 60000,
    enabled: open,
  })

  const suggestions = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const pool = (data?.players ?? []).filter((n) => !names.includes(n))
    if (!needle) return []
    return pool.filter((n) => n.toLowerCase().includes(needle)).slice(0, 30)
  }, [data, names, query])

  const add = (name: string) => {
    const clean = name.trim()
    if (clean && !names.includes(clean)) onChange([...names, clean])
    setQuery("")
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex min-h-9 min-w-44 max-w-72 flex-wrap items-center gap-1 rounded-md border border-input bg-background px-2 py-1 text-left text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
            IS_V2 && "min-w-0 max-w-full",
            className,
          )}
          onClick={() => setOpen(true)}
        >
          {names.map((n) => (
            <Badge key={n} variant="secondary" className={cn("gap-1", IS_V2 && "max-w-full min-w-0")}>
              <span className={cn(IS_V2 && "min-w-0 truncate")} title={n}>{n}</span>
              <span
                role="button"
                tabIndex={-1}
                className="text-muted-foreground hover:text-destructive"
                onClick={(e) => { e.stopPropagation(); onChange(names.filter((x) => x !== n)) }}
              >
                <X className="size-3" />
              </span>
            </Badge>
          ))}
          {!names.length && <span className="text-muted-foreground">Выберите игрока…</span>}
        </button>
      </PopoverTrigger>
      <PopoverContent
        className={cn("w-64 p-0", IS_V2 && "w-[var(--radix-popover-trigger-width)] min-w-72")}
        align="start"
      >
        <div className="border-b p-1.5">
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(query) } }}
            placeholder="Имя, затем Enter…"
            className="w-full bg-transparent px-1 py-0.5 text-sm outline-none"
          />
        </div>
        <div className="max-h-56 overflow-y-auto p-1">
          {isFetching && (
            <div className="flex items-center justify-center gap-2 px-2 py-4 text-xs text-muted-foreground">
              <LoaderCircle className="size-3.5 animate-spin" /> Загружаем список игроков…
            </div>
          )}
          {suggestions.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => add(n)}
              className="block w-full truncate rounded px-2 py-1 text-left text-sm hover:bg-accent"
            >
              {n}
            </button>
          ))}
          {!isFetching && !suggestions.length && (
            <p className="px-2 py-4 text-center text-xs text-muted-foreground">
              {query.trim() ? "Ничего похожего — Enter добавит как есть" : "Начните вводить имя"}
            </p>
          )}
        </div>
        <p className="border-t px-3 py-2 text-[11px] leading-4 text-muted-foreground">
          {scope === "actor"
            ? "Точное имя: только события, совершённые игроком."
            : "Имя ищется и в тексте чужих событий; запрос может быть медленнее."}
        </p>
      </PopoverContent>
    </Popover>
  )
}
