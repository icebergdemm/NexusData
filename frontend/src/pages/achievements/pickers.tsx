/** Мелкие виджеты конструктора ачивок: выбор предмета из каталога с
 *  иконками, поиск по длинному справочнику (статы/таланты/недуги) и список
 *  строк «чипами».
 *
 *  Локальные для страницы намеренно — страницы-плагины в этом проекте
 *  самодостаточны; визуально повторяют такие же виджеты конструктора кукол
 *  (respawn-dolls/InventoryTab.tsx).
 */
import { useMemo, useState } from "react"
import { Search, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { ItemIcon } from "@/components/layout/ItemIcon"
import { useItemCatalog, itemDisplayName, type ItemCatalog } from "@/lib/items"

const MAX_RESULTS = 80

export interface Option {
  value: string
  label: string
  hint?: string
}

/** Поповер со списком опций и поиском. Использован везде, где справочник
 *  длиннее десятка позиций и обычный Select превращается в простыню. */
export function SearchPicker({ options, value, onPick, placeholder, className, disabled }: {
  options: Option[]
  value: string
  onPick: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matched = needle
      ? options.filter((o) => o.value.toLowerCase().includes(needle) || o.label.toLowerCase().includes(needle))
      : options
    return matched.slice(0, MAX_RESULTS)
  }, [options, query])

  const current = options.find((o) => o.value === value)

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQuery("") }}>
      <PopoverTrigger asChild disabled={disabled}>
        <Button
          type="button" variant="outline"
          className={`h-9 justify-start font-normal ${className ?? ""}`}
          disabled={disabled}
        >
          <span className={`truncate ${value ? "" : "text-muted-foreground"}`}>
            {current?.label ?? value ?? ""}
            {!value && (placeholder ?? "Выберите")}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start">
        <div className="flex items-center gap-2 border-b px-2 py-1.5">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск"
            className="w-full bg-transparent text-sm outline-none"
          />
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {results.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => { onPick(o.value); setOpen(false); setQuery("") }}
              className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-accent ${
                o.value === value ? "bg-accent/60" : ""
              }`}
            >
              <span className="min-w-0 flex-1 truncate">{o.label}</span>
              {o.hint && <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{o.hint}</span>}
            </button>
          ))}
          {!results.length && <p className="px-2 py-6 text-center text-sm text-muted-foreground">Ничего не найдено</p>}
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** Выбор предмета из игрового каталога — с иконками, как в конструкторе
 *  кукол: опечатка в identifier тихо ломает награду при выдаче. */
export function ItemPicker({ catalog, onPick, children }: {
  catalog: ItemCatalog | undefined
  onPick: (identifier: string) => void
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")

  const results = useMemo(() => {
    const entries = Object.entries(catalog ?? {})
    const needle = query.trim().toLowerCase()
    const matched = needle
      ? entries.filter(([id, e]) => id.includes(needle) || e.name?.toLowerCase().includes(needle))
      : entries
    // Совпадение по началу названия — выше, как в InventoryTab кукол.
    return matched
      .sort(([, a], [, b]) => {
        const an = (a.name || "").toLowerCase()
        const bn = (b.name || "").toLowerCase()
        if (needle) {
          const ap = an.startsWith(needle) ? 0 : 1
          const bp = bn.startsWith(needle) ? 0 : 1
          if (ap !== bp) return ap - bp
        }
        return an.localeCompare(bn)
      })
      .slice(0, MAX_RESULTS)
  }, [catalog, query])

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setQuery("") }}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent className="w-80 p-0" align="start">
        <div className="flex items-center gap-2 border-b px-2 py-1.5">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск предмета"
            className="w-full bg-transparent text-sm outline-none"
          />
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {results.map(([id, entry]) => (
            <button
              key={id}
              type="button"
              onClick={() => { onPick(id); setOpen(false); setQuery("") }}
              className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-accent"
            >
              <ItemIcon identifier={id} size={24} />
              <span className="min-w-0 flex-1 truncate">{entry.name || id}</span>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{id}</span>
            </button>
          ))}
          {!results.length && <p className="px-2 py-6 text-center text-sm text-muted-foreground">Ничего не найдено</p>}
        </div>
      </PopoverContent>
    </Popover>
  )
}

/** Кнопка-предмет: показывает иконку и название выбранного, открывает
 *  каталог по клику. */
export function ItemField({ value, onChange, className }: {
  value: string
  onChange: (identifier: string) => void
  className?: string
}) {
  const { data: catalog } = useItemCatalog()
  return (
    <ItemPicker catalog={catalog} onPick={onChange}>
      <Button type="button" variant="outline" className={`h-9 justify-start gap-2 font-normal ${className ?? ""}`}>
        {value
          ? <><ItemIcon identifier={value} size={20} /><span className="truncate">{itemDisplayName(catalog, value)}</span></>
          : <span className="text-muted-foreground">Выберите предмет</span>}
      </Button>
    </ItemPicker>
  )
}

/** Список строк как чипы: серверы, requires, значения для op=in/not_in. */
export function ChipsField({ values, onChange, placeholder, options, mono }: {
  values: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  /** Если задано — добавление выбором из списка, иначе свободным вводом. */
  options?: Option[]
  mono?: boolean
}) {
  const [draft, setDraft] = useState("")

  const add = (v: string) => {
    const clean = v.trim()
    if (clean && !values.includes(clean)) onChange([...values, clean])
    setDraft("")
  }

  const available = (options ?? []).filter((o) => !values.includes(o.value))

  return (
    <div className="space-y-1.5">
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {values.map((v) => (
            <Badge key={v} variant="secondary" className={mono ? "font-mono" : ""}>
              {options?.find((o) => o.value === v)?.label ?? v}
              <button
                type="button"
                className="ml-1 text-muted-foreground hover:text-destructive"
                onClick={() => onChange(values.filter((x) => x !== v))}
              >
                <X className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      {options ? (
        <SearchPicker
          options={available}
          value=""
          onPick={add}
          placeholder={placeholder ?? "Добавить…"}
          className="w-full"
          disabled={!available.length}
        />
      ) : (
        <div className="flex gap-1.5">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(draft) } }}
            placeholder={placeholder}
            className={`h-9 ${mono ? "font-mono text-xs" : ""}`}
          />
          <Button type="button" variant="outline" size="sm" className="h-9" onClick={() => add(draft)} disabled={!draft.trim()}>
            Добавить
          </Button>
        </div>
      )}
    </div>
  )
}
