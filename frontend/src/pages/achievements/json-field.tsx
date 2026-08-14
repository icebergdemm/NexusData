import { useState } from "react"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"

/** JSON textarea with live validation feedback + a format button — дубликат
 * pages/kits/json-field.tsx (тот же генерик-компонент без специфики китов),
 * продублирован намеренно: страницы-плагины в этом проекте самодостаточны. */
export function JsonField({ value, onChange, rows = 6 }: { value: string; onChange: (v: string) => void; rows?: number }) {
  const [error, setError] = useState<string | null>(null)

  const handleChange = (v: string) => {
    onChange(v)
    try {
      JSON.parse(v)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const format = () => {
    try {
      onChange(JSON.stringify(JSON.parse(value), null, 2))
      setError(null)
    } catch {
      // оставляем как есть, если невалидно
    }
  }

  return (
    <div className="space-y-1">
      <Textarea value={value} onChange={(e) => handleChange(e.target.value)} rows={rows} className="font-mono text-xs" />
      <div className="flex items-center justify-between">
        <span className={`text-[11px] font-semibold ${error ? "text-destructive" : "text-success"}`}>
          {error ? `✗ Ошибка: ${error}` : "✓ JSON корректен"}
        </span>
        <Button type="button" size="sm" variant="ghost" className="h-6 text-xs" onClick={format}>🔧 Форматировать</Button>
      </div>
    </div>
  )
}
