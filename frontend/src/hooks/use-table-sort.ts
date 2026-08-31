import { useMemo, useState } from "react"

export type SortDirection = "asc" | "desc"

export function useTableSort<T>(data: T[], defaultKey?: keyof T, defaultDir: SortDirection = "asc") {
  const [sortKey, setSortKey] = useState<keyof T | null>(defaultKey ?? null)
  const [direction, setDirection] = useState<SortDirection>(defaultDir)

  const toggle = (key: keyof T) => {
    if (sortKey === key) {
      setDirection((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setDirection("asc")
    }
  }

  const sorted = useMemo(() => {
    if (!sortKey) return data
    const copy = [...data]
    copy.sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      // nulls last
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      // numbers
      if (typeof av === "number" && typeof bv === "number") {
        return direction === "asc" ? av - bv : bv - av
      }
      // dates (ISO strings)
      const ad = Date.parse(String(av))
      const bd = Date.parse(String(bv))
      const aIsDate = !isNaN(ad) && String(av).match(/^\d{4}-\d{2}-\d{2}/)
      const bIsDate = !isNaN(bd) && String(bv).match(/^\d{4}-\d{2}-\d{2}/)
      if (aIsDate && bIsDate) {
        return direction === "asc" ? ad - bd : bd - ad
      }
      // strings
      const cmp = String(av).localeCompare(String(bv), "ru", { numeric: true, sensitivity: "base" })
      return direction === "asc" ? cmp : -cmp
    })
    return copy
  }, [data, sortKey, direction])

  return { sorted, sortKey, direction, toggle, setSortKey, setDirection }
}
