import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"

export interface KvRow { key: string; value: string }

export function KvRowEditor({
  rows, onChange, keyPlaceholder, valuePlaceholder, addLabel,
}: {
  rows: KvRow[]
  onChange: (rows: KvRow[]) => void
  keyPlaceholder: string
  valuePlaceholder: string
  addLabel: string
}) {
  const update = (i: number, field: "key" | "value", v: string) => {
    const next = rows.slice()
    next[i] = { ...next[i], [field]: v }
    onChange(next)
  }
  const remove = (i: number) => onChange(rows.filter((_, idx) => idx !== i))
  const add = () => onChange([...rows, { key: "", value: "" }])

  return (
    <div className="space-y-1.5">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input className="flex-[2]" placeholder={keyPlaceholder} value={row.key} onChange={(e) => update(i, "key", e.target.value)} />
          <Input className="flex-1" placeholder={valuePlaceholder} value={row.value} onChange={(e) => update(i, "value", e.target.value)} />
          <Button type="button" size="sm" variant="ghost" className="shrink-0 text-destructive" onClick={() => remove(i)}>✕</Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" className="w-full border-dashed" onClick={add}>{addLabel}</Button>
    </div>
  )
}
