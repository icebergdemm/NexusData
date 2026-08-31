import * as React from "react"
import { ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react"

import { cn } from "@/lib/utils"

type SortDir = "asc" | "desc"
type SortCtx = { sortCol: number | null; sortDir: SortDir; toggle: (col: number) => void }

const SortContext = React.createContext<SortCtx | null>(null)

function Table({ className, ...props }: React.ComponentProps<"table">) {
  const [sortCol, setSortCol] = React.useState<number | null>(null)
  const [sortDir, setSortDir] = React.useState<SortDir>("asc")
  const toggle = React.useCallback((col: number) => {
    setSortCol((prev) => {
      if (prev === col) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"))
        return col
      }
      setSortDir("asc")
      return col
    })
  }, [])
  return (
    <SortContext.Provider value={{ sortCol, sortDir, toggle }}>
      <div data-slot="table-container" className="relative w-full overflow-x-auto">
        <table data-slot="table" className={cn("w-full caption-bottom text-sm", className)} {...props} />
      </div>
    </SortContext.Provider>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead data-slot="table-header" className={cn("[&_tr]:border-b", className)} {...props} />
}

function TableBody({ className, children, ...props }: React.ComponentProps<"tbody">) {
  const ctx = React.useContext(SortContext)
  const sortedChildren = React.useMemo(() => {
    if (!ctx || ctx.sortCol == null) return children
    const rows = React.Children.toArray(children).filter(
      (c): c is React.ReactElement => React.isValidElement(c)
    )
    // keep rows that are not data rows (e.g. empty state with colSpan) at bottom unsorted?
    const dataRows = rows.filter((r) => {
      const cells = React.Children.toArray((r.props as { children?: React.ReactNode }).children)
      const first = cells[0] as React.ReactElement<{ colSpan?: number }>
      return cells.length > 1 || first?.props?.colSpan == null
    })
    const emptyRows = rows.filter((r) => !dataRows.includes(r))
    const getCellText = (row: React.ReactElement, col: number): string => {
      const cells = React.Children.toArray((row.props as { children?: React.ReactNode }).children) as React.ReactElement[]
      const cell = cells[col]
      if (!cell) return ""
      const extract = (node: React.ReactNode): string => {
        if (node == null) return ""
        if (typeof node === "string" || typeof node === "number") return String(node)
        if (Array.isArray(node)) return node.map(extract).join(" ")
        if (React.isValidElement(node)) {
          const p = node.props as { children?: React.ReactNode }
          // if Badge etc, get its children
          if (p.children) return extract(p.children)
          return ""
        }
        return ""
      }
      return extract((cell.props as { children?: React.ReactNode }).children).trim()
    }
    const sorted = [...dataRows].sort((a, b) => {
      const av = getCellText(a, ctx.sortCol!)
      const bv = getCellText(b, ctx.sortCol!)
      if (!av && !bv) return 0
      if (!av) return 1
      if (!bv) return -1
      const an = parseFloat(av.replace(/\s/g, "").replace(",", "."))
      const bn = parseFloat(bv.replace(/\s/g, "").replace(",", "."))
      const aIsNum = !isNaN(an) && /^[\d\s.,\-+]+$/.test(av.replace(/\u00a0/g, ""))
      const bIsNum = !isNaN(bn) && /^[\d\s.,\-+]+$/.test(bv.replace(/\u00a0/g, ""))
      if (aIsNum && bIsNum) return ctx.sortDir === "asc" ? an - bn : bn - an
      const ad = Date.parse(av)
      const bd = Date.parse(bv)
      const aIsDate = !isNaN(ad) && /(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})/.test(av)
      const bIsDate = !isNaN(bd) && /(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})/.test(bv)
      if (aIsDate && bIsDate) return ctx.sortDir === "asc" ? ad - bd : bd - ad
      return ctx.sortDir === "asc"
        ? av.localeCompare(bv, "ru", { numeric: true, sensitivity: "base" })
        : bv.localeCompare(av, "ru", { numeric: true, sensitivity: "base" })
    })
    return [...sorted, ...emptyRows] as unknown as React.ReactNode
  }, [children, ctx?.sortCol, ctx?.sortDir])

  return (
    <tbody data-slot="table-body" className={cn("[&_tr:last-child]:border-0", className)} {...props}>
      {sortedChildren}
    </tbody>
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return <tfoot data-slot="table-footer" className={cn("border-t bg-muted/50 font-medium [&>tr]:last:border-b-0", className)} {...props} />
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return <tr data-slot="table-row" className={cn("border-b transition-colors hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted", className)} {...props} />
}

function TableHead({
  className,
  children,
  sortable,
  ...props
}: React.ComponentProps<"th"> & { sortable?: boolean }) {
  const ctx = React.useContext(SortContext)
  const ref = React.useRef<HTMLTableCellElement>(null)
  const dir = ctx?.sortDir ?? "asc"

  const handleClick = (e: React.MouseEvent<HTMLTableCellElement>) => {
    if (sortable === false) return
    // actions column (text-right) не сортируем
    if (className?.includes("text-right")) return
    const th = e.currentTarget as HTMLTableCellElement
    const idx = th.cellIndex
    ctx?.toggle(idx)
    // also call original onClick if any
    const orig = (props as { onClick?: (e: React.MouseEvent<HTMLTableCellElement>) => void }).onClick
    orig?.(e as unknown as React.MouseEvent<HTMLTableCellElement>)
  }

  // determine active via context sortCol vs this cell's index (need to read after mount)
  const [active, setActive] = React.useState(false)
  React.useEffect(() => {
    if (!ctx || !ref.current) return
    setActive(ctx.sortCol === ref.current.cellIndex)
  }, [ctx?.sortCol])

  return (
    <th
      ref={ref}
      data-slot="table-head"
      data-sortable={sortable === false ? "false" : "true"}
      className={cn(
        "h-10 px-2 text-left align-middle font-medium whitespace-nowrap text-foreground [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]",
        sortable !== false && !className?.includes("text-right") && "cursor-pointer select-none hover:text-foreground",
        className
      )}
      onClick={handleClick}
      {...props}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {sortable === false || className?.includes("text-right") ? null : active ? (
          dir === "asc" ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />
        ) : (
          <ArrowUpDown className="size-3.5 opacity-30" />
        )}
      </span>
    </th>
  )
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return <td data-slot="table-cell" className={cn("p-2 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]", className)} {...props} />
}

function TableCaption({ className, ...props }: React.ComponentProps<"caption">) {
  return <caption data-slot="table-caption" className={cn("mt-4 text-sm text-muted-foreground", className)} {...props} />
}

export { Table, TableHeader, TableBody, TableFooter, TableHead, TableRow, TableCell, TableCaption }
