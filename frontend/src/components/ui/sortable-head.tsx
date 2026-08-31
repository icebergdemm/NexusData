import { TableHead } from "@/components/ui/table"
import { ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react"

export function SortableHead({
  active,
  direction,
  onClick,
  children,
  className,
}: {
  active: boolean
  direction: "asc" | "desc"
  onClick: () => void
  children: React.ReactNode
  className?: string
}) {
  return (
    <TableHead
      className={`cursor-pointer select-none hover:text-foreground ${className ?? ""}`}
      onClick={onClick}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {active ? (
          direction === "asc" ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />
        ) : (
          <ArrowUpDown className="size-3.5 opacity-30" />
        )}
      </span>
    </TableHead>
  )
}
