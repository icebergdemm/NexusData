/** Мульти-выбор игроков для фильтра логов — чипы + автодополнение по уже
 *  встречавшимся в логах именам (`/api/v1/admin/logs/players`).
 *
 *  Раньше был один текстовый инпут на ОДНО имя-подстроку — нельзя было
 *  посмотреть "что делали Вася И Петя" одним запросом. Бэкенд уже умел
 *  принимать список имён (`player_names`, см. routes/logs.py), просто
 *  фронтенд им не пользовался. Свободный ввод (Enter) тоже работает — не
 *  обязательно выбирать из подсказки, это остаётся LIKE-подстрокой.
 */
import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { api } from "@/lib/api"

export function PlayerNamesFilter({ names, onChange, server }: {
  names: string[]
  onChange: (next: string[]) => void
  server: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  const { data } = useQuery({
    queryKey: ["logs-players", server],
    queryFn: () => api.get<{ players: string[] }>(`/api/v1/admin/logs/players?${server !== "all" ? `server=${server}` : ""}`),
    staleTime: 60000,
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
          className="flex h-9 min-w-44 max-w-72 flex-wrap items-center gap-1 rounded-md border bg-background px-2 py-1 text-left text-sm"
          onClick={() => setOpen(true)}
        >
          {names.map((n) => (
            <Badge key={n} variant="secondary" className="gap-1">
              {n}
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
          {!names.length && <span className="text-muted-foreground">Игрок(и)…</span>}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0" align="start">
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
          {!suggestions.length && (
            <p className="px-2 py-4 text-center text-xs text-muted-foreground">
              {query.trim() ? "Ничего похожего — Enter добавит как есть" : "Начните вводить имя"}
            </p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
